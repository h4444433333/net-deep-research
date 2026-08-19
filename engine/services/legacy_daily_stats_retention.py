"""
旧版日统计清理。

sources_daily_stats 只作为迁移过渡层存在；一旦对应 rollup 已建立，就按窗口逐步删除。
"""

from __future__ import annotations

import os

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("legacy_daily_stats_retention")

LEGACY_DAILY_STATS_RETENTION_DAYS = int(os.environ.get("LEGACY_DAILY_STATS_RETENTION_DAYS", "30"))


def get_connection():
    """历史兼容入口：旧版日统计清理默认走 analytics 写路径。"""
    return get_write_connection(role="analytics", reason="legacy_daily_stats_retention")


def cleanup_legacy_sources_daily_stats(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.sources_daily_stats')")
                if cur.fetchone()[0] is None:
                    return {"operator": operator, "deleted_legacy_daily_stats": 0}

                cur.execute(
                    """
                    WITH doomed AS (
                        SELECT sds.source_id, sds.stat_date
                        FROM sources_daily_stats sds
                        WHERE sds.stat_date < (CURRENT_DATE - %s)
                          AND EXISTS (
                              SELECT 1
                              FROM source_signal_rollup ssr
                              WHERE ssr.source_id = sds.source_id
                                AND (
                                    (ssr.grain = 'daily' AND ssr.bucket_start = sds.stat_date)
                                    OR (ssr.grain = 'monthly' AND ssr.bucket_start = date_trunc('month', sds.stat_date)::date)
                                    OR (ssr.grain = 'quarterly' AND ssr.bucket_start = date_trunc('quarter', sds.stat_date)::date)
                                    OR (ssr.grain = 'yearly' AND ssr.bucket_start = date_trunc('year', sds.stat_date)::date)
                                )
                          )
                        ORDER BY sds.stat_date ASC, sds.source_id ASC
                        LIMIT %s
                    )
                    DELETE FROM sources_daily_stats sds
                    USING doomed
                    WHERE sds.source_id = doomed.source_id
                      AND sds.stat_date = doomed.stat_date
                    RETURNING sds.source_id, sds.stat_date
                    """,
                    (LEGACY_DAILY_STATS_RETENTION_DAYS, row_limit),
                )
                deleted = cur.fetchall()
    except Exception:
        logger.exception("legacy daily stats cleanup failed, operator=%s", operator)
        return {"operator": operator, "deleted_legacy_daily_stats": 0}

    logger.info("legacy daily stats cleanup deleted %s rows, operator=%s", len(deleted), operator)
    return {"operator": operator, "deleted_legacy_daily_stats": len(deleted)}
