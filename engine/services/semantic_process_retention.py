"""
Semantic process-layer cleanup.

统一清理 semantic storage 的过程层对象，避免 claim_slot_evidence /
candidate_causal_edge / causal_gap 长期堆积。
"""

from __future__ import annotations

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("semantic_process_retention")

_SEMANTIC_PROCESS_TABLES = (
    "claim_slot_evidence",
    "candidate_causal_edge",
    "causal_gap",
)


def get_connection():
    """过程层清理默认走 process 写路径。"""
    return get_write_connection(role="process", reason="semantic_process_retention")


def cleanup_semantic_process_objects(*, limit: int | None = None, operator: str = "cron") -> dict:
    row_limit = max(1, min(limit or 5000, 50000))
    deleted_by_table = {table_name: 0 for table_name in _SEMANTIC_PROCESS_TABLES}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for table_name in _SEMANTIC_PROCESS_TABLES:
                    cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
                    if cur.fetchone()[0] is None:
                        continue
                    cur.execute(
                        f"""
                        DELETE FROM {table_name}
                        WHERE id IN (
                            SELECT id
                            FROM {table_name}
                            WHERE expires_at IS NOT NULL
                              AND expires_at < CURRENT_TIMESTAMP
                            ORDER BY expires_at ASC
                            LIMIT %s
                        )
                        RETURNING id
                        """,
                        (row_limit,),
                    )
                    deleted_by_table[table_name] = len(cur.fetchall())
    except Exception:
        logger.exception("semantic process cleanup failed, operator=%s", operator)
        return {
            "operator": operator,
            "deleted_rows": 0,
            "deleted_by_table": deleted_by_table,
        }

    deleted_rows = sum(deleted_by_table.values())
    logger.info(
        "semantic process cleanup deleted %s rows, operator=%s",
        deleted_rows,
        operator,
    )
    return {
        "operator": operator,
        "deleted_rows": deleted_rows,
        "deleted_by_table": deleted_by_table,
    }
