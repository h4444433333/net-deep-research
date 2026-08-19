"""
投票明细窗口清理。

votes 只承担短期去重与审计明细职责，长期信誉累计保留在 sources 聚合字段。
"""

from __future__ import annotations

import os

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("vote_retention")

VOTE_AUDIT_RETENTION_DAYS = int(os.environ.get("VOTE_AUDIT_RETENTION_DAYS", "180"))


def get_connection():
    """历史兼容入口：投票明细清理默认走 primary 写路径。"""
    return get_write_connection(role="primary", reason="vote_retention")


def cleanup_vote_audit_rows(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.votes')")
                if cur.fetchone()[0] is None:
                    return {"operator": operator, "deleted_votes": 0}

                cur.execute(
                    """
                    DELETE FROM votes
                    WHERE id IN (
                        SELECT id
                        FROM votes
                        WHERE created_at < (CURRENT_TIMESTAMP - (%s || ' days')::interval)
                        ORDER BY created_at ASC
                        LIMIT %s
                    )
                    RETURNING id
                    """,
                    (VOTE_AUDIT_RETENTION_DAYS, row_limit),
                )
                deleted = [row[0] for row in cur.fetchall()]
    except Exception:
        logger.exception("vote audit cleanup failed, operator=%s", operator)
        return {"operator": operator, "deleted_votes": 0}

    logger.info("vote audit cleanup deleted %s rows, operator=%s", len(deleted), operator)
    return {"operator": operator, "deleted_votes": len(deleted)}
