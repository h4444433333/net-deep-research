from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from cache.redis_client import build_rate_limit_key, check_rate_limit
from utils.logger import get_logger

logger = get_logger("request_guard")

_RATE_LIMIT_SALT = os.environ.get("REQUEST_RATE_LIMIT_SALT", "net-info-request-guard-v1")


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RateLimitRule:
    scope: str
    max_requests: int
    window_seconds: int


_GLOBAL_RULE = RateLimitRule(
    scope="public_api",
    max_requests=max(10, _env_int("REQUEST_LIMIT_PUBLIC_API_MAX_REQUESTS", 240)),
    window_seconds=max(10, _env_int("REQUEST_LIMIT_PUBLIC_API_WINDOW_SECONDS", 60)),
)

_ENDPOINT_RULES: tuple[tuple[str, str, RateLimitRule], ...] = (
    (
        "POST",
        "/sources/check",
        RateLimitRule(
            scope="sources_check",
            max_requests=max(5, _env_int("REQUEST_LIMIT_SOURCES_CHECK_MAX_REQUESTS", 15)),
            window_seconds=max(10, _env_int("REQUEST_LIMIT_SOURCES_CHECK_WINDOW_SECONDS", 60)),
        ),
    ),
    (
        "POST",
        "/research-feedback",
        RateLimitRule(
            scope="research_feedback",
            max_requests=max(5, _env_int("REQUEST_LIMIT_RESEARCH_FEEDBACK_MAX_REQUESTS", 30)),
            window_seconds=max(10, _env_int("REQUEST_LIMIT_RESEARCH_FEEDBACK_WINDOW_SECONDS", 300)),
        ),
    ),
    (
        "POST",
        "/offnet-analysis",
        RateLimitRule(
            scope="offnet_analysis",
            max_requests=max(5, _env_int("REQUEST_LIMIT_OFFNET_ANALYSIS_MAX_REQUESTS", 30)),
            window_seconds=max(10, _env_int("REQUEST_LIMIT_OFFNET_ANALYSIS_WINDOW_SECONDS", 300)),
        ),
    ),
    (
        "GET",
        "/sources/search",
        RateLimitRule(
            scope="sources_search",
            max_requests=max(10, _env_int("REQUEST_LIMIT_SOURCES_SEARCH_MAX_REQUESTS", 120)),
            window_seconds=max(10, _env_int("REQUEST_LIMIT_SOURCES_SEARCH_WINDOW_SECONDS", 60)),
        ),
    ),
    (
        "GET",
        "/sources",
        RateLimitRule(
            scope="sources_lookup",
            max_requests=max(10, _env_int("REQUEST_LIMIT_SOURCES_LOOKUP_MAX_REQUESTS", 180)),
            window_seconds=max(10, _env_int("REQUEST_LIMIT_SOURCES_LOOKUP_WINDOW_SECONDS", 60)),
        ),
    ),
)


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _header_lookup(event: dict, name: str) -> str:
    headers = event.get("headers") or {}
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return str(value)
    return ""


def _client_ip(event: dict) -> str:
    for header_name in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        raw_value = _header_lookup(event, header_name)
        if raw_value:
            return raw_value.split(",", 1)[0].strip()
    return "unknown"


def compute_request_actor_hash(event: dict) -> str:
    ip = _client_ip(event)
    ua = _header_lookup(event, "user-agent") or "unknown"
    raw = f"{ip}|{ua}|{_RATE_LIMIT_SALT}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _matched_rules(path: str, method: str) -> list[RateLimitRule]:
    public_path = path.startswith("/v1/") and not path.startswith("/v1/internal/")
    rules: list[RateLimitRule] = []
    if public_path:
        rules.append(_GLOBAL_RULE)
    for rule_method, suffix, rule in _ENDPOINT_RULES:
        if method == rule_method and path.endswith(suffix):
            rules.append(rule)
    return rules


def enforce_request_limits(path: str, method: str, event: dict) -> dict | None:
    rules = _matched_rules(path, method)
    if not rules:
        return None

    actor_hash = compute_request_actor_hash(event)
    for rule in rules:
        try:
            allowed = check_rate_limit(
                build_rate_limit_key(rule.scope, actor_hash),
                max_requests=rule.max_requests,
                window_seconds=rule.window_seconds,
            )
        except Exception as exc:
            logger.exception(
                "request rate limit backend failed scope=%s path=%s method=%s",
                rule.scope,
                path,
                method,
            )
            return _json_response(
                503,
                {
                    "error": "request_rate_limit_unavailable",
                    "detail": str(exc),
                    "scope": rule.scope,
                },
            )

        if not allowed:
            return _json_response(
                429,
                {
                    "error": "rate limit exceeded, slow down",
                    "scope": rule.scope,
                    "window_seconds": rule.window_seconds,
                    "max_requests": rule.max_requests,
                },
            )
    return None
