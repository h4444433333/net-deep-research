"""
LLM 偏好窗口清理。

llm_preferences 只保留近期样本，长期信息应汇总到固定维度层。
"""

from __future__ import annotations

import os

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("llm_preference_retention")

LLM_PREFERENCE_RETENTION_DAYS = int(os.environ.get("LLM_PREFERENCE_RETENTION_DAYS", "180"))


def get_connection():
    """历史兼容入口：偏好过程数据清理默认走 process 写路径。"""
    return get_write_connection(role="process", reason="llm_preference_retention")


def cleanup_llm_preferences(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.llm_preferences')")
                if cur.fetchone()[0] is None:
                    return {"operator": operator, "deleted_preferences": 0}
                cur.execute(
                    """
                    DELETE FROM llm_preferences
                    WHERE id IN (
                        SELECT id
                        FROM llm_preferences
                        WHERE created_at < (CURRENT_TIMESTAMP - (%s || ' days')::interval)
                        ORDER BY created_at ASC
                        LIMIT %s
                    )
                    RETURNING id
                    """,
                    (LLM_PREFERENCE_RETENTION_DAYS, row_limit),
                )
                deleted = [row[0] for row in cur.fetchall()]
    except Exception:
        logger.exception("llm preference cleanup failed, operator=%s", operator)
        return {"operator": operator, "deleted_preferences": 0}
    logger.info("llm preference cleanup deleted %s rows, operator=%s", len(deleted), operator)
    return {"operator": operator, "deleted_preferences": len(deleted)}
