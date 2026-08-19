from __future__ import annotations

import json

try:
    from db.connection import get_read_connection, get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_read_connection
    from db.connection import get_connection as get_write_connection


def _log_trace_node(node: str, message: str, *, data: dict | None = None) -> None:
    try:
        from utils.request_trace import log_trace_node
    except Exception:
        return
    log_trace_node(node, message, data=data)


def fetch_source_reputation_snapshot(source_pks: set[int]) -> dict[int, dict]:
    source_ids = sorted(source_pks)
    if not source_ids:
        return {}

    placeholders = ",".join(["%s"] * len(source_ids))
    sql = f"""
        SELECT id, domain, reputation_score, confidence, status, category, security_risk
        FROM sources
        WHERE id IN ({placeholders})
    """

    with get_read_connection(
        role="primary",
        consistency="strong",
        reason="feedback.source_reputation_snapshot",
    ) as conn:
        _log_trace_node(
            "feedback.snapshot.db.acquire",
            "source reputation snapshot connection acquired",
            data={
                "source_count": len(source_ids),
                "db_role": "primary",
                "db_intent": "read",
                "consistency": "strong",
            },
        )
        with conn.cursor() as cur:
            cur.execute(sql, tuple(source_ids))
            rows = cur.fetchall()

    result: dict[int, dict] = {}
    for row in rows:
        result[int(row[0])] = {
            "source_pk": int(row[0]),
            "domain": row[1],
            "reputation_score": float(row[2]) if row[2] is not None else None,
            "confidence": float(row[3]) if row[3] is not None else None,
            "status": row[4],
            "category": row[5],
            "security_risk": int(row[6]) if row[6] is not None else 0,
        }
    return result


def store_llm_preference(
    *,
    session_id: str,
    source_id: int,
    llm_source_id: str | None,
    blob: dict,
    query_category: str | None = None,
    cur=None,
) -> None:
    normalized_query_category = ((blob.get("query_category") or query_category or "")[:100]) or None
    usefulness = blob.get("source_usefulness_ratings", {})
    rating = usefulness.get(llm_source_id) if llm_source_id and isinstance(usefulness, dict) else None
    answer_gap = (blob.get("answer_quality_gap") or "")[:200] or None

    params = (
        session_id,
        source_id,
        normalized_query_category,
        float(rating) if isinstance(rating, (int, float)) else None,
        answer_gap,
        json.dumps(blob, ensure_ascii=False),
    )
    sql = """
        INSERT INTO llm_preferences
            (session_id, source_id, query_category,
             source_usefulness_rating, answer_quality_gap, preference_blob)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
    """

    if cur is not None:
        cur.execute(sql, params)
        return

    with get_write_connection(role="process", reason="feedback.store_preference") as conn:
        _log_trace_node(
            "feedback.preference.db.acquire",
            "llm preference connection acquired",
            data={
                "session_id": session_id,
                "source_id": source_id,
                "db_role": "process",
                "db_intent": "write",
            },
        )
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
