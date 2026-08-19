"""
统一信号账本。

daily -> monthly -> quarterly -> yearly
长期层只保留汇总，不永久保留细日粒度。
"""

from __future__ import annotations

import os
from datetime import date, datetime

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

logger = get_logger("source_signal_rollup")

DAILY_RETENTION_DAYS = int(os.environ.get("SIGNAL_DAILY_RETENTION_DAYS", "90"))
MONTHLY_RETENTION_MONTHS = int(os.environ.get("SIGNAL_MONTHLY_RETENTION_MONTHS", "24"))
QUARTERLY_RETENTION_QUARTERS = int(os.environ.get("SIGNAL_QUARTERLY_RETENTION_QUARTERS", "12"))


def get_connection():
    """历史兼容入口：聚合统计默认走 analytics 写路径。"""
    return get_write_connection(role="analytics", reason="source_signal_rollup")

def _ensure_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def month_start(value: date | datetime) -> date:
    day = _ensure_date(value)
    return day.replace(day=1)


def quarter_start(value: date | datetime) -> date:
    day = _ensure_date(value)
    month = ((day.month - 1) // 3) * 3 + 1
    return day.replace(month=month, day=1)


def year_start(value: date | datetime) -> date:
    day = _ensure_date(value)
    return day.replace(month=1, day=1)


def _shift_months(anchor: date, months: int) -> date:
    total_months = anchor.year * 12 + (anchor.month - 1) + months
    target_year = total_months // 12
    target_month = total_months % 12 + 1
    return anchor.replace(year=target_year, month=target_month, day=1)


def upsert_signal_rollup(
    cur,
    *,
    source_id: int,
    grain: str,
    bucket_start: date,
    ref_count_total: int = 0,
    cited_count_total: int = 0,
    adopted_count_total: int = 0,
    discard_count_total: int = 0,
    contradiction_count: int = 0,
    quality_high_count: int = 0,
    quality_low_count: int = 0,
    verifiable_carrier_count: int = 0,
    exact_match_count: int = 0,
    independent_consensus_count: int = 0,
) -> None:
    cur.execute(
        """
        INSERT INTO source_signal_rollup (
            source_id,
            grain,
            bucket_start,
            ref_count_total,
            cited_count_total,
            adopted_count_total,
            discard_count_total,
            contradiction_count,
            quality_high_count,
            quality_low_count,
            verifiable_carrier_count,
            exact_match_count,
            independent_consensus_count
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_id, grain, bucket_start) DO UPDATE SET
            ref_count_total = source_signal_rollup.ref_count_total + EXCLUDED.ref_count_total,
            cited_count_total = source_signal_rollup.cited_count_total + EXCLUDED.cited_count_total,
            adopted_count_total = source_signal_rollup.adopted_count_total + EXCLUDED.adopted_count_total,
            discard_count_total = source_signal_rollup.discard_count_total + EXCLUDED.discard_count_total,
            contradiction_count = source_signal_rollup.contradiction_count + EXCLUDED.contradiction_count,
            quality_high_count = source_signal_rollup.quality_high_count + EXCLUDED.quality_high_count,
            quality_low_count = source_signal_rollup.quality_low_count + EXCLUDED.quality_low_count,
            verifiable_carrier_count = source_signal_rollup.verifiable_carrier_count + EXCLUDED.verifiable_carrier_count,
            exact_match_count = source_signal_rollup.exact_match_count + EXCLUDED.exact_match_count,
            independent_consensus_count = source_signal_rollup.independent_consensus_count + EXCLUDED.independent_consensus_count,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            source_id,
            grain,
            bucket_start,
            ref_count_total,
            cited_count_total,
            adopted_count_total,
            discard_count_total,
            contradiction_count,
            quality_high_count,
            quality_low_count,
            verifiable_carrier_count,
            exact_match_count,
            independent_consensus_count,
        ),
    )


def record_daily_signal(
    cur,
    *,
    source_id: int,
    stat_date: date,
    cited_in_final: bool,
    adopted: bool,
    discarded: bool,
    contradiction: bool,
    quality_label: str | None,
    verifiable_carrier_count: int = 0,
    exact_match_count: int = 0,
    independent_consensus_count: int = 0,
) -> None:
    upsert_signal_rollup(
        cur,
        source_id=source_id,
        grain="daily",
        bucket_start=stat_date,
        ref_count_total=1,
        cited_count_total=1 if cited_in_final else 0,
        adopted_count_total=1 if adopted else 0,
        discard_count_total=1 if discarded else 0,
        contradiction_count=1 if contradiction else 0,
        quality_high_count=1 if quality_label == "high" else 0,
        quality_low_count=1 if quality_label == "low" else 0,
        verifiable_carrier_count=max(0, int(verifiable_carrier_count or 0)),
        exact_match_count=max(0, int(exact_match_count or 0)),
        independent_consensus_count=max(0, int(independent_consensus_count or 0)),
    )


def record_daily_contradiction(cur, *, source_id: int, stat_date: date) -> None:
    upsert_signal_rollup(
        cur,
        source_id=source_id,
        grain="daily",
        bucket_start=stat_date,
        discard_count_total=1,
        contradiction_count=1,
    )


def migrate_legacy_daily_stats(limit: int = 5000) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sds.source_id, sds.stat_date, sds.usage_count, sds.implicit_trust,
                       sds.implicit_untrust, sds.contradictions
                FROM sources_daily_stats sds
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM source_signal_rollup ssr
                    WHERE ssr.source_id = sds.source_id
                      AND ssr.grain = 'daily'
                      AND ssr.bucket_start = sds.stat_date
                )
                ORDER BY sds.stat_date ASC, sds.source_id ASC
                LIMIT %s
                """,
                (max(1, limit),),
            )
            rows = cur.fetchall()
            for row in rows:
                upsert_signal_rollup(
                    cur,
                    source_id=int(row[0]),
                    grain="daily",
                    bucket_start=row[1],
                    ref_count_total=int(row[2] or 0),
                    cited_count_total=int(row[3] or 0),
                    discard_count_total=int(row[4] or 0),
                    contradiction_count=int(row[5] or 0),
                )
    return {"migrated_rows": len(rows)}


def _aggregate_grain(grain_from: str, grain_to: str, cutoff: date) -> int:
    if grain_to not in {"monthly", "quarterly", "yearly"}:
        raise ValueError("unsupported target grain")

    bucket_expr = {
        "monthly": "date_trunc('month', bucket_start)::date",
        "quarterly": "date_trunc('quarter', bucket_start)::date",
        "yearly": "date_trunc('year', bucket_start)::date",
    }[grain_to]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    source_id,
                    {bucket_expr} AS bucket_start,
                    SUM(ref_count_total),
                    SUM(cited_count_total),
                    SUM(adopted_count_total),
                    SUM(discard_count_total),
                    SUM(contradiction_count),
                    SUM(quality_high_count),
                    SUM(quality_low_count),
                    SUM(verifiable_carrier_count),
                    SUM(exact_match_count),
                    SUM(independent_consensus_count)
                FROM source_signal_rollup
                WHERE grain = %s
                  AND bucket_start < %s
                GROUP BY source_id, {bucket_expr}
                """,
                (grain_from, cutoff),
            )
            rows = cur.fetchall()
            for row in rows:
                upsert_signal_rollup(
                    cur,
                    source_id=int(row[0]),
                    grain=grain_to,
                    bucket_start=row[1],
                    ref_count_total=int(row[2] or 0),
                    cited_count_total=int(row[3] or 0),
                    adopted_count_total=int(row[4] or 0),
                    discard_count_total=int(row[5] or 0),
                    contradiction_count=int(row[6] or 0),
                    quality_high_count=int(row[7] or 0),
                    quality_low_count=int(row[8] or 0),
                    verifiable_carrier_count=int(row[9] or 0),
                    exact_match_count=int(row[10] or 0),
                    independent_consensus_count=int(row[11] or 0),
                )
            cur.execute(
                """
                DELETE FROM source_signal_rollup
                WHERE grain = %s
                  AND bucket_start < %s
                """,
                (grain_from, cutoff),
            )
    return len(rows)


def compact_signal_rollups() -> dict:
    today = datetime.utcnow().date()
    daily_cutoff = today.fromordinal(today.toordinal() - DAILY_RETENTION_DAYS)
    monthly_anchor = month_start(today)
    monthly_cutoff = _shift_months(monthly_anchor, -MONTHLY_RETENTION_MONTHS)
    quarterly_anchor = quarter_start(today)
    quarterly_cutoff = _shift_months(quarterly_anchor, -(QUARTERLY_RETENTION_QUARTERS * 3))

    migrated = migrate_legacy_daily_stats(limit=20000)
    monthly_rows = _aggregate_grain("daily", "monthly", daily_cutoff)
    quarterly_rows = _aggregate_grain("monthly", "quarterly", monthly_cutoff)
    yearly_rows = _aggregate_grain("quarterly", "yearly", quarterly_cutoff)
    return {
        "migrated_rows": migrated["migrated_rows"],
        "daily_to_monthly": monthly_rows,
        "monthly_to_quarterly": quarterly_rows,
        "quarterly_to_yearly": yearly_rows,
    }
