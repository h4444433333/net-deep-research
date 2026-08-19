"""
信誉变更日志窗口清理。

reputation_changelog 只保留近期变更轨迹，长期信誉状态以 sources 当前值为准。
"""

from __future__ import annotations

import os

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("reputation_changelog_retention")

REPUTATION_CHANGELOG_RETENTION_DAYS = int(os.environ.get("REPUTATION_CHANGELOG_RETENTION_DAYS", "180"))


def get_connection():
    """历史兼容入口：信誉变更日志清理默认走 primary 写路径。"""
    return get_write_connection(role="primary", reason="reputation_changelog_retention")


def cleanup_reputation_changelog(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.reputation_changelog')")
                if cur.fetchone()[0] is None:
                    return {"operator": operator, "deleted_changelog_rows": 0}

                cur.execute(
                    """
                    DELETE FROM reputation_changelog
                    WHERE id IN (
                        SELECT id
                        FROM reputation_changelog
                        WHERE created_at < (CURRENT_TIMESTAMP - (%s || ' days')::interval)
                        ORDER BY created_at ASC
                        LIMIT %s
                    )
                    RETURNING id
                    """,
                    (REPUTATION_CHANGELOG_RETENTION_DAYS, row_limit),
                )
                deleted = [row[0] for row in cur.fetchall()]
    except Exception:
        logger.exception("reputation changelog cleanup failed, operator=%s", operator)
        return {"operator": operator, "deleted_changelog_rows": 0}

    logger.info("reputation changelog cleanup deleted %s rows, operator=%s", len(deleted), operator)
    return {"operator": operator, "deleted_changelog_rows": len(deleted)}
