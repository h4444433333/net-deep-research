"""
POST /v1/sources/vote

投票接口：接收 trust/untrust 投票，先写 Redis 计数器，异步批量刷 DB。
"""

from __future__ import annotations

import json
import hashlib
import os
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from db.connection import get_connection
from cache.redis_client import build_rate_limit_key, incr_vote_pending, check_rate_limit
from models.source import VoteRequest
from services.reputation import flush_pending_vote_aggregates
from utils.logger import get_logger

_VOTE_SALT = os.environ.get("VOTE_SALT", "net-deep-research-reputation-v1")
_VOTE_FLUSH_THRESHOLD = int(os.environ.get("VOTE_FLUSH_THRESHOLD", "10"))
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
logger = get_logger("vote")


def _compute_voter_hash(event: dict) -> str:
    ip = event.get("headers", {}).get("x-forwarded-for", "unknown")
    ua = event.get("headers", {}).get("user-agent", "")
    raw = f"{ip}|{ua}|{_VOTE_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalize_domain(domain: str) -> str:
    cleaned = domain.strip().lower()
    if cleaned.startswith("www."):
        return cleaned[4:]
    return cleaned


def _normalize_target_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    scheme = (parts.scheme or "https").lower()
    host = (parts.netloc or "").lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]
    if not host:
        raise ValueError(f"invalid target_url: {raw_url}")

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    query = "&".join(
        f"{key}={value}" if value else key
        for key, value in kept_query
    )
    normalized = urlunsplit((scheme, host, path, query, ""))
    return normalized[:2048]


def _extract_target_domain(target_url: str) -> str:
    parsed = urlsplit(target_url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError(f"invalid target_url: {target_url}")
    return _normalize_domain(host)


def _source_id_by_domain(domain: str) -> int | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM sources WHERE domain = %s", (domain,))
            row = cur.fetchone()
    return row[0] if row else None


def _check_duplicate(source_id: int, voter_hash: str, target_url: str | None) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM votes
                WHERE source_id = %s
                  AND voter_hash = %s
                  AND target_url IS NOT DISTINCT FROM %s
                LIMIT 1
                """,
                (source_id, voter_hash, target_url),
            )
            return cur.fetchone() is not None


def _insert_vote_record(source_id: int, voter_hash: str, vote_type: str, target_url: str | None) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO votes (source_id, vote, voter_hash, target_url) VALUES (%s, %s, %s, %s)",
                (source_id, vote_type, voter_hash, target_url),
            )


def _maybe_flush_vote_aggregate(source_id: int, pending_count: int) -> None:
    if pending_count <= 0:
        raise RuntimeError("vote pending counter unavailable")
    if pending_count >= _VOTE_FLUSH_THRESHOLD:
        flush_pending_vote_aggregates([source_id], reason="vote_threshold_flush", operator="system")


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _critical_failure_response(error_code: str, detail: str, *, vote_recorded: bool) -> dict:
    return _json_response(
        503,
        {
            "error": error_code,
            "detail": detail,
            "vote_recorded": vote_recorded,
            "aggregation_status": "failed",
        },
    )


def handler(event, context):
    if event.get("httpMethod") != "POST":
        return _json_response(405, {"error": "method not allowed"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return _json_response(400, {"error": "invalid JSON"})

    try:
        req = VoteRequest(**body)
    except Exception as e:
        return _json_response(400, {"error": str(e)})

    domain = _normalize_domain(req.domain)
    vote_type = req.vote
    target_url = None
    if req.target_url:
        try:
            target_url = _normalize_target_url(req.target_url)
        except ValueError as exc:
            return _json_response(400, {"error": str(exc)})
        target_domain = _extract_target_domain(target_url)
        if target_domain != domain:
            return _json_response(
                400,
                {
                    "error": "target_url domain must match vote domain",
                    "domain": domain,
                    "target_url": target_url,
                    "target_domain": target_domain,
                },
            )

    source_id = _source_id_by_domain(domain)
    if source_id is None:
        return _json_response(404, {"error": f"source not found: {domain}"})

    voter_hash = _compute_voter_hash(event)
    if _check_duplicate(source_id, voter_hash, target_url):
        return _json_response(
            409,
            {
                "error": "you have already voted for this source target",
                "vote": vote_type,
                "target_url": target_url,
            },
        )

    try:
        allowed = check_rate_limit(
            build_rate_limit_key("voter", voter_hash),
            max_requests=10,
            window_seconds=60,
        )
    except Exception as exc:
        logger.exception("vote rate limit backend failed for source_id=%s", source_id)
        return _critical_failure_response("vote_rate_limit_unavailable", str(exc), vote_recorded=False)

    if not allowed:
        return _json_response(429, {"error": "rate limit exceeded, slow down"})

    _insert_vote_record(source_id, voter_hash, vote_type, target_url)
    try:
        pending_count = incr_vote_pending(source_id, vote_type)
        _maybe_flush_vote_aggregate(source_id, pending_count)
    except Exception as exc:
        logger.exception("vote aggregation failed after vote insert for source_id=%s", source_id)
        return _critical_failure_response("vote_aggregation_unavailable", str(exc), vote_recorded=True)

    return _json_response(
        200,
        {
            "ok": True,
            "domain": domain,
            "vote": vote_type,
            "target_url": target_url,
            "aggregation_scope": "source",
            "pending_count": pending_count,
            "aggregation_status": "queued" if pending_count < _VOTE_FLUSH_THRESHOLD else "flushed",
        },
    )
