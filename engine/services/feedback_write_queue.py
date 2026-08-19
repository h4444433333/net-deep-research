"""
Redis Streams 写队列生产者（research-feedback 写异步化）。

职责：
1. `feedback_write_async_enabled()` 读取 FEEDBACK_WRITE_ASYNC 开关。
2. `enqueue_feedback_write(request_payload)` 将已验证请求 JSON 写入 Stream。
3. 幂等：用 Lua 脚本原子执行「SETNX 去重 + XADD 入队」，重复请求在窗口内被跳过，
   且 SET/XADD 同生共死，避免“去重标记已写但消息未入队”造成的数据丢失。

Streams 为 append-only 日志，用 MAXLEN ~ 近似截断实现约 1 天的 retention。
写队列属于关键状态，复用 state redis；Redis 故障时向上抛错，由调用方降级为同步落库。
"""

from __future__ import annotations

import hashlib
import json
import os

from cache.redis_client import get_state_client
from utils.logger import get_logger

logger = get_logger("feedback_write_queue")

_NAMESPACE = os.environ.get("REDIS_NAMESPACE", "netinfo")
STREAM_KEY = f"{_NAMESPACE}:state:feedback:write"
_DEDUP_PREFIX = f"{_NAMESPACE}:state:feedback:write:dedup"
_STREAM_MAXLEN = int(os.environ.get("FEEDBACK_WRITE_STREAM_MAXLEN", "100000"))
_DEDUP_TTL_SECONDS = int(os.environ.get("FEEDBACK_WRITE_DEDUP_TTL_SECONDS", "86400"))

# 原子去重 + 入队：KEYS[1]=dedup_key, KEYS[2]=stream_key
# ARGV[1]=dedup_ttl, ARGV[2]=maxlen, ARGV[3]=dedup_id, ARGV[4]=session_id, ARGV[5]=payload_json
_ENQUEUE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return {0, ''}
end
redis.call('SET', KEYS[1], '1', 'EX', tonumber(ARGV[1]))
local id = redis.call('XADD', KEYS[2], 'MAXLEN', '~', tonumber(ARGV[2]), '*',
    'dedup_id', ARGV[3], 'session_id', ARGV[4], 'payload', ARGV[5])
return {1, id}
"""


def feedback_write_async_enabled() -> bool:
    raw = os.environ.get("FEEDBACK_WRITE_ASYNC", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _stable_dedup_id(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def enqueue_feedback_write(request_payload: dict, *, session_id: str | None = None) -> str | None:
    """原子地将请求写入 Stream。返回消息 ID；重复请求返回 None（跳过）。

    Redis 故障时直接抛错，由调用方决定是否降级为同步落库。
    """
    dedup_id = _stable_dedup_id(request_payload)
    dedup_key = f"{_DEDUP_PREFIX}:{dedup_id}"
    payload_json = json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"))

    client = get_state_client()
    inserted, message_id = client.eval(
        _ENQUEUE_SCRIPT,
        2,
        dedup_key,
        STREAM_KEY,
        _DEDUP_TTL_SECONDS,
        _STREAM_MAXLEN,
        dedup_id,
        session_id or "",
        payload_json,
    )
    if int(inserted) == 0:
        logger.info("feedback write deduplicated: session=%s dedup_id=%s", session_id, dedup_id)
        return None
    logger.info(
        "feedback write enqueued: stream=%s message_id=%s session=%s",
        STREAM_KEY,
        message_id,
        session_id,
    )
    return message_id
