"""
运行时请求指标聚合。

目标：
1. 在不依赖数据库的情况下，为访问日志提供每日 skill 调用总量
2. 同时记录当天小时级峰值，便于快速看调用高峰
3. 增补轻量并发/吞吐/延迟快照，便于估算最大吞吐与扩容阈值
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
import threading
import time

from cache.redis_client import get_state_client
from utils.logger import get_logger


_BEIJING_TZ = timezone(timedelta(hours=8))
_WINDOW_5M_MS = 5 * 60 * 1000
_WINDOW_1M_MS = 60 * 1000
_RUNTIME_NAMESPACE = os.environ.get("REDIS_NAMESPACE", "netinfo").strip() or "netinfo"
_RUNTIME_PREFIX = f"{_RUNTIME_NAMESPACE}:state:runtime"
_ACTIVE_REQUEST_TTL_MS = max(60_000, int(os.environ.get("RUNTIME_ACTIVE_REQUEST_TTL_MS", str(30 * 60 * 1000))))

logger = get_logger("runtime_metrics")


@dataclass
class _DailyMetrics:
    date_key: str
    skill_call_total: int = 0
    hourly_counts: dict[str, int] = field(default_factory=dict)
    peak_hour_bucket: str | None = None
    peak_hour_total: int = 0
    inflight_requests: int = 0
    peak_inflight_requests: int = 0
    completion_times: deque[datetime] = field(default_factory=deque)
    server_error_times: deque[datetime] = field(default_factory=deque)
    latency_samples: deque[tuple[datetime, int]] = field(default_factory=deque)


_lock = threading.Lock()
_state: _DailyMetrics | None = None
_redis_metrics_warning_logged = False


def _beijing_now() -> datetime:
    return datetime.now(_BEIJING_TZ)


def _bucket_for(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:00")


def _snapshot(metrics: _DailyMetrics, *, now: datetime) -> dict:
    requests_last_minute = len(
        [ts for ts in metrics.completion_times if ts >= now - timedelta(minutes=1)]
    )
    requests_last_5m = len(metrics.completion_times)
    server_errors_last_5m = len(metrics.server_error_times)
    latency_values = [value for _, value in metrics.latency_samples]

    return {
        "date": metrics.date_key,
        "daily_skill_call_total": metrics.skill_call_total,
        "daily_peak_hour_calls": metrics.peak_hour_total,
        "daily_peak_hour_bucket": metrics.peak_hour_bucket,
        "inflight_requests": metrics.inflight_requests,
        "peak_inflight_requests": metrics.peak_inflight_requests,
        "requests_last_minute": requests_last_minute,
        "avg_rps_last_minute": round(requests_last_minute / 60, 3),
        "requests_last_5m": requests_last_5m,
        "avg_rps_last_5m": round(requests_last_5m / 300, 3),
        "server_errors_last_5m": server_errors_last_5m,
        "server_error_rate_last_5m": (
            round(server_errors_last_5m / requests_last_5m, 4) if requests_last_5m else 0.0
        ),
        "latency_p50_ms_last_5m": _percentile(latency_values, 50),
        "latency_p95_ms_last_5m": _percentile(latency_values, 95),
    }


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, round((percentile / 100) * (len(sorted_values) - 1))))
    return sorted_values[index]


def _daily_key(date_key: str) -> str:
    return f"{_RUNTIME_PREFIX}:daily:{date_key}"


def _hourly_key(date_key: str) -> str:
    return f"{_RUNTIME_PREFIX}:daily:{date_key}:hourly"


def _rollover_key(date_key: str) -> str:
    return f"{_RUNTIME_PREFIX}:rollover:{date_key}"


def _active_key() -> str:
    return f"{_RUNTIME_PREFIX}:active"


def _completion_key() -> str:
    return f"{_RUNTIME_PREFIX}:completion"


def _server_error_key() -> str:
    return f"{_RUNTIME_PREFIX}:server_error"


def _latency_key() -> str:
    return f"{_RUNTIME_PREFIX}:latency"


def _request_token(now: datetime) -> str:
    return f"{time.time_ns()}:{os.getpid()}:{threading.get_ident()}:{int(now.timestamp() * 1000)}"


def _event_member(prefix: str, now: datetime, value: int | None = None) -> str:
    value_part = "" if value is None else f":{value}"
    return f"{prefix}:{time.time_ns()}:{os.getpid()}:{threading.get_ident()}:{int(now.timestamp() * 1000)}{value_part}"


def _safe_int(value: str | None, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _log_redis_metrics_warning(exc: Exception) -> None:
    global _redis_metrics_warning_logged
    if _redis_metrics_warning_logged:
        return
    _redis_metrics_warning_logged = True
    logger.exception("runtime metrics redis aggregation unavailable, falling back to local metrics: %s", exc)


def _redis_rollover_summary(current_date_key: str) -> dict | None:
    previous_date = (
        datetime.strptime(current_date_key, "%Y-%m-%d").replace(tzinfo=_BEIJING_TZ) - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    daily_data = get_state_client().hgetall(_daily_key(previous_date))
    if not daily_data:
        return None
    if not get_state_client().set(_rollover_key(previous_date), "1", nx=True, ex=3 * 24 * 3600):
        return None
    return {
        "date": previous_date,
        "daily_skill_call_total": _safe_int(daily_data.get("skill_call_total")),
        "daily_peak_hour_calls": _safe_int(daily_data.get("peak_hour_total")),
        "daily_peak_hour_bucket": daily_data.get("peak_hour_bucket") or None,
        "inflight_requests": 0,
        "peak_inflight_requests": _safe_int(daily_data.get("peak_inflight_requests")),
        "requests_last_minute": 0,
        "avg_rps_last_minute": 0.0,
        "requests_last_5m": 0,
        "avg_rps_last_5m": 0.0,
        "server_errors_last_5m": 0,
        "server_error_rate_last_5m": 0.0,
        "latency_p50_ms_last_5m": None,
        "latency_p95_ms_last_5m": None,
    }


def _begin_request_redis(*, now: datetime) -> str:
    client = get_state_client()
    date_key = now.strftime("%Y-%m-%d")
    now_ms = int(now.timestamp() * 1000)
    token = _request_token(now)
    daily_key = _daily_key(date_key)

    pipe = client.pipeline()
    pipe.zremrangebyscore(_active_key(), "-inf", now_ms)
    pipe.zadd(_active_key(), {token: now_ms + _ACTIVE_REQUEST_TTL_MS})
    pipe.zcard(_active_key())
    pipe.expire(_active_key(), max(60, _ACTIVE_REQUEST_TTL_MS // 1000))
    pipe.hget(daily_key, "peak_inflight_requests")
    pipe.expire(daily_key, 3 * 24 * 3600)
    results = pipe.execute()

    inflight_requests = int(results[2] or 0)
    peak_inflight_requests = _safe_int(results[4], 0)
    if inflight_requests > peak_inflight_requests:
        client.hset(daily_key, "peak_inflight_requests", inflight_requests)
    return token


def _finish_request_redis(
    *,
    request_token: str | None,
    status_code: int,
    elapsed_ms: int,
    now: datetime,
) -> tuple[dict, dict | None]:
    client = get_state_client()
    date_key = now.strftime("%Y-%m-%d")
    hour_bucket = _bucket_for(now)
    now_ms = int(now.timestamp() * 1000)
    cutoff_1m = now_ms - _WINDOW_1M_MS
    cutoff_5m = now_ms - _WINDOW_5M_MS

    completion_member = _event_member("done", now)
    latency_member = _event_member("latency", now, elapsed_ms)
    error_member = _event_member("server_error", now) if status_code >= 500 else None

    daily_key = _daily_key(date_key)
    hourly_key = _hourly_key(date_key)

    if request_token is None:
        client.zremrangebyscore(_active_key(), "-inf", now_ms)
        try:
            client.zpopmin(_active_key(), 1)
        except TypeError:
            client.zpopmin(_active_key())

    pipe = client.pipeline()
    pipe.zremrangebyscore(_active_key(), "-inf", now_ms)
    if request_token:
        pipe.zrem(_active_key(), request_token)
    else:
        pipe.ping()
    pipe.zcard(_active_key())

    pipe.zadd(_completion_key(), {completion_member: now_ms})
    pipe.zremrangebyscore(_completion_key(), "-inf", cutoff_5m)
    pipe.zcount(_completion_key(), cutoff_1m, "+inf")
    pipe.zcount(_completion_key(), cutoff_5m, "+inf")
    pipe.expire(_completion_key(), 24 * 3600)

    if error_member:
        pipe.zadd(_server_error_key(), {error_member: now_ms})
    else:
        pipe.ping()
    pipe.zremrangebyscore(_server_error_key(), "-inf", cutoff_5m)
    pipe.zcount(_server_error_key(), cutoff_5m, "+inf")
    pipe.expire(_server_error_key(), 24 * 3600)

    pipe.zadd(_latency_key(), {latency_member: now_ms})
    pipe.zremrangebyscore(_latency_key(), "-inf", cutoff_5m)
    pipe.zrangebyscore(_latency_key(), cutoff_5m, "+inf")
    pipe.expire(_latency_key(), 24 * 3600)

    pipe.hincrby(daily_key, "skill_call_total", 1)
    pipe.hincrby(hourly_key, hour_bucket, 1)
    pipe.hget(daily_key, "peak_hour_total")
    pipe.hget(daily_key, "peak_hour_bucket")
    pipe.hget(daily_key, "peak_inflight_requests")
    pipe.expire(daily_key, 3 * 24 * 3600)
    pipe.expire(hourly_key, 3 * 24 * 3600)

    results = pipe.execute()

    inflight_requests = int(results[2] or 0)
    requests_last_minute = int(results[5] or 0)
    requests_last_5m = int(results[6] or 0)
    server_errors_last_5m = int(results[10] or 0)
    latency_members = results[14] or []
    daily_skill_call_total = int(results[16] or 0)
    hour_total = int(results[17] or 0)
    peak_hour_total = _safe_int(results[18], 0)
    peak_hour_bucket = results[19] or None
    peak_inflight_requests = _safe_int(results[20], 0)

    if hour_total >= peak_hour_total:
        client.hset(daily_key, mapping={"peak_hour_total": hour_total, "peak_hour_bucket": hour_bucket})
        peak_hour_total = hour_total
        peak_hour_bucket = hour_bucket

    latency_values: list[int] = []
    for member in latency_members:
        try:
            latency_values.append(int(str(member).rsplit(":", 1)[-1]))
        except (TypeError, ValueError):
            continue

    snapshot = {
        "date": date_key,
        "daily_skill_call_total": daily_skill_call_total,
        "daily_peak_hour_calls": peak_hour_total,
        "daily_peak_hour_bucket": peak_hour_bucket,
        "inflight_requests": inflight_requests,
        "peak_inflight_requests": peak_inflight_requests,
        "requests_last_minute": requests_last_minute,
        "avg_rps_last_minute": round(requests_last_minute / 60, 3),
        "requests_last_5m": requests_last_5m,
        "avg_rps_last_5m": round(requests_last_5m / 300, 3),
        "server_errors_last_5m": server_errors_last_5m,
        "server_error_rate_last_5m": (
            round(server_errors_last_5m / requests_last_5m, 4) if requests_last_5m else 0.0
        ),
        "latency_p50_ms_last_5m": _percentile(latency_values, 50),
        "latency_p95_ms_last_5m": _percentile(latency_values, 95),
    }
    return snapshot, _redis_rollover_summary(date_key)


def _purge_old_samples(metrics: _DailyMetrics, *, now: datetime) -> None:
    five_minute_cutoff = now - timedelta(minutes=5)

    while metrics.completion_times and metrics.completion_times[0] < five_minute_cutoff:
        metrics.completion_times.popleft()
    while metrics.server_error_times and metrics.server_error_times[0] < five_minute_cutoff:
        metrics.server_error_times.popleft()
    while metrics.latency_samples and metrics.latency_samples[0][0] < five_minute_cutoff:
        metrics.latency_samples.popleft()


def _begin_request_local(*, now: datetime) -> None:
    request_time = now
    date_key = request_time.strftime("%Y-%m-%d")

    with _lock:
        global _state
        if _state is None:
            _state = _DailyMetrics(date_key=date_key)
        elif _state.date_key != date_key:
            _state = _DailyMetrics(date_key=date_key)

        _state.inflight_requests += 1
        if _state.inflight_requests > _state.peak_inflight_requests:
            _state.peak_inflight_requests = _state.inflight_requests


def begin_request(*, now: datetime | None = None) -> str | None:
    request_time = now.astimezone(_BEIJING_TZ) if now else _beijing_now()
    try:
        return _begin_request_redis(now=request_time)
    except Exception as exc:
        _log_redis_metrics_warning(exc)
        _begin_request_local(now=request_time)
        return None


def _finish_request_local(*, status_code: int, elapsed_ms: int, now: datetime) -> tuple[dict, dict | None]:
    """
    记录一次已完成请求并返回当前快照。

    Returns:
        (current_snapshot, previous_day_rollover_summary_or_none)
    """
    request_time = now
    date_key = request_time.strftime("%Y-%m-%d")
    hour_bucket = _bucket_for(request_time)

    with _lock:
        global _state
        rollover_summary = None

        if _state is None:
            _state = _DailyMetrics(date_key=date_key)
        elif _state.date_key != date_key:
            _purge_old_samples(_state, now=request_time)
            rollover_summary = _snapshot(_state, now=request_time)
            _state = _DailyMetrics(date_key=date_key)

        if _state.inflight_requests > 0:
            _state.inflight_requests -= 1
        _state.skill_call_total += 1
        hour_total = _state.hourly_counts.get(hour_bucket, 0) + 1
        _state.hourly_counts[hour_bucket] = hour_total
        if hour_total >= _state.peak_hour_total:
            _state.peak_hour_total = hour_total
            _state.peak_hour_bucket = hour_bucket
        _state.completion_times.append(request_time)
        if status_code >= 500:
            _state.server_error_times.append(request_time)
        _state.latency_samples.append((request_time, elapsed_ms))
        _purge_old_samples(_state, now=request_time)

        return _snapshot(_state, now=request_time), rollover_summary


def finish_request(
    *,
    status_code: int,
    elapsed_ms: int,
    request_token: str | None = None,
    now: datetime | None = None,
) -> tuple[dict, dict | None]:
    request_time = now.astimezone(_BEIJING_TZ) if now else _beijing_now()
    try:
        return _finish_request_redis(
            request_token=request_token,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
            now=request_time,
        )
    except Exception as exc:
        _log_redis_metrics_warning(exc)
        return _finish_request_local(status_code=status_code, elapsed_ms=elapsed_ms, now=request_time)


def record_request(*, now: datetime | None = None) -> tuple[dict, dict | None]:
    """
    兼容旧调用方：将一次请求视为已完成请求处理。
    """
    return finish_request(status_code=200, elapsed_ms=0, now=now)


def reset_metrics() -> None:
    """仅供测试使用。"""
    global _state, _redis_metrics_warning_logged
    with _lock:
        _state = None
        _redis_metrics_warning_logged = False
    try:
        client = get_state_client()
        keys = list(client.scan_iter(match=f"{_RUNTIME_PREFIX}:*"))
        if keys:
            client.delete(*keys)
    except Exception:
        pass
