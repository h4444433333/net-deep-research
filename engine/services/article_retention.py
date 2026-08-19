"""
页面证据保留策略。

页面层只作为短中期高价值证据索引存在，不作为长期信誉主体。
"""

from __future__ import annotations

import os

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("article_retention")

ARTICLE_RETENTION_DAYS = int(os.environ.get("ARTICLE_RETENTION_DAYS", "90"))
ARTICLE_HIGH_VALUE_RETENTION_DAYS = int(os.environ.get("ARTICLE_HIGH_VALUE_RETENTION_DAYS", "365"))
ARTICLE_RETENTION_MIN_SCORE = float(os.environ.get("ARTICLE_RETENTION_MIN_SCORE", "1.5"))

OFFICIAL_CONTENT_TYPES = {
    "official_docs",
    "official_blog",
    "official_release",
    "official_api",
    "official_repo",
    "company_filing",
    "company_ir",
    "government_notice",
    "standard_spec",
    "medical_guideline",
    "clinical_evidence",
}


def get_connection():
    """历史兼容入口：内容层清理默认走 content 写路径。"""
    return get_write_connection(role="content", reason="article_retention")

def classify_retention_reason(
    *,
    content_type: str | None,
    cited_in_final: bool,
    citation_count: int,
    selected_as_evidence: bool,
    contribution_weight: float,
    article_score: float,
) -> str:
    normalized_content_type = (content_type or "").strip().lower()
    if normalized_content_type in OFFICIAL_CONTENT_TYPES:
        return "official"
    if citation_count >= 2 or article_score >= 1.85:
        return "high_value"
    if cited_in_final or selected_as_evidence or contribution_weight >= 0.45:
        return "high_value"
    return "ephemeral"


def cleanup_article_sources(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH doomed AS (
                    SELECT article_id
                    FROM article_sources
                    WHERE (
                            retention_reason = 'ephemeral'
                            AND last_referenced_at < (CURRENT_TIMESTAMP - (%s || ' days')::interval)
                            AND COALESCE(cited_count_total, 0) = 0
                            AND COALESCE(adopted_count_total, 0) = 0
                            AND COALESCE(contradiction_count, 0) <= 1
                            AND COALESCE(article_score, 0) < %s
                          )
                       OR (
                            retention_reason = 'high_value'
                            AND last_referenced_at < (CURRENT_TIMESTAMP - (%s || ' days')::interval)
                            AND COALESCE(cited_count_total, 0) < 3
                            AND COALESCE(adopted_count_total, 0) < 3
                            AND COALESCE(article_score, 0) < (%s + 0.25)
                          )
                    ORDER BY last_referenced_at ASC
                    LIMIT %s
                )
                SELECT article_id FROM doomed
                """,
                (
                    ARTICLE_RETENTION_DAYS,
                    ARTICLE_RETENTION_MIN_SCORE,
                    ARTICLE_HIGH_VALUE_RETENTION_DAYS,
                    ARTICLE_RETENTION_MIN_SCORE,
                    row_limit,
                ),
            )
            doomed_ids = [row[0] for row in cur.fetchall()]
            if not doomed_ids:
                return {"operator": operator, "deleted_articles": 0}

            for bucket in ("simhash_bucket_0", "simhash_bucket_1", "simhash_bucket_2", "simhash_bucket_3"):
                cur.execute(
                    f"DELETE FROM {bucket} WHERE article_id = ANY(%s)",
                    (doomed_ids,),
                )
            cur.execute(
                "DELETE FROM article_sources WHERE article_id = ANY(%s)",
                (doomed_ids,),
            )
    logger.info("article cleanup deleted %s rows, operator=%s", len(doomed_ids), operator)
    return {"operator": operator, "deleted_articles": len(doomed_ids)}
