from __future__ import annotations

import json
import os
import threading
import time

from utils.logger import get_logger

_logger = get_logger("health_probe")
_SUMMARY_INTERVAL_SECONDS = max(
    60,
    int(os.environ.get("NET_INFO_HEALTH_LOG_INTERVAL_SECONDS", "1800")),
)

_lock = threading.Lock()
_state = {
    "window_started_at": time.time(),
    "last_logged_at": 0.0,
    "count": 0,
    "internal_count": 0,
    "external_count": 0,
}


def _is_internal_ip(client_ip: str | None) -> bool:
    if not client_ip:
        return True
    if client_ip in {"127.0.0.1", "::1", "localhost", "unknown"}:
        return True
    return client_ip.startswith("10.") or client_ip.startswith("192.168.") or client_ip.startswith("172.")


def record_health_probe(client_ip: str | None) -> None:
    now = time.time()
    should_log = False
    payload = None
    with _lock:
        _state["count"] += 1
        if _is_internal_ip(client_ip):
            _state["internal_count"] += 1
        else:
            _state["external_count"] += 1

        if now - _state["last_logged_at"] >= _SUMMARY_INTERVAL_SECONDS:
            should_log = True
            payload = {
                "window_started_at": int(_state["window_started_at"]),
                "window_seconds": int(now - _state["window_started_at"]),
                "probe_count": _state["count"],
                "internal_probe_count": _state["internal_count"],
                "external_probe_count": _state["external_count"],
            }
            _state["window_started_at"] = now
            _state["last_logged_at"] = now
            _state["count"] = 0
            _state["internal_count"] = 0
            _state["external_count"] = 0

    if should_log and payload is not None:
        _logger.info("health probe summary: %s", json.dumps(payload, ensure_ascii=False))
