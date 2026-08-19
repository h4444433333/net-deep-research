from __future__ import annotations

import ipaddress
import json
import uuid
from contextvars import ContextVar

from services.request_guard import compute_request_actor_hash
from utils.logger import get_logger, get_user_logger

_trace_context: ContextVar[dict | None] = ContextVar("request_trace_context", default=None)
_api_trace_logger = get_logger("request_trace")
_user_logger = get_user_logger("user_trace")
_MAX_STRING = 1200
_MAX_ITEMS = 10
_QUERY_LIMIT = 1000
_PREVIEW_LIMIT = 400
_INFRA_NODE_PREFIXES = (
    "db.",
    "scheduler",
    "security",
    "cache.",
    "redis.",
    "rate_limit",
    "metrics.",
    "health",
)
_USER_KEY_NODE_PREFIXES = (
    "route.dispatch",
    "request_guard",
    "sources.lookup",
    "feedback.",
    "offnet.",
    "vote.",
    "articles.search",
)


def _trim_string(value: str, limit: int = _MAX_STRING) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated,{len(value) - limit} chars omitted)"


def _sanitize(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _trim_string(value)
    if isinstance(value, list):
        items = [_sanitize(item) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"...({len(value) - _MAX_ITEMS} more items)")
        return items
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                sanitized["__truncated__"] = f"{len(value) - _MAX_ITEMS} more keys"
                break
            sanitized[str(key)] = _sanitize(item)
        return sanitized
    return _trim_string(str(value))


def _safe_json_loads(raw: str):
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return _trim_string(raw)


def _header_value(headers: dict | None, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if isinstance(key, str) and key.lower() == name.lower():
            return _trim_string(str(value), limit=256)
    return None


def _client_ip(event: dict) -> str:
    headers = event.get("headers") or {}
    for header_name in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == header_name:
                return str(value).split(",", 1)[0].strip()
    return "unknown"


def _is_internal_ip(client_ip: str) -> bool:
    if client_ip in {"unknown", "localhost"}:
        return True
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _traffic_class(path: str, client_ip: str) -> str:
    if path == "/health":
        return "health_probe"
    if path.startswith("/v1/internal/"):
        return "internal_api"
    if path.startswith("/v1/"):
        return "internal_public_api" if _is_internal_ip(client_ip) else "external_public_api"
    return "other"


def _extract_session_fields(body) -> dict:
    if not isinstance(body, dict):
        return {}
    result = {}
    if "session_id" in body:
        result["session_id"] = _sanitize(body.get("session_id"))
    if "query" in body:
        result["query"] = _trim_string(str(body.get("query") or ""), limit=_QUERY_LIMIT)
    if "final_answer" in body:
        result["final_answer_preview"] = _trim_string(str(body.get("final_answer") or ""), limit=_PREVIEW_LIMIT)
    if "answer_text" in body:
        result["answer_text_preview"] = _trim_string(str(body.get("answer_text") or ""), limit=_PREVIEW_LIMIT)
    return result


def _summarize_body(body):
    if body is None:
        return None
    if isinstance(body, dict):
        keys = list(body.keys())
        summary: dict[str, object] = {
            "type": "object",
            "keys": keys[:_MAX_ITEMS],
            "key_count": len(keys),
        }
        count_fields = {}
        for key, value in body.items():
            if isinstance(value, list):
                count_fields[f"{key}_count"] = len(value)
        if count_fields:
            summary["counts"] = count_fields
        return summary
    if isinstance(body, list):
        return {"type": "array", "item_count": len(body)}
    if isinstance(body, str):
        return {"type": "string", "preview": _trim_string(body, limit=_PREVIEW_LIMIT)}
    return {"type": type(body).__name__, "preview": _sanitize(body)}


def _response_summary(body) -> dict | None:
    return _summarize_body(body)


def _base_payload() -> dict:
    return dict(_trace_context.get() or {})


def _is_infra_node(node: str | None) -> bool:
    if not node:
        return False
    return any(node.startswith(prefix) for prefix in _INFRA_NODE_PREFIXES)


def _is_user_key_node(node: str | None) -> bool:
    if not node:
        return False
    return any(node.startswith(prefix) for prefix in _USER_KEY_NODE_PREFIXES)


def _route_target(payload: dict, event_name: str, node: str | None) -> str:
    traffic_class = payload.get("traffic_class")
    if traffic_class in {"health_probe", "internal_api", "internal_public_api"}:
        return "api"

    if event_name in {"request.start", "request.finish", "request.exception", "external_call.finish"}:
        return "user" if traffic_class == "external_public_api" else "api"

    if event_name == "request.node":
        if _is_infra_node(node):
            return "api"
        if traffic_class == "external_public_api" and _is_user_key_node(node):
            return "user"
        return "api"

    return "api"


def begin_request_trace(path: str, method: str, event: dict) -> str:
    body = _safe_json_loads(event.get("body", ""))
    trace = {
        "trace_id": uuid.uuid4().hex[:12],
        "path": path,
        "method": method,
        "client_ip": _client_ip(event),
        "actor_hash": compute_request_actor_hash(event),
        "query_parameters": _sanitize(event.get("queryParameters") or {}),
        "user_agent": _header_value(event.get("headers"), "user-agent"),
        "request_body_summary": _summarize_body(body),
    }
    trace["traffic_class"] = _traffic_class(path, trace["client_ip"])
    trace["is_external_user_call"] = trace["traffic_class"] == "external_public_api"
    trace.update(_extract_session_fields(body))
    _trace_context.set(trace)
    _write("request.start", message="incoming request")
    return trace["trace_id"]


def bind_trace_fields(**fields) -> None:
    trace = _trace_context.get()
    if not trace:
        return
    for key, value in fields.items():
        if value is not None:
            if key == "query":
                trace[key] = _trim_string(str(value), limit=_QUERY_LIMIT)
            elif key in {"final_answer", "answer_text"}:
                trace[f"{key}_preview"] = _trim_string(str(value), limit=_PREVIEW_LIMIT)
            else:
                trace[key] = _sanitize(value)
    _trace_context.set(trace)


def log_trace_node(node: str, message: str, *, data: dict | None = None, level: str = "info") -> None:
    _write("request.node", node=node, message=message, data=_sanitize(data or {}), level=level)


def log_trace_response(status_code: int, body) -> None:
    _write(
        "request.finish",
        message="request finished",
        status_code=status_code,
        response_summary=_response_summary(body),
    )


def log_trace_exception(node: str, exc: Exception, *, message: str, data: dict | None = None) -> None:
    payload = _base_payload()
    payload.update(
        {
            "event": "request.exception",
            "node": node,
            "message": message,
            "data": _sanitize(data or {}),
            "error_type": exc.__class__.__name__,
            "error_message": _trim_string(str(exc) or exc.__class__.__name__),
        }
    )
    target_logger = _user_logger if _route_target(payload, "request.exception", node) == "user" else _api_trace_logger
    target_logger.exception(json.dumps(payload, ensure_ascii=False))


def clear_request_trace() -> None:
    _trace_context.set(None)


def get_request_trace_payload() -> dict:
    return _base_payload()


def log_external_user_call(status_code: int, elapsed_ms: int) -> None:
    payload = _base_payload()
    if not payload or not payload.get("is_external_user_call"):
        return
    _write(
        "external_call.finish",
        message="external user call finished",
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        query_parameters=payload.get("query_parameters"),
        request_body_summary=payload.get("request_body_summary"),
        session_id=payload.get("session_id"),
        user_agent=payload.get("user_agent"),
    )


def _write(event_name: str, *, node: str | None = None, message: str, data=None, level: str = "info", **extra) -> None:
    payload = _base_payload()
    payload.update(
        {
            "event": event_name,
            "message": message,
        }
    )
    if node:
        payload["node"] = node
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    target = _route_target(payload, event_name, node)
    target_logger = _user_logger if target == "user" else _api_trace_logger
    log_fn = getattr(target_logger, level, target_logger.info)
    log_fn(json.dumps(payload, ensure_ascii=False))
