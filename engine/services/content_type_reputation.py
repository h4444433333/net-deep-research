"""
内容类型可靠性服务。

按 content_type（如 official_docs, forum, social 等）聚合跨信源的贝叶斯信誉分。
正向信号：cited_in_final 或被选为 evidence
负向信号：discard_reason != None
"""

from __future__ import annotations

import os

from db.connection import get_connection
from utils.logger import get_logger

logger = get_logger("content_type_reputation")

_PRIOR_STRENGTH = float(os.environ.get("CTYPE_PRIOR_STRENGTH", "10"))
_POSITIVE_WEIGHT = float(os.environ.get("CTYPE_POSITIVE_WEIGHT", "1.0"))
_NEGATIVE_WEIGHT = float(os.environ.get("CTYPE_NEGATIVE_WEIGHT", "0.75"))

def update_content_type_reputation(
    content_type: str | None,
    *,
    is_positive: bool,
    cur=None,
) -> None:
    """更新内容类型可靠性分。

    传入 `cur` 时复用调用方事务连接（跨表原子落库），否则自行开写连接。
    """
    if not content_type:
        return

    content_type = content_type.strip().lower()
    if not content_type:
        return

    def _run(cursor) -> None:
        cursor.execute(
            """
            INSERT INTO content_type_reputation
                (content_type, alpha, beta)
            VALUES (%s, %s, %s)
            ON CONFLICT (content_type) DO NOTHING
            """,
            (content_type, _PRIOR_STRENGTH * 0.5, _PRIOR_STRENGTH * 0.5),
        )

        if is_positive:
            cursor.execute(
                "UPDATE content_type_reputation SET alpha = alpha + %s "
                "WHERE content_type = %s",
                (_POSITIVE_WEIGHT, content_type),
            )
        else:
            cursor.execute(
                "UPDATE content_type_reputation SET beta = beta + %s "
                "WHERE content_type = %s",
                (_NEGATIVE_WEIGHT, content_type),
            )

        cursor.execute(
            """
            UPDATE content_type_reputation
            SET reputation_score = ROUND((alpha / NULLIF(alpha + beta, 0))::numeric * 2, 2),
                confidence = ROUND(LEAST(1.0, (alpha + beta) / 30.0)::numeric, 2)
            WHERE content_type = %s
            """,
            (content_type,),
        )

    if cur is not None:
        _run(cur)
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                _run(cursor)
    except Exception:
        logger.exception("Failed to update content_type reputation for %s", content_type)


def get_content_type_reputation(content_type: str) -> dict | None:
    """查询内容类型可靠性分。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content_type, alpha, beta, reputation_score, confidence, updated_at
                FROM content_type_reputation
                WHERE content_type = %s
                """,
                (content_type.strip().lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "content_type": row[0],
                "alpha": float(row[1]),
                "beta": float(row[2]),
                "reputation_score": float(row[3]),
                "confidence": float(row[4]),
                "updated_at": row[5].isoformat() if row[5] else None,
            }


def get_all_content_type_scores() -> list[dict]:
    """获取所有内容类型的可靠性排名。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content_type, reputation_score, confidence, alpha, beta
                FROM content_type_reputation
                ORDER BY confidence DESC, reputation_score DESC
                """
            )
            return [
                {
                    "content_type": row[0],
                    "reputation_score": float(row[1]),
                    "confidence": float(row[2]),
                    "alpha": float(row[3]),
                    "beta": float(row[4]),
                }
                for row in cur.fetchall()
            ]
