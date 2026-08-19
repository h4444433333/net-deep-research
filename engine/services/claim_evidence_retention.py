"""
Claim evidence edge window cleanup.

claim_evidence_edge 只保留近期会话证据边，避免过程表长期无限膨胀。
"""

from __future__ import annotations

import os

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("claim_evidence_retention")

CLAIM_EVIDENCE_RETENTION_DAYS = int(os.environ.get("CLAIM_EVIDENCE_RETENTION_DAYS", "30"))


def get_connection():
    """历史兼容入口：过程层清理默认走 process 写路径。"""
    return get_write_connection(role="process", reason="claim_evidence_retention")


def cleanup_claim_evidence_edges(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.claim_evidence_edge')")
                if cur.fetchone()[0] is None:
                    return {"operator": operator, "deleted_edges": 0}

                cur.execute(
                    """
                    DELETE FROM claim_evidence_edge
                    WHERE id IN (
                        SELECT id
                        FROM claim_evidence_edge
                        WHERE created_at < (CURRENT_TIMESTAMP - (%s || ' days')::interval)
                        ORDER BY created_at ASC
                        LIMIT %s
                    )
                    RETURNING id
                    """,
                    (CLAIM_EVIDENCE_RETENTION_DAYS, row_limit),
                )
                deleted = [row[0] for row in cur.fetchall()]
    except Exception:
        logger.exception("claim evidence cleanup failed, operator=%s", operator)
        return {"operator": operator, "deleted_edges": 0}

    logger.info("claim evidence cleanup deleted %s rows, operator=%s", len(deleted), operator)
    return {"operator": operator, "deleted_edges": len(deleted)}
