"""
信誉分聚合与重算服务。

职责：
1. 将 `votes` / `source_signal_rollup` 聚合回 `sources`
2. 统一重算 `alpha` / `beta` / `reputation_score` / `confidence`
3. 写入 `reputation_changelog`
4. 提供基于 Redis dirty key 的投票批量刷回
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg2.extras import RealDictCursor

from cache.redis_client import (
    cache_source,
    clear_pending_votes,
    get_pending_votes,
    list_pending_vote_source_ids,
)
try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("reputation")


def _env_float(key: str, default: str) -> float:
    raw = os.environ.get(key, default).strip()
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(key: str, default: str) -> int:
    raw = os.environ.get(key, default).strip()
    try:
        return int(raw)
    except ValueError:
        return int(default)


_PRIOR_STRENGTH = _env_float("REPUTATION_PRIOR_STRENGTH", "20")
_TRUST_VOTE_WEIGHT = _env_float("REPUTATION_TRUST_VOTE_WEIGHT", "1.0")
_UNTRUST_VOTE_WEIGHT = _env_float("REPUTATION_UNTRUST_VOTE_WEIGHT", "1.0")
_IMPLICIT_TRUST_WEIGHT = _env_float("REPUTATION_IMPLICIT_TRUST_WEIGHT", "0.35")
_IMPLICIT_UNTRUST_WEIGHT = _env_float("REPUTATION_IMPLICIT_UNTRUST_WEIGHT", "0.75")
_CONTRADICTION_WEIGHT = _env_float("REPUTATION_CONTRADICTION_WEIGHT", "0.50")
_VERIFIABLE_CARRIER_WEIGHT = _env_float("REPUTATION_VERIFIABLE_CARRIER_WEIGHT", "0.25")
_EXACT_MATCH_WEIGHT = _env_float("REPUTATION_EXACT_MATCH_WEIGHT", "0.55")
_INDEPENDENT_CONSENSUS_WEIGHT = _env_float("REPUTATION_INDEPENDENT_CONSENSUS_WEIGHT", "0.75")
_USAGE_CONFIDENCE_WEIGHT = _env_float("REPUTATION_USAGE_CONFIDENCE_WEIGHT", "0.10")
_CONFIDENCE_FULL_SAMPLE = _env_float("REPUTATION_CONFIDENCE_FULL_SAMPLE", "30")
_FULL_RECALC_BATCH_SIZE = _env_int("REPUTATION_FULL_RECALC_BATCH_SIZE", "500")

_PRIOR_MEAN_BY_AUTHORITY = {
    0: 0.30,
    1: 0.50,
    2: 0.80,
}


@dataclass
class RecalculationResult:
    source_id: int
    domain: str
    old_score: float
    new_score: float
    old_confidence: float
    new_confidence: float
    changed: bool


def get_connection():
    """历史兼容入口：信誉写路径默认走 primary 写库。"""
    return get_write_connection(role="primary", reason="reputation")


def _prior_params(authority_base: int) -> tuple[float, float]:
    prior_mean = _PRIOR_MEAN_BY_AUTHORITY.get(authority_base, 0.50)
    alpha = round(prior_mean * _PRIOR_STRENGTH, 4)
    beta = round((1 - prior_mean) * _PRIOR_STRENGTH, 4)
    return alpha, beta


def _rounded_float(value) -> float:
    return round(float(value or 0), 2)


def _compute_reputation(row: dict) -> dict:
    trust_votes = int(row.get("trust_votes_agg") or 0)
    untrust_votes = int(row.get("untrust_votes_agg") or 0)
    usage_count = float(row.get("usage_count_agg") or 0)
    implicit_trust = float(row.get("implicit_trust_agg") or 0)
    implicit_untrust = float(row.get("implicit_untrust_agg") or 0)
    contradictions = float(row.get("contradictions_agg") or 0)
    verifiable_carrier = float(row.get("verifiable_carrier_agg") or 0)
    exact_match = float(row.get("exact_match_agg") or 0)
    independent_consensus = float(row.get("independent_consensus_agg") or 0)

    prior_alpha, prior_beta = _prior_params(int(row["authority_base"]))
    alpha = (
        prior_alpha
        + trust_votes * _TRUST_VOTE_WEIGHT
        + implicit_trust * _IMPLICIT_TRUST_WEIGHT
        + verifiable_carrier * _VERIFIABLE_CARRIER_WEIGHT
        + exact_match * _EXACT_MATCH_WEIGHT
        + independent_consensus * _INDEPENDENT_CONSENSUS_WEIGHT
    )
    beta = (
        prior_beta
        + untrust_votes * _UNTRUST_VOTE_WEIGHT
        + implicit_untrust * _IMPLICIT_UNTRUST_WEIGHT
        + contradictions * _CONTRADICTION_WEIGHT
    )

    total = alpha + beta
    posterior_mean = alpha / total if total > 0 else 0.5
    reputation_score = round(posterior_mean * 2, 2)

    evidence_count = (
        trust_votes
        + untrust_votes
        + implicit_trust
        + implicit_untrust
        + contradictions
        + verifiable_carrier
        + exact_match
        + independent_consensus
        + usage_count * _USAGE_CONFIDENCE_WEIGHT
    )
    confidence = round(min(1.0, evidence_count / _CONFIDENCE_FULL_SAMPLE), 2)

    return {
        "trust_votes": trust_votes,
        "untrust_votes": untrust_votes,
        "alpha": round(alpha, 4),
        "beta": round(beta, 4),
        "reputation_score": reputation_score,
        "confidence": confidence,
    }


def _iter_source_rows(
    source_ids: list[int] | None = None,
    *,
    vote_count_overrides: dict[int, dict[str, int]] | None = None,
):
    where_sql = "WHERE s.status != 'dead'"
    params: tuple = ()
    if source_ids:
        where_sql = "WHERE s.id = ANY(%s)"
        params = (source_ids,)

    sql = f"""
        SELECT
            s.id,
            s.domain,
            s.authority_base,
            s.category,
            s.subcategory,
            s.status,
            s.reputation_score AS current_score,
            s.confidence AS current_confidence,
            COALESCE(s.trust_votes, 0) AS trust_votes_base,
            COALESCE(s.untrust_votes, 0) AS untrust_votes_base,
            COALESCE(d.usage_count, 0) AS usage_count_agg,
            COALESCE(d.implicit_trust, 0) AS implicit_trust_agg,
            COALESCE(d.implicit_untrust, 0) AS implicit_untrust_agg,
            COALESCE(d.contradictions, 0) AS contradictions_agg,
            COALESCE(d.verifiable_carrier, 0) AS verifiable_carrier_agg,
            COALESCE(d.exact_match, 0) AS exact_match_agg,
            COALESCE(d.independent_consensus, 0) AS independent_consensus_agg
        FROM sources AS s
        LEFT JOIN (
            SELECT
                source_id,
                SUM(ref_count_total) AS usage_count,
                SUM(cited_count_total) AS implicit_trust,
                SUM(discard_count_total) AS implicit_untrust,
                SUM(contradiction_count) AS contradictions,
                SUM(verifiable_carrier_count) AS verifiable_carrier,
                SUM(exact_match_count) AS exact_match,
                SUM(independent_consensus_count) AS independent_consensus
            FROM (
                SELECT
                    ssr.source_id,
                    ssr.ref_count_total,
                    ssr.cited_count_total,
                    ssr.discard_count_total,
                    ssr.contradiction_count,
                    ssr.verifiable_carrier_count,
                    ssr.exact_match_count,
                    ssr.independent_consensus_count
                FROM source_signal_rollup ssr
                UNION ALL
                SELECT
                    sds.source_id,
                    sds.usage_count AS ref_count_total,
                    sds.implicit_trust AS cited_count_total,
                    sds.implicit_untrust AS discard_count_total,
                    sds.contradictions AS contradiction_count,
                    0 AS verifiable_carrier_count,
                    0 AS exact_match_count,
                    0 AS independent_consensus_count
                FROM sources_daily_stats sds
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM source_signal_rollup ssr
                    WHERE ssr.source_id = sds.source_id
                      AND ssr.grain = 'daily'
                      AND ssr.bucket_start = sds.stat_date
                )
            ) AS signal_book
            GROUP BY source_id
        ) AS d ON d.source_id = s.id
        {where_sql}
        ORDER BY s.id
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                override = (vote_count_overrides or {}).get(int(row["id"]), {})
                row["trust_votes_agg"] = int(row.get("trust_votes_base") or 0) + int(override.get("trust", 0) or 0)
                row["untrust_votes_agg"] = int(row.get("untrust_votes_base") or 0) + int(override.get("untrust", 0) or 0)
                yield row


def _write_changelog(cur, source_id: int, old_score: float, new_score: float, reason: str, operator: str) -> None:
    if round(old_score, 2) == round(new_score, 2):
        return
    cur.execute(
        """
        INSERT INTO reputation_changelog (source_id, old_score, new_score, reason, operator)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (source_id, old_score, new_score, reason, operator),
    )


def _update_source(cur, row: dict, computed: dict, reason: str, operator: str) -> RecalculationResult:
    old_score = _rounded_float(row["current_score"])
    old_confidence = _rounded_float(row["current_confidence"])
    new_score = computed["reputation_score"]
    new_confidence = computed["confidence"]

    cur.execute(
        """
        UPDATE sources
        SET trust_votes = %s,
            untrust_votes = %s,
            alpha = %s,
            beta = %s,
            reputation_score = %s,
            confidence = %s
        WHERE id = %s
        """,
        (
            computed["trust_votes"],
            computed["untrust_votes"],
            computed["alpha"],
            computed["beta"],
            new_score,
            new_confidence,
            row["id"],
        ),
    )
    _write_changelog(cur, row["id"], old_score, new_score, reason, operator)

    cache_source(
        row["domain"],
        {
            "domain": row["domain"],
            "reputation_score": new_score,
            "confidence": new_confidence,
            "authority_base": int(row["authority_base"]),
            "category": row["category"],
            "subcategory": row["subcategory"] or "",
            "status": row["status"],
            "trust_votes": computed["trust_votes"],
            "untrust_votes": computed["untrust_votes"],
        },
    )

    return RecalculationResult(
        source_id=int(row["id"]),
        domain=row["domain"],
        old_score=old_score,
        new_score=new_score,
        old_confidence=old_confidence,
        new_confidence=new_confidence,
        changed=(old_score != new_score or old_confidence != new_confidence),
    )


def recalculate_source_reputation(
    source_ids: list[int] | None = None,
    *,
    reason: str = "manual_recalc",
    operator: str = "system",
    vote_count_overrides: dict[int, dict[str, int]] | None = None,
) -> dict:
    rows = list(_iter_source_rows(source_ids, vote_count_overrides=vote_count_overrides))
    if not rows:
        return {"requested": 0, "processed": 0, "changed": 0, "sources": []}

    results: list[RecalculationResult] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                computed = _compute_reputation(row)
                results.append(_update_source(cur, row, computed, reason, operator))

    changed_count = sum(1 for item in results if item.changed)
    return {
        "requested": len(source_ids or rows),
        "processed": len(results),
        "changed": changed_count,
        "sources": [
            {
                "source_id": item.source_id,
                "domain": item.domain,
                "old_score": item.old_score,
                "new_score": item.new_score,
                "old_confidence": item.old_confidence,
                "new_confidence": item.new_confidence,
                "changed": item.changed,
            }
            for item in results
        ],
    }


def recalculate_all_sources(*, reason: str = "scheduled_recalc", operator: str = "cron") -> dict:
    processed = 0
    changed = 0
    batches = 0
    last_id = 0

    while True:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM sources
                    WHERE status != 'dead' AND id > %s
                    ORDER BY id
                    LIMIT %s
                    """,
                    (last_id, _FULL_RECALC_BATCH_SIZE),
                )
                batch_ids = [int(row["id"]) for row in cur.fetchall()]

        if not batch_ids:
            break

        summary = recalculate_source_reputation(batch_ids, reason=reason, operator=operator)
        processed += int(summary["processed"])
        changed += int(summary["changed"])
        batches += 1
        last_id = batch_ids[-1]

    return {
        "processed": processed,
        "changed": changed,
        "batches": batches,
    }


def flush_pending_vote_aggregates(
    source_ids: list[int] | None = None,
    *,
    reason: str = "vote_pending_flush",
    operator: str = "system",
) -> dict:
    dirty_ids = source_ids or list_pending_vote_source_ids()
    if not dirty_ids:
        return {"dirty_sources": 0, "processed": 0, "changed": 0, "failed": []}

    dirty_ids = sorted(set(int(source_id) for source_id in dirty_ids))
    failed: list[dict] = []
    processed = 0
    changed = 0

    for source_id in dirty_ids:
        try:
            pending_votes = get_pending_votes(source_id)
            if pending_votes["trust"] <= 0 and pending_votes["untrust"] <= 0:
                clear_pending_votes(source_id)
                continue
            summary = recalculate_source_reputation(
                [source_id],
                reason=reason,
                operator=operator,
                vote_count_overrides={source_id: pending_votes},
            )
            processed += int(summary["processed"])
            changed += int(summary["changed"])
            clear_pending_votes(source_id)
        except Exception as exc:
            logger.exception("flush pending votes failed for source_id=%s", source_id)
            failed.append({"source_id": source_id, "error": str(exc)})

    return {
        "dirty_sources": len(dirty_ids),
        "processed": processed,
        "changed": changed,
        "failed": failed,
    }
