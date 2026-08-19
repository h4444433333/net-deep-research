"""
Redis Streams 写队列消费者（research-feedback 写异步化）。

消费 `feedback_write_queue` 写入的 Stream，反序列化 FeedbackRequest 后调用
`handlers.feedback._derive_and_persist` 单事务落库；成功后 XACK，失败重试，超限进入 DLQ。

保证语义：
- at-least-once：手动 XACK（落库成功后才确认）+ Streams 持久化 + XAUTOCLAIM 回收 pending。
- 幂等：落库路径本身幂等（ON CONFLICT / DELETE+INSERT），乱序或重复消费不会产生脏数据。
- 死信：超过最大重试次数的消息 XADD 到 DLQ 后 XACK 原消息，避免无限重试阻塞队列。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

from redis.exceptions import ResponseError

from cache.redis_client import get_state_client
from models.source import FeedbackRequest
from pydantic import ValidationError
from services.feedback_write_queue import STREAM_KEY, feedback_write_async_enabled
from utils.logger import get_logger

logger = get_logger("feedback_write_worker")

_DLQ_STREAM_KEY = f"{STREAM_KEY}:dlq"
_GROUP_NAME = os.environ.get("FEEDBACK_WRITE_GROUP", "feedback-write-workers")
_CONSUMER_NAME = os.environ.get(
    "FEEDBACK_WRITE_CONSUMER",
    f"{socket.gethostname()}-{os.getpid()}",
)
# 必须小于 get_state_client() 的 socket_timeout（2s），否则阻塞读会被客户端提前超时打断。
_BLOCK_MS = int(os.environ.get("FEEDBACK_WRITE_BLOCK_MS", "1000"))
_BATCH_COUNT = int(os.environ.get("FEEDBACK_WRITE_BATCH_COUNT", "10"))
_MAX_RETRIES = int(os.environ.get("FEEDBACK_WRITE_MAX_RETRIES", "3"))
_RECLAIM_IDLE_MS = int(os.environ.get("FEEDBACK_WRITE_RECLAIM_IDLE_MS", "30000"))
_DLQ_MAXLEN = int(os.environ.get("FEEDBACK_WRITE_DLQ_MAXLEN", "10000"))
_RETRY_KEY_PREFIX = f"{STREAM_KEY}:retry"

_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()
_worker_lock = threading.Lock()


def _retry_key(message_id: str) -> str:
    return f"{_RETRY_KEY_PREFIX}:{message_id}"


def _ensure_group(client) -> None:
    """创建消费者组；已存在（BUSYGROUP）则忽略。

    首次创建从 `0` 开始消费，确保 worker 启动前已入队的消息不被跳过（避免数据丢失）。
    """
    try:
        client.xgroup_create(STREAM_KEY, _GROUP_NAME, id="0", mkstream=True)
        logger.info("feedback write consumer group created: %s", _GROUP_NAME)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    except Exception:
        logger.exception("failed to create feedback write consumer group: %s", _GROUP_NAME)
        raise


def _ack(client, message_id: str) -> None:
    try:
        client.xack(STREAM_KEY, _GROUP_NAME, message_id)
    except Exception:
        logger.exception("feedback write xack failed: %s", message_id)
    try:
        client.delete(_retry_key(message_id))
    except Exception:
        pass


def _dead_letter(client, message_id: str, fields: dict, *, reason: str) -> None:
    """将消息移入 DLQ 并从主队列确认，保留原始 payload 供人工核查。"""
    try:
        dlq_fields = dict(fields)
        dlq_fields["dlq_reason"] = reason or ""
        dlq_fields["original_message_id"] = message_id
        client.xadd(_DLQ_STREAM_KEY, dlq_fields, id="*", maxlen=_DLQ_MAXLEN, approximate=True)
    except Exception:
        logger.exception("failed to write DLQ for message: %s", message_id)
    _ack(client, message_id)


def _fail_message(client, message_id: str, fields: dict, *, reason: str) -> None:
    """持久化失败：累加重试计数；超限进入 DLQ，否则保留 pending 等待 XAUTOCLAIM 重投。"""
    try:
        retries = int(client.hincrby(_retry_key(message_id), "count", 1))
    except Exception:
        logger.exception("feedback write retry counter failed: %s", message_id)
        retries = _MAX_RETRIES

    if retries >= _MAX_RETRIES:
        logger.error(
            "feedback write exceeded max retries (%s), moving to DLQ: %s reason=%s",
            _MAX_RETRIES,
            message_id,
            reason,
        )
        _dead_letter(client, message_id, fields, reason=reason)
        return

    logger.warning(
        "feedback write persist failed (retry %s/%s): message_id=%s reason=%s",
        retries,
        _MAX_RETRIES,
        message_id,
        reason,
    )


def _handle_message(client, message_id: str, fields: dict) -> None:
    """处理单条消息：反序列化 -> 单事务落库 -> ack / 重试 / DLQ。"""
    if not isinstance(fields, dict):
        _dead_letter(client, message_id, {}, reason="malformed fields")
        return

    session_id = fields.get("session_id", "")
    payload_raw = fields.get("payload")
    if not payload_raw:
        logger.warning("feedback write message missing payload, skipping: %s", message_id)
        _ack(client, message_id)
        return

    try:
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError):
        logger.error("feedback write message has invalid JSON: %s", message_id)
        _dead_letter(client, message_id, fields, reason="invalid json")
        return

    try:
        req = FeedbackRequest(**payload)
    except ValidationError as exc:
        logger.error(
            "feedback write message failed validation: message_id=%s session=%s error=%s",
            message_id,
            session_id,
            exc,
        )
        _dead_letter(client, message_id, fields, reason=f"validation: {exc}")
        return

    from handlers.feedback import _derive_and_persist

    result = _derive_and_persist(req)
    if result.get("ok"):
        logger.info("feedback write persisted: message_id=%s session=%s", message_id, req.session_id)
        _ack(client, message_id)
        return

    _fail_message(client, message_id, fields, reason=result.get("error"))


def _consume_batch(client, *, do_reclaim: bool) -> int:
    """处理一批消息（可选先回收 pending），返回处理条数。"""
    processed = 0

    if do_reclaim:
        try:
            _, reclaimed, _ = client.xautoclaim(
                STREAM_KEY,
                _GROUP_NAME,
                _CONSUMER_NAME,
                min_idle_time=_RECLAIM_IDLE_MS,
                start_id="0-0",
                count=_BATCH_COUNT,
            )
        except Exception:
            logger.exception("feedback write xautoclaim failed")
            reclaimed = []
        for message_id, fields in reclaimed:
            processed += 1
            _handle_message(client, message_id, fields)

    try:
        stream_data = client.xreadgroup(
            _GROUP_NAME,
            _CONSUMER_NAME,
            {STREAM_KEY: ">"},
            count=_BATCH_COUNT,
            block=_BLOCK_MS,
        )
    except Exception:
        logger.exception("feedback write xreadgroup failed")
        stream_data = None

    if stream_data:
        for _stream, messages in stream_data:
            for message_id, fields in messages:
                processed += 1
                _handle_message(client, message_id, fields)

    return processed


def _worker_loop() -> None:
    client = get_state_client()
    group_ready = False
    next_reclaim_at = time.monotonic()

    while not _worker_stop.is_set():
        try:
            if not group_ready:
                _ensure_group(client)
                group_ready = True
                logger.info(
                    "feedback write worker started: stream=%s group=%s consumer=%s",
                    STREAM_KEY,
                    _GROUP_NAME,
                    _CONSUMER_NAME,
                )

            now = time.monotonic()
            do_reclaim = now >= next_reclaim_at
            _consume_batch(client, do_reclaim=do_reclaim)
            if do_reclaim:
                next_reclaim_at = now + (_RECLAIM_IDLE_MS / 1000.0)
        except Exception:
            logger.exception("feedback write worker loop error")
            _worker_stop.wait(1)

    logger.info("feedback write worker stopped")


def start_feedback_write_worker() -> bool:
    """启动写队列消费线程。仅在 FEEDBACK_WRITE_ASYNC 开启时生效。"""
    global _worker_thread

    if not feedback_write_async_enabled():
        return False

    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return True
        _worker_stop.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="net-info-feedback-write-worker",
            daemon=True,
        )
        _worker_thread.start()
        return True


def stop_feedback_write_worker() -> None:
    _worker_stop.set()
