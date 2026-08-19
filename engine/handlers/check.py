"""
POST /v1/sources/check

实时 URL 安全检查端点。Skill 在 fetch 任何 URL 之前调用此接口，
对未知域名做 SSL + Safe Browsing + XSS 深扫，并显式返回安全检查细节。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from psycopg2.extras import RealDictCursor

from db.connection import get_connection
from utils.logger import get_logger
from utils.request_trace import bind_trace_fields, log_trace_exception, log_trace_node

logger = get_logger("check")

_SAFE_BROWSING_KEY = os.environ.get("SAFE_BROWSING_KEY", "").strip()
_XSS_DEEP_SCAN_MODE = (os.environ.get("XSS_DEEP_SCAN_MODE", "active_probe").strip().lower() or "active_probe")
_SSL_TIMEOUT = float(os.environ.get("SSL_TIMEOUT_SECONDS", "3"))
_SB_TIMEOUT = float(os.environ.get("SAFE_BROWSING_TIMEOUT_SECONDS", "2"))
_XSS_SCAN_TIMEOUT = float(os.environ.get("XSS_SCAN_TIMEOUT_SECONDS", "4"))
_XSS_SCAN_MAX_BYTES = int(os.environ.get("XSS_SCAN_MAX_BYTES", "262144"))
_XSS_SCAN_MAX_VARIANTS = int(os.environ.get("XSS_SCAN_MAX_VARIANTS", "4"))
_SECURITY_SCAN_FRESHNESS_SECONDS = int(os.environ.get("SECURITY_SCAN_FRESHNESS_SECONDS", "86400"))
_SECURITY_RESCAN_BATCH_SIZE = int(os.environ.get("SECURITY_RESCAN_BATCH_SIZE", "50"))
_DEAD_LINK_CLEANUP_BATCH_SIZE = int(os.environ.get("DEAD_LINK_CLEANUP_BATCH_SIZE", "50"))
_LOW_SCORE_CLEANUP_BATCH_SIZE = int(os.environ.get("LOW_SCORE_CLEANUP_BATCH_SIZE", "100"))
_LOW_SCORE_THRESHOLD = float(os.environ.get("LOW_SCORE_CLEANUP_THRESHOLD", "1.0"))
_LOW_SCORE_CONFIDENCE_THRESHOLD = float(os.environ.get("LOW_SCORE_CLEANUP_CONFIDENCE_THRESHOLD", "0.6"))
_LINK_PROBE_TIMEOUT = float(os.environ.get("LINK_PROBE_TIMEOUT_SECONDS", "4"))
_HTTP_HEADERS = {
    "User-Agent": "net-info-security-check/1.0",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.2",
}
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
_DEAD_HTTP_CODES = {404, 410, 451}
_SCRIPT_CONTEXT_RE = re.compile(r"<script\b[^>]*>[\s\S]{0,240}__trae_xss_probe_[a-f0-9]{8}__[\s\S]{0,240}</script>", re.I)
_EVENT_HANDLER_RE = re.compile(r"on\w+\s*=\s*['\"][^'\"]{0,240}__trae_xss_probe_[a-f0-9]{8}__", re.I)
_DOM_SINK_RE = re.compile(r"(innerHTML|outerHTML|document\.write|insertAdjacentHTML)\s*=", re.I)
_ALLOWED_WEB_SCHEMES = {"http", "https"}
_BLOCKED_SCHEMES = {"javascript", "data", "file", "ftp"}
_LOCAL_HOSTS = {"localhost"}


def _normalize_url(raw_url: str) -> str:
    if not isinstance(raw_url, str):
        raise ValueError("url must be a string")

    normalized = raw_url.strip()
    if not normalized:
        raise ValueError("url must not be empty")

    parsed = urlparse(normalized)
    if parsed.scheme:
        scheme = parsed.scheme.lower()
        if scheme in _BLOCKED_SCHEMES:
            raise ValueError(f"unsupported url scheme: {scheme}")
        if scheme not in _ALLOWED_WEB_SCHEMES:
            raise ValueError(f"only http and https URLs are allowed, got: {scheme}")
    else:
        normalized = "https://" + normalized
        parsed = urlparse(normalized)

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("url host is required")
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        raise ValueError("local host probing is not allowed")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
        raise ValueError("local or non-public IP targets are not allowed")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("invalid port")

    return urlunparse(parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc, fragment=""))


def _extract_domain(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    domain = (parsed.hostname or "").strip().lower()
    if not domain:
        raise ValueError(f"invalid url: {raw_url}")
    return domain


def _build_invalid_input_result(raw_url: str, detail: str) -> dict:
    return {
        "url": raw_url,
        "domain": None,
        "safe": False,
        "in_db": False,
        "decision": "block",
        "reason": "invalid_input_url",
        "checks": {
            "ssl": {
                "status": "skipped",
                "reason": "invalid_input",
                "detail": detail,
            },
            "safe_browsing": {
                "status": "skipped",
                "configured": bool(_SAFE_BROWSING_KEY),
                "safe": None,
                "reason": "invalid_input",
                "blocking": True,
                "detail": detail,
            },
            "xss_deep_scan": {
                "status": "skipped",
                "mode": _XSS_DEEP_SCAN_MODE,
                "blocking": True,
                "reason": "invalid_input",
                "detail": detail,
                "observable": False,
            },
        },
        "warnings": [],
    }


def _error_text(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:240]


def _bool_to_smallint(value: bool | None) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def _ssl_status_from_cached(ssl_valid: int | None) -> dict:
    if ssl_valid is True or ssl_valid == 1:
        return {"status": "passed", "ssl_valid": True}
    if ssl_valid is False or ssl_valid == 0:
        return {"status": "failed", "ssl_valid": False, "reason": "ssl_invalid"}
    return {"status": "warning", "ssl_valid": None, "reason": "http_only_or_unknown"}


def _check_ssl(domain: str) -> dict:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=_SSL_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", []))
                return {
                    "status": "passed",
                    "ssl_valid": True,
                    "ssl_issuer": issuer or None,
                }
    except (ssl.SSLError, socket.timeout, OSError, ConnectionRefusedError) as exc:
        try:
            socket.create_connection((domain, 80), timeout=_SSL_TIMEOUT).close()
            return {
                "status": "warning",
                "ssl_valid": None,
                "ssl_issuer": None,
                "reason": "http_only",
                "detail": _error_text(exc),
            }
        except (socket.timeout, OSError):
            return {
                "status": "failed",
                "ssl_valid": False,
                "ssl_issuer": None,
                "reason": "ssl_invalid",
                "detail": _error_text(exc),
            }


def _check_safe_browsing(check_url: str) -> dict:
    if not _SAFE_BROWSING_KEY:
        return {
            "status": "skipped",
            "configured": False,
            "safe": None,
            "reason": "not_configured",
            "blocking": False,
        }

    request_url = (
        "https://safebrowsing.googleapis.com/v4/threatMatches:find"
        f"?key={_SAFE_BROWSING_KEY}"
    )
    body = json.dumps(
        {
            "client": {"clientId": "net-info", "clientVersion": "1.0.0"},
            "threatInfo": {
                "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": check_url}],
            },
        }
    ).encode()
    req = urllib.request.Request(request_url, data=body, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=_SB_TIMEOUT) as resp:
            data = json.loads(resp.read())
        matches = data.get("matches") or []
        if matches:
            logger.warning("Safe Browsing blocked %s with %d matches", check_url, len(matches))
            return {
                "status": "blocked",
                "configured": True,
                "safe": False,
                "reason": "malware_phishing",
                "blocking": True,
                "matches_count": len(matches),
            }
        return {
            "status": "passed",
            "configured": True,
            "safe": True,
            "reason": None,
            "blocking": False,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        detail = {
            "status": "failed",
            "configured": True,
            "safe": None,
            "reason": "lookup_failed",
            "blocking": True,
            "detail": _error_text(exc),
        }
        logger.error("Safe Browsing lookup failed for %s: %s", check_url, detail["detail"])
        return detail


def _fetch_http_text(check_url: str, timeout: float, max_bytes: int) -> dict:
    req = urllib.request.Request(check_url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get_content_type() or ""
        charset = resp.headers.get_content_charset() or "utf-8"
        payload = resp.read(max_bytes + 1)
        truncated = len(payload) > max_bytes
        if truncated:
            payload = payload[:max_bytes]
        text = payload.decode(charset, errors="replace")
        return {
            "url": resp.geturl(),
            "status_code": getattr(resp, "status", 200),
            "content_type": content_type,
            "body": text,
            "truncated": truncated,
        }


def _build_xss_probe_urls(check_url: str, payload: str) -> list[str]:
    parsed = urlparse(_normalize_url(check_url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    variants: list[str] = []
    seen: set[str] = set()

    def add_variant(new_pairs: list[tuple[str, str]]) -> None:
        if len(variants) >= _XSS_SCAN_MAX_VARIANTS:
            return
        candidate = urlunparse(parsed._replace(query=urlencode(new_pairs, doseq=True)))
        if candidate in seen:
            return
        seen.add(candidate)
        variants.append(candidate)

    if pairs:
        for idx, (key, _) in enumerate(pairs[:_XSS_SCAN_MAX_VARIANTS]):
            mutated = list(pairs)
            mutated[idx] = (key, payload)
            add_variant(mutated)

    add_variant(pairs + [("xss_probe", payload)])
    if not variants:
        add_variant([("xss_probe", payload)])
    return variants[:_XSS_SCAN_MAX_VARIANTS]


def _classify_reflection(body: str, payload: str) -> dict | None:
    if payload in body:
        start = body.find(payload)
        snippet = body[max(0, start - 180): start + len(payload) + 180]
        if _SCRIPT_CONTEXT_RE.search(snippet):
            context = "script_tag"
        elif _EVENT_HANDLER_RE.search(snippet):
            context = "event_handler"
        elif _DOM_SINK_RE.search(snippet):
            context = "dom_sink_nearby"
        else:
            context = "raw_html"
        return {
            "reflected": True,
            "encoded_only": False,
            "context": context,
            "snippet": snippet[:240],
        }

    decoded = unescape(body)
    if payload in decoded:
        start = decoded.find(payload)
        snippet = decoded[max(0, start - 180): start + len(payload) + 180]
        return {
            "reflected": True,
            "encoded_only": True,
            "context": "html_escaped",
            "snippet": snippet[:240],
        }

    return None


def _check_xss_deep_scan(check_url: str) -> dict:
    if _XSS_DEEP_SCAN_MODE == "disabled":
        return {
            "status": "skipped",
            "mode": "disabled",
            "blocking": False,
            "reason": "disabled_by_config",
            "observable": True,
        }

    if _XSS_DEEP_SCAN_MODE != "active_probe":
        logger.warning("Unknown XSS deep scan mode '%s', falling back to active_probe", _XSS_DEEP_SCAN_MODE)

    marker = f"__trae_xss_probe_{uuid.uuid4().hex[:8]}__"
    payload = f'\"><script>{marker}</script>'
    variants = _build_xss_probe_urls(check_url, payload)
    attempted = 0
    html_responses = 0
    warnings: list[str] = []
    last_error = None

    for variant in variants:
        attempted += 1
        try:
            response = _fetch_http_text(variant, timeout=_XSS_SCAN_TIMEOUT, max_bytes=_XSS_SCAN_MAX_BYTES)
        except urllib.error.HTTPError as exc:
            status_code = getattr(exc, "code", 0)
            if status_code in {403, 406}:
                warnings.append(f"waf_response:{status_code}")
                continue
            last_error = _error_text(exc)
            continue
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = _error_text(exc)
            continue

        content_type = response["content_type"]
        if content_type not in _HTML_CONTENT_TYPES and not content_type.startswith("text/html"):
            warnings.append(f"non_html:{content_type or 'unknown'}")
            continue

        html_responses += 1
        reflection = _classify_reflection(response["body"], payload)
        if not reflection:
            continue

        if reflection["encoded_only"]:
            warnings.append("encoded_reflection_detected")
            continue

        logger.warning("XSS deep scan blocked %s via %s", check_url, variant)
        return {
            "status": "blocked",
            "mode": "active_probe",
            "blocking": True,
            "reason": "reflected_xss_probe",
            "context": reflection["context"],
            "scanned_variants": attempted,
            "matched_url": variant,
            "snippet": reflection["snippet"],
            "observable": True,
        }

    if html_responses == 0 and last_error:
        return {
            "status": "failed",
            "mode": "active_probe",
            "blocking": True,
            "reason": "scanner_request_failed",
            "detail": last_error,
            "scanned_variants": attempted,
            "observable": True,
        }

    if html_responses == 0:
        return {
            "status": "skipped",
            "mode": "active_probe",
            "blocking": False,
            "reason": "non_html_response",
            "scanned_variants": attempted,
            "warnings": warnings,
            "observable": True,
        }

    status = "warning" if warnings else "passed"
    return {
        "status": status,
        "mode": "active_probe",
        "blocking": False,
        "reason": None if not warnings else "probe_warnings",
        "scanned_variants": attempted,
        "warnings": warnings,
        "observable": True,
    }


def _fetch_source_row(domain: str) -> dict | None:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT domain, canonical_url, freshness_url, security_risk, ssl_valid, sb_flagged, xss_flagged,
                       reputation_score, status, last_security_scan
                FROM sources
                WHERE domain = %s
                """,
                (domain,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _pick_scan_url(domain: str, row: dict | None = None, fallback_url: str | None = None) -> str:
    for candidate in (
        fallback_url,
        row.get("freshness_url") if row else None,
        row.get("canonical_url") if row else None,
        f"https://{domain}",
    ):
        if candidate:
            return _normalize_url(candidate)
    return f"https://{domain}"


def _scan_is_fresh(row: dict) -> bool:
    last_scan = row.get("last_security_scan")
    if last_scan is None:
        return False
    if last_scan.tzinfo is None:
        last_scan = last_scan.replace(tzinfo=timezone.utc)
    threshold = datetime.now(timezone.utc) - timedelta(seconds=_SECURITY_SCAN_FRESHNESS_SECONDS)
    return last_scan >= threshold


def _cached_result(domain: str, url: str, row: dict) -> dict:
    checks = {
        "ssl": _ssl_status_from_cached(row.get("ssl_valid")),
        "safe_browsing": {
            "status": "blocked" if row.get("sb_flagged") else "cached_clear",
            "configured": True if row.get("sb_flagged") else None,
            "safe": False if row.get("sb_flagged") else True,
            "reason": "known_malicious" if row.get("sb_flagged") else None,
            "blocking": bool(row.get("sb_flagged")),
        },
        "xss_deep_scan": {
            "status": "blocked" if row.get("xss_flagged") else "cached_clear",
            "mode": "database_cache",
            "blocking": bool(row.get("xss_flagged")),
            "reason": "known_xss_risk" if row.get("xss_flagged") else None,
            "observable": True,
        },
    }
    result = {
        "url": url,
        "domain": domain,
        "safe": not bool(row.get("security_risk") or row.get("sb_flagged") or row.get("xss_flagged")),
        "in_db": True,
        "score": float(row["reputation_score"]) if row.get("reputation_score") is not None else None,
        "source_status": row.get("status"),
        "checks": checks,
        "decision": "allow",
        "reason": None,
        "warnings": [],
        "last_security_scan": row["last_security_scan"].isoformat() if row.get("last_security_scan") else None,
    }
    if row.get("security_risk") or row.get("sb_flagged"):
        result["safe"] = False
        result["decision"] = "block"
        result["reason"] = "known_malicious"
    elif row.get("xss_flagged"):
        result["safe"] = False
        result["decision"] = "block"
        result["reason"] = "known_xss_risk"
    elif row.get("ssl_valid") is False or row.get("ssl_valid") == 0:
        result["warnings"].append("ssl_invalid")
    return result


def _persist_security_scan(
    domain: str,
    security_risk: int,
    ssl_valid: int | None,
    sb_flagged: int,
    xss_flagged: int | None = None,
) -> None:
    assignments = [
        "security_risk = EXCLUDED.security_risk",
        "ssl_valid = EXCLUDED.ssl_valid",
        "sb_flagged = EXCLUDED.sb_flagged",
        "last_security_scan = CURRENT_TIMESTAMP",
    ]
    columns = ["domain", "category", "status", "security_risk", "ssl_valid", "sb_flagged"]
    values = [domain, "unknown", "unverified", security_risk, ssl_valid, sb_flagged]

    if xss_flagged is not None:
        columns.append("xss_flagged")
        values.append(xss_flagged)
        assignments.append("xss_flagged = EXCLUDED.xss_flagged")

    sql = (
        f"INSERT INTO sources ({', '.join(columns)}, last_security_scan) "
        f"VALUES ({', '.join(['%s'] * len(values))}, CURRENT_TIMESTAMP) "
        "ON CONFLICT (domain) DO UPDATE SET "
        + ", ".join(assignments)
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(values))
    except Exception as exc:
        logger.error("Failed to persist security scan for %s: %s", domain, _error_text(exc))


def _run_live_scan(domain: str, url: str, *, in_db: bool) -> dict:
    with ThreadPoolExecutor(max_workers=3) as pool:
        ssl_future = pool.submit(_check_ssl, domain)
        sb_future = pool.submit(_check_safe_browsing, url)
        xss_future = pool.submit(_check_xss_deep_scan, url)
        ssl_result = ssl_future.result()
        sb_result = sb_future.result()
        xss_result = xss_future.result()

    result = {
        "url": url,
        "domain": domain,
        "safe": True,
        "in_db": in_db,
        "decision": "allow",
        "reason": None,
        "warnings": [],
        "checks": {
            "ssl": ssl_result,
            "safe_browsing": sb_result,
            "xss_deep_scan": xss_result,
        },
    }

    ssl_valid = _bool_to_smallint(ssl_result.get("ssl_valid"))
    sb_flagged = 1 if sb_result["status"] == "blocked" else 0
    xss_flagged = 1 if xss_result["status"] == "blocked" else 0
    security_risk = 1 if (sb_flagged or xss_flagged) else 0

    if sb_result["status"] == "blocked":
        result["safe"] = False
        result["decision"] = "block"
        result["reason"] = sb_result["reason"]
    elif sb_result["status"] == "failed":
        result["safe"] = False
        result["decision"] = "block"
        result["reason"] = "safe_browsing_unavailable"
    elif xss_result["status"] == "blocked":
        result["safe"] = False
        result["decision"] = "block"
        result["reason"] = "known_xss_risk"
    elif xss_result["status"] == "failed":
        result["safe"] = False
        result["decision"] = "block"
        result["reason"] = "xss_scan_failed"

    if ssl_result.get("ssl_valid") is False:
        result["warnings"].append("ssl_invalid")
    if xss_result["status"] == "warning":
        result["warnings"].extend(xss_result.get("warnings", []))
    if xss_result["status"] == "skipped":
        result["warnings"].append(xss_result["reason"])

    _persist_security_scan(
        domain,
        security_risk=security_risk,
        ssl_valid=ssl_valid,
        sb_flagged=sb_flagged,
        xss_flagged=xss_flagged,
    )
    return result


def _check_domain(domain: str, url: str) -> dict:
    row = _fetch_source_row(domain)
    if row and (row.get("security_risk") or row.get("sb_flagged") or row.get("xss_flagged")) and _scan_is_fresh(row):
        return _cached_result(domain, url, row)
    if row and _scan_is_fresh(row):
        return _cached_result(domain, url, row)
    return _run_live_scan(domain, _pick_scan_url(domain, row, url), in_db=bool(row))


def _probe_link_health(check_url: str) -> dict:
    try:
        response = _fetch_http_text(check_url, timeout=_LINK_PROBE_TIMEOUT, max_bytes=2048)
        status_code = int(response["status_code"])
        if 200 <= status_code < 400 or status_code in {401, 403}:
            return {"dead": False, "transient": False, "reason": None, "status_code": status_code}
        if status_code in _DEAD_HTTP_CODES:
            return {"dead": True, "transient": False, "reason": f"http_{status_code}", "status_code": status_code}
        if status_code in _TRANSIENT_HTTP_CODES:
            return {"dead": False, "transient": True, "reason": f"http_{status_code}", "status_code": status_code}
        return {"dead": False, "transient": False, "reason": f"http_{status_code}", "status_code": status_code}
    except urllib.error.HTTPError as exc:
        if exc.code in _DEAD_HTTP_CODES:
            return {"dead": True, "transient": False, "reason": f"http_{exc.code}", "status_code": exc.code}
        if exc.code in _TRANSIENT_HTTP_CODES:
            return {"dead": False, "transient": True, "reason": f"http_{exc.code}", "status_code": exc.code}
        if exc.code in {401, 403}:
            return {"dead": False, "transient": False, "reason": f"http_{exc.code}", "status_code": exc.code}
        return {"dead": False, "transient": False, "reason": f"http_{exc.code}", "status_code": exc.code}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return {"dead": True, "transient": False, "reason": "dns_not_found", "status_code": None}
        if isinstance(reason, ConnectionRefusedError):
            return {"dead": True, "transient": False, "reason": "connection_refused", "status_code": None}
        if isinstance(reason, socket.timeout):
            return {"dead": False, "transient": True, "reason": "timeout", "status_code": None}
        return {"dead": False, "transient": True, "reason": _error_text(exc), "status_code": None}
    except (socket.timeout, OSError, ValueError) as exc:
        return {"dead": False, "transient": True, "reason": _error_text(exc), "status_code": None}


def run_security_rescan(
    *,
    limit: int | None = None,
    force: bool = False,
    operator: str = "cron",
) -> dict:
    del operator  # 保留接口，便于任务系统统一传参。
    batch_size = max(1, min(limit or _SECURITY_RESCAN_BATCH_SIZE, 500))
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT domain, canonical_url, freshness_url, security_risk, ssl_valid, sb_flagged, xss_flagged,
                       reputation_score, status, last_security_scan
                FROM sources
                WHERE status != 'dead'
                ORDER BY last_security_scan NULLS FIRST, id ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            rows = [dict(row) for row in cur.fetchall()]

    if not force:
        rows = [row for row in rows if not _scan_is_fresh(row)]

    results = {"requested": len(rows), "processed": 0, "blocked": 0, "failed": 0, "passed": 0, "warnings": 0}
    for row in rows:
        domain = row["domain"]
        scan_url = _pick_scan_url(domain, row)
        outcome = _run_live_scan(domain, scan_url, in_db=True)
        results["processed"] += 1
        if outcome["decision"] == "block":
            if outcome["reason"] in {"safe_browsing_unavailable", "xss_scan_failed"}:
                results["failed"] += 1
            else:
                results["blocked"] += 1
        else:
            results["passed"] += 1
        if outcome["warnings"]:
            results["warnings"] += 1
    return results


def cleanup_dead_sources(*, limit: int | None = None, operator: str = "cron") -> dict:
    batch_size = max(1, min(limit or _DEAD_LINK_CLEANUP_BATCH_SIZE, 500))
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, domain, canonical_url, freshness_url, status
                FROM sources
                WHERE status != 'dead'
                ORDER BY last_verified NULLS FIRST, id ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            rows = [dict(row) for row in cur.fetchall()]

    cleaned: list[dict] = []
    transient: list[dict] = []
    for row in rows:
        check_url = _pick_scan_url(row["domain"], row)
        probe = _probe_link_health(check_url)
        if probe["dead"]:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE sources
                        SET status = 'dead',
                            last_verified = CURRENT_TIMESTAMP,
                            verified_by = %s
                        WHERE id = %s
                        """,
                        (operator, row["id"]),
                    )
            cleaned.append({"domain": row["domain"], "reason": probe["reason"], "status_code": probe["status_code"]})
        elif probe["transient"]:
            transient.append({"domain": row["domain"], "reason": probe["reason"]})
        else:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE sources
                        SET last_verified = CURRENT_TIMESTAMP,
                            verified_by = %s
                        WHERE id = %s
                        """,
                        (operator, row["id"]),
                    )

    return {
        "requested": len(rows),
        "cleaned": len(cleaned),
        "transient_failures": len(transient),
        "sources": cleaned,
        "skipped": transient,
    }


def cleanup_low_score_sources(
    *,
    limit: int | None = None,
    threshold: float | None = None,
    confidence_threshold: float | None = None,
    operator: str = "cron",
) -> dict:
    batch_size = max(1, min(limit or _LOW_SCORE_CLEANUP_BATCH_SIZE, 1000))
    score_threshold = threshold if threshold is not None else _LOW_SCORE_THRESHOLD
    min_confidence = confidence_threshold if confidence_threshold is not None else _LOW_SCORE_CONFIDENCE_THRESHOLD

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, domain, reputation_score, confidence, status
                FROM sources
                WHERE status IN ('active', 'unverified')
                  AND reputation_score < %s
                  AND confidence >= %s
                ORDER BY reputation_score ASC, confidence DESC, id ASC
                LIMIT %s
                """,
                (score_threshold, min_confidence, batch_size),
            )
            rows = [dict(row) for row in cur.fetchall()]

    if not rows:
        return {
            "requested": 0,
            "cleaned": 0,
            "threshold": score_threshold,
            "confidence_threshold": min_confidence,
            "sources": [],
        }

    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    UPDATE sources
                    SET status = 'degraded',
                        last_verified = CURRENT_TIMESTAMP,
                        verified_by = %s
                    WHERE id = %s
                    """,
                    (operator, row["id"]),
                )

    return {
        "requested": len(rows),
        "cleaned": len(rows),
        "threshold": score_threshold,
        "confidence_threshold": min_confidence,
        "sources": [
            {
                "domain": row["domain"],
                "reputation_score": float(row["reputation_score"]),
                "confidence": float(row["confidence"]),
            }
            for row in rows
        ],
    }


def _json_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event, context):
    if event.get("httpMethod") != "POST":
        return _json_response(405, {"error": "method not allowed"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        log_trace_node("check.parse", "invalid sources/check json", level="warning")
        return _json_response(400, {"error": "invalid JSON"})

    urls = body.get("urls", [])
    if not urls or not isinstance(urls, list):
        log_trace_node("check.validate", "sources/check missing urls", level="warning")
        return _json_response(400, {"error": "urls must be a non-empty array"})

    if len(urls) > 20:
        urls = urls[:20]

    bind_trace_fields(check_urls=urls)
    log_trace_node("check.validate", "sources/check payload validated", data={"url_count": len(urls)})

    domain_map = {}
    ordered_keys = []
    results_by_key = {}
    for raw_url in urls:
        try:
            normalized_url = _normalize_url(raw_url)
            domain = _extract_domain(normalized_url)
        except Exception as exc:
            invalid_key = f"invalid:{str(raw_url).strip()}"
            if invalid_key not in results_by_key:
                results_by_key[invalid_key] = _build_invalid_input_result(str(raw_url), str(exc))
                ordered_keys.append(invalid_key)
                log_trace_node(
                    "check.validate.url",
                    "sources/check rejected invalid url",
                    level="warning",
                    data={"url": raw_url, "reason": str(exc)},
                )
            continue
        domain_key = f"domain:{domain}"
        if domain not in domain_map:
            domain_map[domain] = normalized_url
            ordered_keys.append(domain_key)
            results_by_key[domain_key] = None

    log_trace_node("check.scan", "security scan started", data={"domain_count": len(domain_map)})
    for domain, url in domain_map.items():
        domain_key = f"domain:{domain}"
        try:
            results_by_key[domain_key] = _check_domain(domain, url)
            log_trace_node(
                "check.scan.domain",
                "security scan finished for domain",
                data={
                    "domain": domain,
                    "decision": results_by_key[domain_key].get("decision"),
                    "safe": results_by_key[domain_key].get("safe"),
                },
            )
        except Exception as exc:
            log_trace_exception(
                "check.scan.domain",
                exc,
                message="security scan failed for domain",
                data={"domain": domain, "url": url},
            )
            logger.error("Security check failed for %s: %s", domain, _error_text(exc))
            results_by_key[domain_key] = {
                "url": url,
                "domain": domain,
                "safe": False,
                "in_db": False,
                "decision": "block",
                "reason": "internal_security_check_failure",
                "checks": {
                    "ssl": {"status": "unknown"},
                    "safe_browsing": {
                        "status": "failed",
                        "configured": bool(_SAFE_BROWSING_KEY),
                        "safe": None,
                        "reason": "internal_error",
                        "blocking": True,
                        "detail": _error_text(exc),
                    },
                    "xss_deep_scan": {
                        "status": "failed",
                        "mode": _XSS_DEEP_SCAN_MODE,
                        "blocking": True,
                        "reason": "internal_error",
                        "detail": _error_text(exc),
                        "observable": True,
                    },
                },
                "warnings": [],
            }

    results = [results_by_key[key] for key in ordered_keys if results_by_key.get(key) is not None]
    log_trace_node(
        "check.finish",
        "sources/check processed successfully",
        data={"result_count": len(results)},
    )
    return _json_response(200, {"results": results})
