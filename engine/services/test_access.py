from __future__ import annotations

import hmac
import json
import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def is_test_runtime() -> bool:
    return _env("NET_INFO_RUNTIME_ENV", "prod").lower() == "test"


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
            return str(value).strip()
    return ""


def _extract_bearer_token(value: str) -> str:
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _provided_token(event: dict) -> str:
    explicit_token = _header_lookup(event, "x-net-info-test-token")
    if explicit_token:
        return explicit_token
    return _extract_bearer_token(_header_lookup(event, "authorization"))


def _allowed_clients() -> set[str]:
    raw = _env("TEST_API_ALLOWED_CLIENTS")
    return {item.strip() for item in raw.split(",") if item.strip()}


def enforce_test_access(path: str, method: str, event: dict) -> dict | None:
    if not is_test_runtime():
        return None

    expected_token = _env("TEST_API_AUTH_TOKEN")
    allowed_clients = _allowed_clients()
    if not expected_token or not allowed_clients:
        missing: list[str] = []
        if not expected_token:
            missing.append("TEST_API_AUTH_TOKEN")
        if not allowed_clients:
            missing.append("TEST_API_ALLOWED_CLIENTS")
        return _json_response(
            503,
            {
                "error": "test_access_not_configured",
                "detail": "test runtime requires explicit access control configuration",
                "missing": missing,
                "path": path,
                "method": method,
            },
        )

    provided_token = _provided_token(event)
    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        return _json_response(
            401,
            {
                "error": "test_access_unauthorized",
                "detail": "provide Authorization: Bearer <token> or X-Net-Info-Test-Token",
            },
        )

    client_id = _header_lookup(event, "x-net-info-test-client")
    if not client_id:
        return _json_response(
            403,
            {
                "error": "test_access_client_required",
                "detail": "provide X-Net-Info-Test-Client",
            },
        )

    if client_id not in allowed_clients:
        return _json_response(
            403,
            {
                "error": "test_access_client_forbidden",
                "detail": "client is not allowed for test backend",
                "client": client_id,
            },
        )

    return None
