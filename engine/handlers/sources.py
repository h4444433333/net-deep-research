from __future__ import annotations

"""
GET /v1/sources?domain=X
GET /v1/sources/search?category=X&min_score=Y&limit=20&offset=0
GET /v1/articles/search?q=keyword&limit=10
"""

import json

from cache.redis_client import cache_source, get_cached_source
from repositories.source_repository import (
    fetch_active_source,
    fetch_article_search_rows,
    fetch_sources_search_rows,
)
from utils.request_trace import bind_trace_fields, log_trace_exception, log_trace_node

_DEFAULT_MIN_SCORE = 1.0


def _json_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _to_security_payload(row: dict) -> dict:
    return {
        "risk": int(row["security_risk"]),
        "ssl_valid": None if row["ssl_valid"] is None else int(row["ssl_valid"]),
        "xss_flagged": int(row["xss_flagged"]),
        "sb_flagged": int(row["sb_flagged"]),
    }


def _to_source_payload(row: dict) -> dict:
    row = dict(row)
    row["reputation_score"] = float(row["reputation_score"])
    row["confidence"] = float(row["confidence"])
    row["security_risk"] = int(row["security_risk"])
    row["ssl_valid"] = None if row["ssl_valid"] is None else int(row["ssl_valid"])
    row["xss_flagged"] = int(row["xss_flagged"])
    row["sb_flagged"] = int(row["sb_flagged"])
    row["trust_votes"] = int(row["trust_votes"])
    row["untrust_votes"] = int(row["untrust_votes"])
    row["security"] = _to_security_payload(row)
    return row


def _parse_json_array(value) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [item for item in decoded if isinstance(item, str) and item]
        except json.JSONDecodeError:
            return [value]
    return []


def _db_fetch(domain: str) -> dict | None:
    row = fetch_active_source(domain)
    if row is None:
        return None
    return _to_source_payload(row)


def get_source(domain: str) -> dict:
    cached = get_cached_source(domain)
    if cached:
        log_trace_node(
            "sources.lookup.cache",
            "source lookup cache hit",
            data={"domain": domain, "found": True},
        )
        return {"found": True, "source": cached}

    log_trace_node(
        "sources.lookup.cache",
        "source lookup cache miss",
        data={"domain": domain},
    )
    data = _db_fetch(domain)
    if data is None:
        log_trace_node(
            "sources.lookup.db",
            "source lookup finished with no match",
            data={"domain": domain, "found": False},
        )
        return {"found": False, "source": None}

    cache_source(domain, data)
    log_trace_node(
        "sources.lookup.db",
        "source lookup finished with match",
        data={
            "domain": domain,
            "found": True,
            "reputation_score": data.get("reputation_score"),
            "confidence": data.get("confidence"),
        },
    )
    return {"found": True, "source": data}


def search_sources(
    category: str | None,
    min_score: float,
    limit: int,
    offset: int,
    include_risky: bool,
    include_non_active: bool,
) -> dict:
    rows = [
        _to_source_payload(row)
        for row in fetch_sources_search_rows(
            category=category,
            min_score=min_score,
            limit=limit,
            offset=offset,
            include_risky=include_risky,
            include_non_active=include_non_active,
        )
    ]

    log_trace_node(
        "sources.search.query",
        "source search query finished",
        data={
            "filters": {
                "category": category,
                "min_score": min_score,
                "include_risky": include_risky,
                "include_non_active": include_non_active,
                "limit": limit,
                "offset": offset,
            },
            "filter_sql_fragments": [
                "reputation_score >= %s",
                "category = %s" if category else None,
                None if include_non_active else "status = 'active'",
                None if include_risky else "security_risk = 0",
            ],
            "result_count": len(rows),
            "top_domains": [row["domain"] for row in rows[:10]],
        },
    )

    return {
        "count": len(rows),
        "filters": {
            "category": category,
            "min_score": min_score,
            "include_risky": include_risky,
            "include_non_active": include_non_active,
        },
        "sources": rows,
    }


def search_articles(query: str, limit: int, offset: int, min_article_score: float) -> dict:
    rows = fetch_article_search_rows(
        query=query,
        limit=limit,
        offset=offset,
        min_article_score=min_article_score,
    )

    articles = []
    for row in rows:
        item = dict(row)
        item["topic_tags"] = _parse_json_array(item.get("topic_tags"))
        item["article_score"] = float(item["article_score"]) if item.get("article_score") is not None else None
        if item.get("content_date") is not None:
            item["content_date"] = item["content_date"].isoformat()
        if item.get("last_referenced_at") is not None:
            item["last_referenced_at"] = item["last_referenced_at"].isoformat()
        item["domain_reputation_score"] = (
            float(item["domain_reputation_score"])
            if item.get("domain_reputation_score") is not None
            else None
        )
        item["domain_confidence"] = (
            float(item["domain_confidence"])
            if item.get("domain_confidence") is not None
            else None
        )
        item["security"] = {
            "risk": int(item.get("security_risk", 0)),
            "ssl_valid": None if item.get("ssl_valid") is None else int(item["ssl_valid"]),
            "xss_flagged": int(item.get("xss_flagged", 0)),
            "sb_flagged": int(item.get("sb_flagged", 0)),
        }
        item["rank"] = float(item["rank"]) if item.get("rank") is not None else 0.0
        articles.append(item)

    log_trace_node(
        "articles.search.query",
        "article search query finished",
        data={
            "query": query,
            "filters": {
                "min_article_score": min_article_score,
                "limit": limit,
                "offset": offset,
            },
            "result_count": len(articles),
            "top_articles": [
                {
                    "article_id": item.get("article_id"),
                    "parent_domain": item.get("parent_domain"),
                    "rank": item.get("rank"),
                    "article_score": item.get("article_score"),
                }
                for item in articles[:10]
            ],
        },
    )

    return {
        "count": len(articles),
        "query": query,
        "filters": {"min_article_score": min_article_score},
        "articles": articles,
    }


def handler(event, context):
    path = event.get("path", "/")
    method = event.get("httpMethod", "GET")
    params = event.get("queryParameters", {}) or {}
    bind_trace_fields(query_parameters=params)

    if path.endswith("/articles/search") and method == "GET":
        query = params.get("q", "").strip()
        if not query:
            log_trace_node("articles.search.validate", "article search missing q parameter", level="warning")
            return _json_response(400, {"error": "q parameter is required"})
        try:
            min_article_score = float(params.get("min_article_score", 0))
        except ValueError:
            min_article_score = 0.0
        try:
            limit = int(params.get("limit", 10))
        except ValueError:
            limit = 10
        try:
            offset = int(params.get("offset", 0))
        except ValueError:
            offset = 0
        normalized_limit = min(limit, 50)
        normalized_offset = max(offset, 0)
        normalized_min_score = max(min_article_score, 0.0)
        bind_trace_fields(query=query)
        log_trace_node(
            "articles.search.validate",
            "article search parameters normalized",
            data={
                "query": query,
                "min_article_score": normalized_min_score,
                "limit": normalized_limit,
                "offset": normalized_offset,
            },
        )
        try:
            result = search_articles(query, normalized_limit, normalized_offset, normalized_min_score)
        except Exception as exc:
            log_trace_exception(
                "articles.search.query",
                exc,
                message="article search failed",
                data={"query": query, "limit": normalized_limit, "offset": normalized_offset},
            )
            raise
        return _json_response(200, result)

    if path.endswith("/sources/search") and method == "GET":
        category = params.get("category", "").strip() or None
        try:
            min_s = float(params.get("min_score", _DEFAULT_MIN_SCORE))
        except ValueError:
            min_s = _DEFAULT_MIN_SCORE
        try:
            limit = int(params.get("limit", 20))
        except ValueError:
            limit = 20
        try:
            offset = int(params.get("offset", 0))
        except ValueError:
            offset = 0
        include_risky = params.get("include_risky", "").lower() in {"1", "true", "yes"}
        include_non_active = params.get("include_non_active", "").lower() in {"1", "true", "yes"}
        normalized_min_score = max(min_s, 0.0)
        normalized_limit = min(limit, 100)
        normalized_offset = max(offset, 0)
        log_trace_node(
            "sources.search.validate",
            "source search parameters normalized",
            data={
                "category": category,
                "min_score": normalized_min_score,
                "limit": normalized_limit,
                "offset": normalized_offset,
                "include_risky": include_risky,
                "include_non_active": include_non_active,
            },
        )
        try:
            result = search_sources(
                category=category,
                min_score=normalized_min_score,
                limit=normalized_limit,
                offset=normalized_offset,
                include_risky=include_risky,
                include_non_active=include_non_active,
            )
        except Exception as exc:
            log_trace_exception(
                "sources.search.query",
                exc,
                message="source search failed",
                data={
                    "category": category,
                    "min_score": normalized_min_score,
                    "limit": normalized_limit,
                    "offset": normalized_offset,
                },
            )
            raise
        return _json_response(200, result)

    if path.endswith("/sources") and method == "GET":
        domain = params.get("domain", "").strip().lower()
        if not domain:
            log_trace_node("sources.lookup.validate", "source lookup missing domain parameter", level="warning")
            return _json_response(400, {"error": "domain parameter is required"})
        bind_trace_fields(domain=domain)
        log_trace_node(
            "sources.lookup.validate",
            "source lookup parameters normalized",
            data={"domain": domain, "domain_length": len(domain)},
        )
        try:
            result = get_source(domain)
        except Exception as exc:
            log_trace_exception(
                "sources.lookup.query",
                exc,
                message="source lookup failed",
                data={"domain": domain},
            )
            raise
        return _json_response(200, result)

    return _json_response(404, {"error": "not found"})
