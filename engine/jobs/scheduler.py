"""
轻量级后台任务调度器。

默认关闭，只在设置 `ENABLE_BACKGROUND_JOBS=1` 时启动。
为避免多 worker 重复执行，建议只在单独 job 进程或 leader 实例中开启。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import TextIO

import fcntl

from handlers.check import cleanup_dead_sources, cleanup_low_score_sources, run_security_rescan
from services.article_retention import cleanup_article_sources
from services.claim_evidence_retention import cleanup_claim_evidence_edges
from services.feedback_write_worker import start_feedback_write_worker
from services.legacy_daily_stats_retention import cleanup_legacy_sources_daily_stats
from services.llm_preference_retention import cleanup_llm_preferences
from services.reputation_changelog_retention import cleanup_reputation_changelog
from services.reputation import flush_pending_vote_aggregates, recalculate_all_sources
from services.semantic_process_retention import cleanup_semantic_process_objects
from services.source_signal_rollup import compact_signal_rollups
from services.tag_taxonomy import apply_pending_tag_merges, prune_dynamic_tags, sync_static_taxonomy
from services.vote_retention import cleanup_vote_audit_rows
from utils.logger import get_logger

logger = get_logger("scheduler")

_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()
_scheduler_lock = threading.Lock()
_scheduler_leader_handle: TextIO | None = None

_FLUSH_INTERVAL_SECONDS = int(os.environ.get("JOB_FLUSH_INTERVAL_SECONDS", "60"))
_RECALC_INTERVAL_SECONDS = int(os.environ.get("JOB_RECALC_INTERVAL_SECONDS", "3600"))
_SECURITY_RESCAN_INTERVAL_SECONDS = int(os.environ.get("JOB_SECURITY_RESCAN_INTERVAL_SECONDS", "1800"))
_DEAD_LINK_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("JOB_DEAD_LINK_CLEANUP_INTERVAL_SECONDS", "7200"))
_LOW_SCORE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("JOB_LOW_SCORE_CLEANUP_INTERVAL_SECONDS", "3600"))
_TAG_GOVERNANCE_INTERVAL_SECONDS = int(os.environ.get("JOB_TAG_GOVERNANCE_INTERVAL_SECONDS", "43200"))
_SIGNAL_ROLLUP_INTERVAL_SECONDS = int(os.environ.get("JOB_SIGNAL_ROLLUP_INTERVAL_SECONDS", "21600"))
_ARTICLE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("JOB_ARTICLE_CLEANUP_INTERVAL_SECONDS", "21600"))
_LLM_PREFERENCE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("JOB_LLM_PREFERENCE_CLEANUP_INTERVAL_SECONDS", "43200"))
_CLAIM_EVIDENCE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("JOB_CLAIM_EVIDENCE_CLEANUP_INTERVAL_SECONDS", "43200"))
_VOTE_AUDIT_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("JOB_VOTE_AUDIT_CLEANUP_INTERVAL_SECONDS", "43200"))
_REPUTATION_CHANGELOG_CLEANUP_INTERVAL_SECONDS = int(
    os.environ.get("JOB_REPUTATION_CHANGELOG_CLEANUP_INTERVAL_SECONDS", "43200")
)
_LEGACY_DAILY_STATS_CLEANUP_INTERVAL_SECONDS = int(
    os.environ.get("JOB_LEGACY_DAILY_STATS_CLEANUP_INTERVAL_SECONDS", "43200")
)
_SEMANTIC_PROCESS_CLEANUP_INTERVAL_SECONDS = int(
    os.environ.get("JOB_SEMANTIC_PROCESS_CLEANUP_INTERVAL_SECONDS", "43200")
)
_BACKGROUND_JOBS_LOCK_FILE = os.environ.get("BACKGROUND_JOBS_LOCK_FILE", "/tmp/net_info_scheduler.lock").strip() or (
    "/tmp/net_info_scheduler.lock"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _acquire_scheduler_leader_lock() -> bool:
    global _scheduler_leader_handle

    if _scheduler_leader_handle is not None:
        return True

    try:
        handle = open(_BACKGROUND_JOBS_LOCK_FILE, "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _scheduler_leader_handle = handle
        return True
    except BlockingIOError:
        logger.info(
            "background scheduler skipped because another process already holds leader lock: %s",
            _BACKGROUND_JOBS_LOCK_FILE,
        )
        return False
    except OSError:
        logger.exception("failed to acquire background scheduler leader lock: %s", _BACKGROUND_JOBS_LOCK_FILE)
        return False


def _option_int(options: dict | None, key: str) -> int | None:
    if not options or key not in options or options[key] in (None, ""):
        return None
    return int(options[key])


def _option_float(options: dict | None, key: str) -> float | None:
    if not options or key not in options or options[key] in (None, ""):
        return None
    return float(options[key])


def _run_single_job(job_name: str, options: dict | None = None, *, operator: str = "cron") -> dict:
    if job_name == "vote_flush":
        result = flush_pending_vote_aggregates(reason="scheduled_vote_flush", operator=operator)
    elif job_name == "reputation_recalc":
        result = recalculate_all_sources(reason="scheduled_recalc", operator=operator)
    elif job_name == "security_rescan":
        result = run_security_rescan(
            limit=_option_int(options, "limit"),
            force=bool(options.get("force")) if options else False,
            operator=operator,
        )
    elif job_name == "dead_link_cleanup":
        result = cleanup_dead_sources(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "low_score_cleanup":
        result = cleanup_low_score_sources(
            limit=_option_int(options, "limit"),
            threshold=_option_float(options, "threshold"),
            confidence_threshold=_option_float(options, "confidence_threshold"),
            operator=operator,
        )
    elif job_name == "tag_governance":
        sync_static_taxonomy()
        result = {
            "tag_merge_backfill": apply_pending_tag_merges(limit=_option_int(options, "limit") or 100),
            "tag_prune": prune_dynamic_tags(),
        }
    elif job_name == "signal_rollup":
        result = compact_signal_rollups()
    elif job_name == "article_cleanup":
        result = cleanup_article_sources(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "llm_preference_cleanup":
        result = cleanup_llm_preferences(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "claim_evidence_cleanup":
        result = cleanup_claim_evidence_edges(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "vote_audit_cleanup":
        result = cleanup_vote_audit_rows(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "reputation_changelog_cleanup":
        result = cleanup_reputation_changelog(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "legacy_daily_stats_cleanup":
        result = cleanup_legacy_sources_daily_stats(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    elif job_name == "semantic_process_cleanup":
        result = cleanup_semantic_process_objects(
            limit=_option_int(options, "limit"),
            operator=operator,
        )
    else:
        raise ValueError(f"unknown job: {job_name}")
    return result


def run_job(job_name: str, options: dict | None = None, *, operator: str = "cron") -> dict:
    if job_name == "all":
        result = {
            "vote_flush": _run_single_job("vote_flush", options, operator=operator),
            "reputation_recalc": _run_single_job("reputation_recalc", options, operator=operator),
            "security_rescan": _run_single_job("security_rescan", options, operator=operator),
            "dead_link_cleanup": _run_single_job("dead_link_cleanup", options, operator=operator),
            "low_score_cleanup": _run_single_job("low_score_cleanup", options, operator=operator),
            "tag_governance": _run_single_job("tag_governance", options, operator=operator),
            "signal_rollup": _run_single_job("signal_rollup", options, operator=operator),
            "article_cleanup": _run_single_job("article_cleanup", options, operator=operator),
            "llm_preference_cleanup": _run_single_job("llm_preference_cleanup", options, operator=operator),
            "claim_evidence_cleanup": _run_single_job("claim_evidence_cleanup", options, operator=operator),
            "vote_audit_cleanup": _run_single_job("vote_audit_cleanup", options, operator=operator),
            "reputation_changelog_cleanup": _run_single_job(
                "reputation_changelog_cleanup", options, operator=operator
            ),
            "legacy_daily_stats_cleanup": _run_single_job("legacy_daily_stats_cleanup", options, operator=operator),
            "semantic_process_cleanup": _run_single_job("semantic_process_cleanup", options, operator=operator),
        }
    else:
        result = _run_single_job(job_name, options, operator=operator)

    payload = {"job": job_name, "ran_at": _utc_now(), "result": result}
    logger.info("job finished: %s", json.dumps(payload, ensure_ascii=False))
    return payload


def _scheduler_loop() -> None:
    next_flush_at = time.monotonic()
    next_recalc_at = time.monotonic()
    next_security_rescan_at = time.monotonic()
    next_dead_link_cleanup_at = time.monotonic()
    next_low_score_cleanup_at = time.monotonic()
    next_tag_governance_at = time.monotonic()
    next_signal_rollup_at = time.monotonic()
    next_article_cleanup_at = time.monotonic()
    next_llm_preference_cleanup_at = time.monotonic()
    next_claim_evidence_cleanup_at = time.monotonic()
    next_vote_audit_cleanup_at = time.monotonic()
    next_reputation_changelog_cleanup_at = time.monotonic()
    next_legacy_daily_stats_cleanup_at = time.monotonic()
    next_semantic_process_cleanup_at = time.monotonic()

    logger.info(
        "background scheduler started, flush_interval=%ss, recalc_interval=%ss, security_rescan_interval=%ss, dead_link_cleanup_interval=%ss, low_score_cleanup_interval=%ss, tag_governance_interval=%ss, signal_rollup_interval=%ss, article_cleanup_interval=%ss, llm_preference_cleanup_interval=%ss, claim_evidence_cleanup_interval=%ss, vote_audit_cleanup_interval=%ss, reputation_changelog_cleanup_interval=%ss, legacy_daily_stats_cleanup_interval=%ss, semantic_process_cleanup_interval=%ss",
        _FLUSH_INTERVAL_SECONDS,
        _RECALC_INTERVAL_SECONDS,
        _SECURITY_RESCAN_INTERVAL_SECONDS,
        _DEAD_LINK_CLEANUP_INTERVAL_SECONDS,
        _LOW_SCORE_CLEANUP_INTERVAL_SECONDS,
        _TAG_GOVERNANCE_INTERVAL_SECONDS,
        _SIGNAL_ROLLUP_INTERVAL_SECONDS,
        _ARTICLE_CLEANUP_INTERVAL_SECONDS,
        _LLM_PREFERENCE_CLEANUP_INTERVAL_SECONDS,
        _CLAIM_EVIDENCE_CLEANUP_INTERVAL_SECONDS,
        _VOTE_AUDIT_CLEANUP_INTERVAL_SECONDS,
        _REPUTATION_CHANGELOG_CLEANUP_INTERVAL_SECONDS,
        _LEGACY_DAILY_STATS_CLEANUP_INTERVAL_SECONDS,
        _SEMANTIC_PROCESS_CLEANUP_INTERVAL_SECONDS,
    )

    while not _scheduler_stop.wait(1):
        now = time.monotonic()

        if now >= next_flush_at:
            try:
                run_job("vote_flush")
            except Exception:
                logger.exception("scheduled vote flush failed")
            next_flush_at = now + _FLUSH_INTERVAL_SECONDS

        if now >= next_recalc_at:
            try:
                run_job("reputation_recalc")
            except Exception:
                logger.exception("scheduled reputation recalc failed")
            next_recalc_at = now + _RECALC_INTERVAL_SECONDS

        if now >= next_security_rescan_at:
            try:
                run_job("security_rescan")
            except Exception:
                logger.exception("scheduled security rescan failed")
            next_security_rescan_at = now + _SECURITY_RESCAN_INTERVAL_SECONDS

        if now >= next_dead_link_cleanup_at:
            try:
                run_job("dead_link_cleanup")
            except Exception:
                logger.exception("scheduled dead link cleanup failed")
            next_dead_link_cleanup_at = now + _DEAD_LINK_CLEANUP_INTERVAL_SECONDS

        if now >= next_low_score_cleanup_at:
            try:
                run_job("low_score_cleanup")
            except Exception:
                logger.exception("scheduled low score cleanup failed")
            next_low_score_cleanup_at = now + _LOW_SCORE_CLEANUP_INTERVAL_SECONDS

        if now >= next_tag_governance_at:
            try:
                run_job("tag_governance")
            except Exception:
                logger.exception("scheduled tag governance failed")
            next_tag_governance_at = now + _TAG_GOVERNANCE_INTERVAL_SECONDS

        if now >= next_signal_rollup_at:
            try:
                run_job("signal_rollup")
            except Exception:
                logger.exception("scheduled signal rollup failed")
            next_signal_rollup_at = now + _SIGNAL_ROLLUP_INTERVAL_SECONDS

        if now >= next_article_cleanup_at:
            try:
                run_job("article_cleanup")
            except Exception:
                logger.exception("scheduled article cleanup failed")
            next_article_cleanup_at = now + _ARTICLE_CLEANUP_INTERVAL_SECONDS

        if now >= next_llm_preference_cleanup_at:
            try:
                run_job("llm_preference_cleanup")
            except Exception:
                logger.exception("scheduled llm preference cleanup failed")
            next_llm_preference_cleanup_at = now + _LLM_PREFERENCE_CLEANUP_INTERVAL_SECONDS

        if now >= next_claim_evidence_cleanup_at:
            try:
                run_job("claim_evidence_cleanup")
            except Exception:
                logger.exception("scheduled claim evidence cleanup failed")
            next_claim_evidence_cleanup_at = now + _CLAIM_EVIDENCE_CLEANUP_INTERVAL_SECONDS

        if now >= next_vote_audit_cleanup_at:
            try:
                run_job("vote_audit_cleanup")
            except Exception:
                logger.exception("scheduled vote audit cleanup failed")
            next_vote_audit_cleanup_at = now + _VOTE_AUDIT_CLEANUP_INTERVAL_SECONDS

        if now >= next_reputation_changelog_cleanup_at:
            try:
                run_job("reputation_changelog_cleanup")
            except Exception:
                logger.exception("scheduled reputation changelog cleanup failed")
            next_reputation_changelog_cleanup_at = now + _REPUTATION_CHANGELOG_CLEANUP_INTERVAL_SECONDS

        if now >= next_legacy_daily_stats_cleanup_at:
            try:
                run_job("legacy_daily_stats_cleanup")
            except Exception:
                logger.exception("scheduled legacy daily stats cleanup failed")
            next_legacy_daily_stats_cleanup_at = now + _LEGACY_DAILY_STATS_CLEANUP_INTERVAL_SECONDS

        if now >= next_semantic_process_cleanup_at:
            try:
                run_job("semantic_process_cleanup")
            except Exception:
                logger.exception("scheduled semantic process cleanup failed")
            next_semantic_process_cleanup_at = now + _SEMANTIC_PROCESS_CLEANUP_INTERVAL_SECONDS

    logger.info("background scheduler stopped")


def start_background_jobs() -> bool:
    global _scheduler_thread

    enabled = os.environ.get("ENABLE_BACKGROUND_JOBS", "").strip() == "1"
    if not enabled:
        return False

    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            start_feedback_write_worker()
            return True
        if not _acquire_scheduler_leader_lock():
            return False

        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            name="net-info-scheduler",
            daemon=True,
        )
        _scheduler_thread.start()
        start_feedback_write_worker()
        return True


def stop_background_jobs() -> None:
    global _scheduler_leader_handle
    _scheduler_stop.set()
    if _scheduler_leader_handle is not None:
        try:
            fcntl.flock(_scheduler_leader_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            _scheduler_leader_handle.close()
        except OSError:
            pass
        _scheduler_leader_handle = None
