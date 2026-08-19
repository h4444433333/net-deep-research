"""
话题专精信誉积分服务。

对每个 (source_id, topic_tag) 对维护独立的贝叶斯信誉分。
正向信号：cited_in_final 或被选为 evidence
负向信号：discard_reason != None 或 contradiction
"""

from __future__ import annotations

import os

from db.connection import get_connection
from utils.logger import get_logger

logger = get_logger("topic_reputation")

_PRIOR_STRENGTH = float(os.environ.get("TOPIC_PRIOR_STRENGTH", "10"))
_POSITIVE_WEIGHT = float(os.environ.get("TOPIC_POSITIVE_WEIGHT", "1.0"))
_NEGATIVE_WEIGHT = float(os.environ.get("TOPIC_NEGATIVE_WEIGHT", "0.75"))

def update_topic_reputation(
    source_id: int,
    topic_tags: list[str],
    *,
    is_positive: bool,
    cur=None,
) -> None:
    """更新话题专精分。

    传入 `cur` 时复用调用方事务连接（跨表原子落库），否则自行开写连接。
    """
    if not topic_tags:
        return

    def _run(cursor) -> None:
        for tag in topic_tags:
            tag = tag.strip().lower()
            if not tag:
                continue

            cursor.execute(
                """
                INSERT INTO source_topic_reputation
                    (source_id, topic_tag, alpha, beta)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_id, topic_tag) DO NOTHING
                """,
                (source_id, tag, _PRIOR_STRENGTH * 0.5, _PRIOR_STRENGTH * 0.5),
            )

            if is_positive:
                cursor.execute(
                    """
                    UPDATE source_topic_reputation
                    SET alpha = alpha + %s
                    WHERE source_id = %s AND topic_tag = %s
                    """,
                    (_POSITIVE_WEIGHT, source_id, tag),
                )
            else:
                cursor.execute(
                    """
                    UPDATE source_topic_reputation
                    SET beta = beta + %s
                    WHERE source_id = %s AND topic_tag = %s
                    """,
                    (_NEGATIVE_WEIGHT, source_id, tag),
                )

            # 重算评分
            cursor.execute(
                """
                UPDATE source_topic_reputation
                SET reputation_score = ROUND((alpha / NULLIF(alpha + beta, 0))::numeric * 2, 2),
                    confidence = ROUND(LEAST(1.0, (alpha + beta) / 30.0)::numeric, 2)
                WHERE source_id = %s AND topic_tag = %s
                """,
                (source_id, tag),
            )

    if cur is not None:
        _run(cur)
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                _run(cursor)
    except Exception:
        logger.exception(
            "Failed to update topic reputation for source_id=%s tags=%s",
            source_id, topic_tags,
        )


def get_topic_reputation(source_id: int, topic_tag: str) -> dict | None:
    """查询单个话题专精分。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_id, topic_tag, alpha, beta,
                       reputation_score, confidence, updated_at
                FROM source_topic_reputation
                WHERE source_id = %s AND topic_tag = %s
                """,
                (source_id, topic_tag.strip().lower()),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "source_id": row[0],
                "topic_tag": row[1],
                "alpha": float(row[2]),
                "beta": float(row[3]),
                "reputation_score": float(row[4]),
                "confidence": float(row[5]),
                "updated_at": row[6].isoformat() if row[6] else None,
            }


def get_source_topic_scores(source_id: int) -> list[dict]:
    """获取某个信源的所有话题专精分。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT topic_tag, reputation_score, confidence, alpha, beta
                FROM source_topic_reputation
                WHERE source_id = %s
                ORDER BY confidence DESC, reputation_score DESC
                LIMIT 20
                """,
                (source_id,),
            )
            return [
                {
                    "topic_tag": row[0],
                    "reputation_score": float(row[1]),
                    "confidence": float(row[2]),
                    "alpha": float(row[3]),
                    "beta": float(row[4]),
                }
                for row in cur.fetchall()
            ]


def get_top_sources_for_topic(topic_tag: str, limit: int = 10) -> list[dict]:
    """获取某个话题下信誉最高的信源。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT str.source_id, s.domain, str.reputation_score, str.confidence
                FROM source_topic_reputation str
                JOIN sources s ON s.id = str.source_id
                WHERE str.topic_tag = %s AND s.status != 'dead'
                ORDER BY str.confidence DESC, str.reputation_score DESC
                LIMIT %s
                """,
                (topic_tag.strip().lower(), limit),
            )
            return [
                {
                    "source_id": row[0],
                    "domain": row[1],
                    "reputation_score": float(row[2]),
                    "confidence": float(row[3]),
                }
                for row in cur.fetchall()
            ]
