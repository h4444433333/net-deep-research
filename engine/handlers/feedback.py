"""
POST /v1/research-feedback

接收研究结束后的结构化反馈，记录域名级日统计，并为证据 URL 建立页面级条目。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import parse_qsl, urlsplit, urlunsplit

try:
    from db.connection import get_write_connection, get_write_transaction
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
    from db.connection import get_connection as get_write_transaction
from models.source import FeedbackRequest
from pydantic import ValidationError
from repositories.article_repository import (
    insert_article_source as repo_insert_article_source,
    resolve_existing_article as repo_resolve_existing_article,
    update_article_source as repo_update_article_source,
    upsert_simhash_buckets as repo_upsert_simhash_buckets,
)
from repositories.feedback_repository import (
    fetch_source_reputation_snapshot,
    store_llm_preference,
)
from repositories.source_repository import ensure_source as repo_ensure_source
from repositories.source_repository import update_last_verified as repo_update_last_verified
from services.article_retention import (
    classify_retention_reason,
)
from services.claim_evidence_store import persist_claim_evidence_edges
from services.numeric_verification import aggregate_numeric_consensus, evaluate_numeric_edge
from services.content_type_reputation import update_content_type_reputation
from services.feedback_write_queue import enqueue_feedback_write, feedback_write_async_enabled
from services.quality_scorer import get_quality_scorer
from services.query_normalizer import normalize_query
from services.reputation import recalculate_source_reputation
from services.semantic_storage import persist_semantic_feedback_best_effort
from services.source_signal_rollup import (
    record_daily_contradiction,
    record_daily_signal,
)
from services.tag_taxonomy import resolve_tags, sync_static_taxonomy
from services.topic_reputation import update_topic_reputation
from utils.logger import get_logger
from utils.request_trace import bind_trace_fields, log_trace_exception, log_trace_node

_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_CITATION_ID_PATTERN = re.compile(r"\bsrc_[a-zA-Z0-9_-]+\b")
_TOKEN_PATTERN = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)
_CJK_SEGMENT_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9._%-]{2,}", re.IGNORECASE)
_SPECIAL_TOKEN_PATTERN = re.compile(
    r"(?:20\d{2}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?|v?\d+(?:\.\d+){1,3}(?:-[a-z0-9]+)?|\d+(?:\.\d+)?%?)",
    re.IGNORECASE,
)
logger = get_logger("feedback")

_QUALITY_HIGH_THRESHOLD = 0.75
_QUALITY_LOW_THRESHOLD = 0.35
_QUALITY_CONTENT_TYPE_SCORES = {
    "official_docs": 1.0,
    "official_blog": 0.9,
    "third_party": 0.65,
    "forum": 0.35,
    "social": 0.2,
}
_QUALITY_DISCARD_REASON_FLOORS = {
    "low_quality": 0.0,
    "contradiction_unresolved": 0.0,
    "contradiction": 0.15,
    "outdated": 0.15,
    "derivative_only": 0.2,
    "unsupported": 0.2,
    "unsafe": 0.0,
}


def get_connection():
    """历史测试兼容入口：research-feedback 主持久化路径显式走写库。"""
    return get_write_connection(role="primary", reason="research-feedback.persist")
_VERIFIABLE_PATH_KEYWORDS = (
    "announcement",
    "bulletin",
    "changelog",
    "circular",
    "docs",
    "guideline",
    "manual",
    "notice",
    "policy",
    "regulation",
    "release",
    "rule",
    "spec",
    "standard",
)
_DOCUMENT_FORM_BONUS = {
    "official_notice": 0.18,
    "pdf": 0.16,
    "policy_page": 0.14,
    "release_note": 0.10,
    "spec_page": 0.12,
    "table_page": 0.10,
    "article_page": 0.02,
    "other": 0.0,
}
_STRUCTURED_MARKER_BONUS = {
    "date": 0.05,
    "version": 0.05,
    "identifier": 0.06,
    "table": 0.04,
}
_SNIPPET_SPAN_BONUS = {
    "original_sentence": 0.08,
    "table_cell": 0.06,
    "title": 0.02,
    "summary": 0.0,
}
_CLAIM_SLOT_WEIGHTS = {
    "subject": 0.24,
    "action": 0.18,
    "time": 0.18,
    "location": 0.10,
    "number": 0.20,
    "version_or_policy_name": 0.10,
}


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _format_validation_errors(exc: ValidationError) -> list[dict]:
    details: list[dict] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", [])) or "body"
        details.append(
            {
                "field": location,
                "message": item.get("msg", "invalid value"),
                "type": item.get("type", "validation_error"),
            }
        )
    return details


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _error_text(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:240]


def _render_new_domain_line(domains: list[str]) -> str:
    if not domains:
        return "未涉及"
    joined = ", ".join(domains[:5])
    if len(domains) > 5:
        joined = f"{joined} 等 {len(domains)} 个"
    return f"{joined}（已建档）"


def _summarize_domains(domains: list[str], limit: int = 3) -> str:
    unique_domains = _dedupe_preserve_order(domains)
    if not unique_domains:
        return "未涉及具体域名"
    joined = "、".join(unique_domains[:limit])
    if len(unique_domains) > limit:
        return f"{joined} 等 {len(unique_domains)} 个域名"
    return joined


def _append_explainability_signal(target: list[dict], signal: str, summary: str, **payload) -> None:
    item = {"signal": signal, "summary": summary}
    item.update(payload)
    target.append(item)


def _build_explainability_summary(
    *,
    adoption_basis: list[dict],
    limitations: list[dict],
    key_domains: list[str],
    citation_count: int,
    risk_level: str,
    risk_signals: list[dict],
    recalculation_result: dict,
) -> dict:
    return {
        "adoption_basis": [item["summary"] for item in adoption_basis],
        "limitation": limitations[0]["summary"] if limitations else "当前未发现明显限制。",
        "key_domains": key_domains,
        "final_citation_count": citation_count,
        "risk_status": {
            "level": risk_level,
            "signal_count": len(risk_signals),
            "top_signal": risk_signals[0]["summary"] if risk_signals else None,
        },
        "recalculation_result": recalculation_result,
    }


def _fetch_source_reputation_snapshot(source_pks: set[int]) -> dict[int, dict]:
    return fetch_source_reputation_snapshot(source_pks)


def _format_reputation_label(score: float | None, confidence: float | None) -> str:
    if score is None:
        return "暂无稳定历史分"
    if (confidence or 0.0) < 0.35:
        return "样本还少，分数仍在收敛"
    if score >= 1.45:
        return "历史表现较稳"
    if score >= 1.05:
        return "目前可作为参考"
    return "需要更谨慎看待"


def _build_source_card_summary(card: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    limitations: list[str] = []

    if card["selected_as_evidence"] and card["cited_in_final"]:
        reasons.append("这次既被选作证据，也真正进入了最终回答")
    elif card["cited_in_final"]:
        reasons.append("这次真正进入了最终回答")
    elif card["selected_as_evidence"]:
        reasons.append("这次被选作了证据支撑")
    else:
        reasons.append("这次主要作为背景参考，没有直接进入最终结论")

    if card["citation_count"] > 0:
        reasons.append(f"最终回答里引用了 {card['citation_count']} 次")
    if card["exact_match_edge_count"] > 0:
        reasons.append(f"{card['exact_match_edge_count']} 条证据对主体/时间/数值等关键信息有原文命中")
    elif card["evidence_edge_count"] > 0:
        reasons.append(f"{card['evidence_edge_count']} 条证据片段参与了本次核验")
    elif card["verifiable_carrier_signal"] or card["is_official_like"]:
        reasons.append("页面形态更适合做事实核验")

    if card["independent_consensus_edge_count"] > 0:
        reasons.append("还存在独立来源的一致支持")

    if card["newly_registered"]:
        limitations.append("这是本次新建档信源，历史分还在积累")
    if not card["accessible"]:
        limitations.append("本次无法直接访问正文，部分判断只能依赖已抓到的摘要或历史记录")
    if card["impersonation_risk"] >= 0.5:
        limitations.append("检测到较高仿冒风险，不能单独作为结论依据")
    if card["has_paywall"]:
        limitations.append("存在付费墙，能直接核验到的正文范围有限")
    if card["has_login_wall"]:
        limitations.append("存在登录墙，能直接核验到的正文范围有限")
    if card["is_derivative"]:
        limitations.append("更像转载或二手整理，优先级低于源头材料")

    return "；".join(reasons[:4]) + "。", limitations[:3]


def _build_user_facing_reasoning(
    *,
    source_cards: list[dict],
    citation_count: int,
    evidence_count: int,
    limitations: list[dict],
    risk_signals: list[dict],
    numeric_summary: dict | None = None,
) -> dict:
    adopted_cards = [card for card in source_cards if card.get("adoption_status") == "adopted"]
    adoption_domains = [card["domain"] for card in adopted_cards[:3]]
    if adopted_cards:
        adoption_text = (
            f"这次优先参考 {_summarize_domains(adoption_domains)}，不是只看历史分高低，"
            f"而是因为它们真的进入了本次证据链。"
        )
        if citation_count > 0:
            adoption_text += f" 最终回答共引用了 {citation_count} 次。"
        if evidence_count > 0:
            adoption_text += f" 同时记录了 {evidence_count} 条已采用证据页面。"
        strongest_card = adopted_cards[0]
        adoption_text += f" 其中 {strongest_card['domain']}：{strongest_card['user_summary']}"
    else:
        adoption_text = "这次还没有形成足够稳定的已采用信源，当前解释更多是在说明记录状态，而不是给出强结论。"

    limit_bits: list[str] = []
    if limitations:
        limit_bits.append(limitations[0]["summary"])
    for card in source_cards:
        if card.get("limitations"):
            limit_bits.append(f"{card['domain']}：{card['limitations'][0]}")
        if len(limit_bits) >= 3:
            break
    if not limit_bits and risk_signals:
        limit_bits.append(risk_signals[0]["summary"])
    if not limit_bits:
        limit_bits.append("当前没有发现特别突出的限制，但仍建议结合更多独立来源交叉查看。")
    if numeric_summary and numeric_summary.get("limitation"):
        limit_bits.append(numeric_summary["limitation"])

    limitation_text = "；".join(item.rstrip("。； ") for item in limit_bits[:3] if item).strip()
    if limitation_text:
        limitation_text += "。"
    else:
        limitation_text = "当前没有发现特别突出的限制。"

    user_facing_sources: list[dict] = []
    for card in source_cards[:5]:
        reason_bits: list[str] = []
        if card["cited_in_final"]:
            reason_bits.append("进入了这次最终回答")
        elif card["selected_as_evidence"]:
            reason_bits.append("在这次被当作证据使用")
        else:
            reason_bits.append("这次主要作为背景参考")

        if card["exact_match_edge_count"] > 0:
            reason_bits.append("有能直接对应关键信息的证据片段")
        elif card["evidence_edge_count"] > 0:
            reason_bits.append("有可对应的证据片段")
        elif card["accessible"]:
            reason_bits.append("正文可直接核验")

        if card["independent_consensus_edge_count"] > 0:
            reason_bits.append("还有其他独立来源给出一致支持")

        caution = None
        if card.get("limitations"):
            caution = card["limitations"][0]

        user_facing_sources.append(
            {
                "domain": card["domain"],
                "reputation_score": card["reputation_score"],
                "score_display": card["score_display"],
                "reputation_label": card["reputation_label"],
                "summary": "；".join(reason_bits[:3]) + "。",
                "caution": caution,
            }
        )

    payload = {
        "title": "为什么这次参考这些信源",
        "overview": adoption_text,
        "sources": user_facing_sources,
        "limitation_title": "这次解释的边界",
        "limitation_explanation": limitation_text,
        "writing_guidance": {
            "tone": "自然、简洁、非技术化",
            "focus": "优先解释信源分析依据，不展开内部算法",
            "avoid": [
                "不要提内部算法、阈值、打分公式、权重或内部机制名",
                "不要解释 reputation_score 或 confidence 的计算过程",
                "不要编造本次没有出现的证据、出处或限制",
            ],
        },
    }
    if numeric_summary:
        payload["numeric_reasoning"] = numeric_summary
    return payload


def _build_numeric_reasoning(claim_reviews: list[dict], consensus_summary: dict[tuple[str, str], dict]) -> dict | None:
    if not claim_reviews and not consensus_summary:
        return None

    adopted: list[str] = []
    limited: list[str] = []
    items: list[dict] = []
    consensus_states = [item["state"] for item in consensus_summary.values()]
    for review in claim_reviews:
        numeric = review.get("numeric_review")
        if not numeric:
            continue
        if numeric.get("status") in {"verified", "weakly_verified"}:
            adopted.append(f"{review['text']}：{numeric['summary']}")
        else:
            limited.append(f"{review['text']}：{numeric['summary']}")
        items.append(
            {
                "claim_id": review["claim_id"],
                "summary": numeric["summary"],
                "status": numeric["status"],
            }
        )

    overview_parts: list[str] = []
    if adopted:
        overview_parts.append(f"本次数字采纳主要依据 {adopted[0]}")
    elif limited:
        overview_parts.append(f"本次数字部分暂未形成稳定采纳，主要因为 {limited[0]}")
    if "independent_consensus" in consensus_states:
        overview_parts.append("至少有一个数字指标拿到了独立来源的一致支持")
    if "hard_conflict" in consensus_states:
        overview_parts.append("部分数字在可比口径下仍存在来源冲突，不能直接单边采纳")
    if "source_divergence" in consensus_states:
        overview_parts.append("部分来源给出的数字不完全一致，需要继续结合口径说明理解")
    overview = "；".join(overview_parts) + "。" if overview_parts else "当前没有足够的结构化数字核验结果。"

    limitation = None
    if limited:
        limitation = limited[0]
    elif "insufficient_numeric_evidence" in consensus_states:
        limitation = "部分数字仍缺少足够独立来源，当前只能给出有限判断。"

    if not items and not consensus_states:
        return None

    return {
        "title": "数字依据说明",
        "overview": overview,
        "items": items[:6],
        "limitation": limitation,
    }


def _build_source_cards(
    *,
    source_facts_by_domain: dict[str, dict],
    reputation_snapshot_by_pk: dict[int, dict],
) -> list[dict]:
    cards: list[dict] = []
    for domain, facts in source_facts_by_domain.items():
        reputation = reputation_snapshot_by_pk.get(facts["source_pk"], {})
        card = {
            "domain": domain,
            "source_pk": facts["source_pk"],
            "reputation_score": reputation.get("reputation_score"),
            "confidence": reputation.get("confidence"),
            "reputation_label": _format_reputation_label(
                reputation.get("reputation_score"),
                reputation.get("confidence"),
            ),
            "score_display": (
                f"{reputation['reputation_score']:.2f}/2.00"
                if reputation.get("reputation_score") is not None
                else None
            ),
            "status": reputation.get("status"),
            "category": reputation.get("category"),
            "selected_as_evidence": facts["selected_as_evidence"],
            "cited_in_final": facts["cited_in_final"],
            "citation_count": facts["citation_count"],
            "evidence_edge_count": facts["evidence_edge_count"],
            "exact_match_edge_count": facts["exact_match_edge_count"],
            "independent_consensus_edge_count": facts["independent_consensus_edge_count"],
            "accessible": facts["accessible"],
            "impersonation_risk": facts["impersonation_risk"],
            "has_paywall": facts["has_paywall"],
            "has_login_wall": facts["has_login_wall"],
            "is_official_like": facts["is_official_like"],
            "verifiable_carrier_signal": facts["verifiable_carrier_signal"],
            "is_derivative": facts["is_derivative"],
            "newly_registered": facts["newly_registered"],
            "adoption_status": (
                "adopted"
                if facts["selected_as_evidence"] or facts["cited_in_final"]
                else "observed"
            ),
        }
        summary, card_limitations = _build_source_card_summary(card)
        card["user_summary"] = summary
        card["limitations"] = card_limitations
        cards.append(card)

    return sorted(
        cards,
        key=lambda item: (
            0 if item["adoption_status"] == "adopted" else 1,
            -(item["citation_count"] or 0),
            -(item["exact_match_edge_count"] or 0),
            -((item["reputation_score"] or 0.0)),
            item["domain"],
        ),
    )


def _build_success_record_status(
    *,
    processed: int,
    evidence_urls_recorded: int,
    evidence_urls_created: int,
    contradiction_count: int,
    new_domains: list[str],
    evidence_domains: list[str],
    recalc_summary: dict,
) -> tuple[dict, list[str]]:
    record_status = {
        "source_reputation_stats": {
            "status": "recorded" if processed > 0 else "not_involved",
            "sources_processed": processed,
            "reason": None if processed > 0 else "no_sources_in_request",
        },
        "page_level_evidence_entries": {
            "status": "generated" if evidence_urls_recorded > 0 else "not_generated",
            "recorded_entries": evidence_urls_recorded,
            "new_entries_created": evidence_urls_created,
            "domains": evidence_domains,
            "reason": None if evidence_urls_recorded > 0 else "no_selected_or_cited_sources",
        },
        "new_domain_discovery": {
            "status": "auto_profiled" if new_domains else "not_involved",
            "domains": new_domains,
            "count": len(new_domains),
            "reason": None if new_domains else "no_new_domains_discovered",
        },
        "contradictions": {
            "status": "recorded" if contradiction_count > 0 else "not_involved",
            "count": contradiction_count,
            "reason": None if contradiction_count > 0 else "no_contradictions_reported",
        },
        "reputation_recalculation": {
            "status": "recorded" if int(recalc_summary.get("processed", 0)) > 0 else "not_involved",
            "processed": int(recalc_summary.get("processed", 0)),
            "changed": int(recalc_summary.get("changed", 0)),
            "reason": None if int(recalc_summary.get("processed", 0)) > 0 else "no_touched_sources",
        },
        "failures": [],
    }
    lines = [
        f"信源信誉统计：{'已记录' if processed > 0 else '未记录'}"
        + (f"（{processed} 个信源）" if processed > 0 else "（本次未提交信源）"),
        f"页面级证据条目：{'已生成' if evidence_urls_recorded > 0 else '未生成'}"
        + (
            f"（记录 {evidence_urls_recorded} 条，新增 {evidence_urls_created} 条）"
            if evidence_urls_recorded > 0
            else "（本次没有 selected_as_evidence 或 cited_in_final 的页面）"
        ),
        f"新发现域名：{_render_new_domain_line(new_domains)}",
        "信誉分重算：已执行"
        + f"（处理 {int(recalc_summary.get('processed', 0))} 个信源，分数变化 {int(recalc_summary.get('changed', 0))} 个）",
    ]
    if contradiction_count > 0:
        lines.append(f"矛盾降权记录：已记录（{contradiction_count} 条）")
    return record_status, lines


def _build_failure_record_status(reason: str) -> tuple[dict, list[str]]:
    record_status = {
        "source_reputation_stats": {
            "status": "failed",
            "sources_processed": 0,
            "reason": reason,
        },
        "page_level_evidence_entries": {
            "status": "failed",
            "recorded_entries": 0,
            "new_entries_created": 0,
            "domains": [],
            "reason": reason,
        },
        "new_domain_discovery": {
            "status": "unknown",
            "domains": [],
            "count": 0,
            "reason": reason,
        },
        "contradictions": {
            "status": "failed",
            "count": 0,
            "reason": reason,
        },
        "reputation_recalculation": {
            "status": "failed",
            "processed": 0,
            "changed": 0,
            "reason": reason,
        },
        "failures": [
            {
                "step": "research_feedback_processing",
                "reason": reason,
            }
        ],
    }
    lines = [
        f"信源信誉统计：未记录（{reason}）",
        "页面级证据条目：未生成（依赖前序写入成功）",
        f"新发现域名：无法确认（{reason}）",
        f"信誉分重算：未执行（{reason}）",
    ]
    return record_status, lines


def _build_success_explainability(
    *,
    key_domains: list[str],
    cited_domain_count: int,
    selected_domain_count: int,
    citation_count: int,
    evidence_count: int,
    accessible_adopted_domains: list[str],
    auto_profiled_domains: list[str],
    inaccessible_domains: list[str],
    impersonation_risk_domains: list[str],
    paywall_domains: list[str],
    login_wall_domains: list[str],
    contradiction_count: int,
    unmatched_citation_ids: list[str],
    recalc_summary: dict,
    source_cards: list[dict],
    numeric_summary: dict | None,
) -> dict:
    adoption_basis: list[dict] = []
    limitations: list[dict] = []
    risk_signals: list[dict] = []

    if citation_count > 0:
        _append_explainability_signal(
            adoption_basis,
            "final_citations",
            f"最终答案实际引用 {citation_count} 次，覆盖 {_summarize_domains(key_domains)}",
            count=citation_count,
            domains=key_domains,
        )
    if evidence_count > 0:
        _append_explainability_signal(
            adoption_basis,
            "selected_evidence",
            f"本次记录了 {evidence_count} 条证据页面，采用域名以 {_summarize_domains(key_domains)} 为主",
            count=evidence_count,
            domains=key_domains,
            selected_domain_count=selected_domain_count,
            cited_domain_count=cited_domain_count,
        )
    if accessible_adopted_domains:
        _append_explainability_signal(
            adoption_basis,
            "accessible_sources",
            f"采用信源中有 {len(accessible_adopted_domains)} 个域名可直接访问",
            count=len(accessible_adopted_domains),
            domains=accessible_adopted_domains,
        )
    if int(recalc_summary.get("processed", 0)) > 0:
        _append_explainability_signal(
            adoption_basis,
            "reputation_recalculated",
            f"相关信源已完成信誉重算，处理 {int(recalc_summary.get('processed', 0))} 个，变化 {int(recalc_summary.get('changed', 0))} 个",
            processed=int(recalc_summary.get("processed", 0)),
            changed=int(recalc_summary.get("changed", 0)),
        )

    if evidence_count == 0:
        _append_explainability_signal(
            limitations,
            "no_adopted_evidence",
            "本次没有 selected_as_evidence 或最终引用页面，可解释性主要来自记录状态而非已采用证据",
        )
    if citation_count == 0:
        _append_explainability_signal(
            limitations,
            "no_verified_citations",
            "最终答案没有解析到可验证引用，无法证明哪些来源真正进入了最终结论",
        )
    if auto_profiled_domains:
        _append_explainability_signal(
            limitations,
            "new_domains_unverified",
            f"包含 {_summarize_domains(auto_profiled_domains)} 等新建档域名，当前仍处于未验证状态",
            domains=auto_profiled_domains,
            count=len(auto_profiled_domains),
        )
    if unmatched_citation_ids:
        _append_explainability_signal(
            limitations,
            "unmatched_citations",
            f"最终答案出现 {len(unmatched_citation_ids)} 个未在 feedback sources 中回传的引用标签",
            citation_ids=unmatched_citation_ids,
            count=len(unmatched_citation_ids),
        )
    if contradiction_count > 0:
        _append_explainability_signal(
            limitations,
            "contradictions_recorded",
            f"本次记录了 {contradiction_count} 条矛盾来源，说明结论仍存在需要人工甄别的冲突证据",
            count=contradiction_count,
        )
    if numeric_summary and numeric_summary.get("overview"):
        _append_explainability_signal(
            adoption_basis,
            "numeric_reasoning",
            numeric_summary["overview"],
        )
    if numeric_summary and numeric_summary.get("limitation"):
        _append_explainability_signal(
            limitations,
            "numeric_limitation",
            numeric_summary["limitation"],
        )

    if impersonation_risk_domains:
        _append_explainability_signal(
            risk_signals,
            "impersonation_risk",
            f"{_summarize_domains(impersonation_risk_domains)} 存在较高仿冒风险信号",
            domains=impersonation_risk_domains,
            count=len(impersonation_risk_domains),
            severity="high",
        )
    if inaccessible_domains:
        _append_explainability_signal(
            risk_signals,
            "inaccessible_sources",
            f"{_summarize_domains(inaccessible_domains)} 在本次采集中不可访问，部分判断只能依赖已有摘要",
            domains=inaccessible_domains,
            count=len(inaccessible_domains),
            severity="medium",
        )
    if paywall_domains:
        _append_explainability_signal(
            risk_signals,
            "paywall",
            f"{_summarize_domains(paywall_domains)} 存在付费墙，正文可核验范围受限",
            domains=paywall_domains,
            count=len(paywall_domains),
            severity="medium",
        )
    if login_wall_domains:
        _append_explainability_signal(
            risk_signals,
            "login_wall",
            f"{_summarize_domains(login_wall_domains)} 存在登录墙，正文可核验范围受限",
            domains=login_wall_domains,
            count=len(login_wall_domains),
            severity="medium",
        )

    risk_level = "low"
    if any(item.get("severity") == "high" for item in risk_signals):
        risk_level = "high"
    elif risk_signals or limitations:
        risk_level = "medium"

    adoption_line = (
        f"优先采用 {_summarize_domains(key_domains)} 的证据"
        if key_domains
        else "本次未形成明确的采用域名"
    )
    if citation_count > 0 or evidence_count > 0:
        adoption_line = (
            f"{adoption_line}：最终引用 {citation_count} 次，记录证据页面 {evidence_count} 条，"
            f"并完成 {int(recalc_summary.get('processed', 0))} 个信源的信誉重算。"
        )
    limitation_line = "当前未发现明显限制。"
    if limitations:
        limitation_line = limitations[0]["summary"]
    elif risk_signals:
        limitation_line = risk_signals[0]["summary"]

    recalculation_result = {
        "status": "recorded" if int(recalc_summary.get("processed", 0)) > 0 else "not_involved",
        "processed": int(recalc_summary.get("processed", 0)),
        "changed": int(recalc_summary.get("changed", 0)),
    }

    return {
        "status": "ready" if key_domains or citation_count > 0 or evidence_count > 0 else "limited",
        "key_domains": key_domains,
        "citation_count": citation_count,
        "evidence_count": evidence_count,
        "adoption_basis": adoption_basis,
        "limitations": limitations,
        "risk": {
            "level": risk_level,
            "signals": risk_signals,
        },
        "recalculation_result": recalculation_result,
        "structured_summary": _build_explainability_summary(
            adoption_basis=adoption_basis,
            limitations=limitations,
            key_domains=key_domains,
            citation_count=citation_count,
            risk_level=risk_level,
            risk_signals=risk_signals,
            recalculation_result=recalculation_result,
        ),
        "summary_lines": {
            "adoption": adoption_line,
            "limitation": limitation_line,
        },
        "user_facing_reasoning": _build_user_facing_reasoning(
            source_cards=source_cards,
            citation_count=citation_count,
            evidence_count=evidence_count,
            limitations=limitations,
            risk_signals=risk_signals,
            numeric_summary=numeric_summary,
        ),
    }


def _build_failure_explainability(reason: str) -> dict:
    recalculation_result = {
        "status": "failed",
        "processed": 0,
        "changed": 0,
    }
    limitations = [
        {
            "signal": "feedback_failed",
            "summary": f"feedback 持久化失败，当前无法依据回传结果生成可靠解释：{reason}",
            "reason": reason,
        }
    ]
    risk_signals = [
        {
            "signal": "feedback_failed",
            "summary": f"research-feedback 处理失败：{reason}",
            "reason": reason,
            "severity": "high",
        }
    ]
    return {
        "status": "failed",
        "key_domains": [],
        "citation_count": 0,
        "evidence_count": 0,
        "adoption_basis": [],
        "limitations": limitations,
        "risk": {
            "level": "high",
            "signals": risk_signals,
        },
        "recalculation_result": recalculation_result,
        "structured_summary": _build_explainability_summary(
            adoption_basis=[],
            limitations=limitations,
            key_domains=[],
            citation_count=0,
            risk_level="high",
            risk_signals=risk_signals,
            recalculation_result=recalculation_result,
        ),
        "summary_lines": {
            "adoption": "未生成采用依据摘要。",
            "limitation": f"research-feedback 处理失败：{reason}",
        },
        "user_facing_reasoning": {
            "title": "为什么这次参考这些信源",
            "overview": "由于 feedback 处理失败，这次无法基于会话真实数据生成可靠的信源说明。",
            "sources": [],
            "limitation_title": "这次解释的边界",
            "limitation_explanation": f"research-feedback 处理失败：{reason}。",
            "writing_guidance": {
                "tone": "自然、简洁、非技术化",
                "focus": "优先解释信源分析依据，不展开内部算法",
                "avoid": [
                    "不要编造本次没有成功记录的证据依据",
                    "不要向用户解释内部算法或失败链路细节",
                ],
            },
        },
    }


def _normalize_domain(domain: str) -> str:
    cleaned = domain.strip().lower()
    if cleaned.startswith("www."):
        return cleaned[4:]
    return cleaned


def _normalize_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    host = parts.netloc.lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":80") and parts.scheme.lower() == "http":
        host = host[:-3]
    if host.endswith(":443") and parts.scheme.lower() == "https":
        host = host[:-4]

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    query = "&".join(
        f"{key}={value}" if value else key
        for key, value in kept_query
    )
    scheme = (parts.scheme or "https").lower()
    normalized = urlunsplit((scheme, host, path, query, ""))
    return normalized[:2048]


def _safe_content_date(raw_value: str | None) -> str | None:
    if not raw_value:
        return None

    candidate = raw_value.strip()
    if not candidate:
        return None

    if len(candidate) >= 10:
        try:
            return datetime.strptime(candidate[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _stable_hex(seed: str, prefix: str) -> str:
    return hashlib.sha256(f"{prefix}:{seed}".encode("utf-8")).hexdigest()[:16]


def _simhash_buckets(simhash_fingerprint: str) -> list[tuple[int, str]]:
    return [(idx, simhash_fingerprint[idx * 4:(idx + 1) * 4]) for idx in range(4)]


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _build_article_text(src) -> str:
    parts = [
        _normalize_text(src.title),
        _normalize_text(src.content_summary),
        " ".join(_normalize_text(tag) for tag in src.topic_tags),
    ]
    text = " ".join(part for part in parts if part)
    return text or _normalize_text(src.url)


def _derive_article_title(src) -> str | None:
    if src.title and src.title.strip():
        return src.title.strip()[:1024]

    parsed = urlsplit(_normalize_url(src.url))
    last_segment = parsed.path.rstrip("/").split("/")[-1].strip()
    if last_segment:
        candidate = re.sub(r"[-_]+", " ", last_segment).strip()
        if candidate:
            return candidate[:1024]
    return _normalize_domain(src.domain)[:1024]


def _derive_topic_tags(src, claim_text_by_id: dict[str, str]) -> list[str]:
    explicit_tags = _dedupe_preserve_order([tag.strip() for tag in src.topic_tags if tag and tag.strip()])
    if explicit_tags:
        resolved = resolve_tags(explicit_tags)
        return resolved["canonical_tags"][:8]

    derived_tags: list[str] = []
    for claim_id in src.support_claim_ids:
        claim_text = claim_text_by_id.get(claim_id, "").strip()
        if claim_text:
            derived_tags.append(claim_text[:120])
        elif claim_id:
            derived_tags.append(claim_id.strip())
    resolved = resolve_tags(_dedupe_preserve_order(derived_tags))
    return resolved["canonical_tags"][:8]


def _derive_content_summary(
    src,
    *,
    query: str,
    claim_text_by_id: dict[str, str],
    title: str | None,
    topic_tags: list[str],
) -> str | None:
    if src.content_summary and src.content_summary.strip():
        return src.content_summary.strip()

    claim_snippets = [
        claim_text_by_id[claim_id].strip()
        for claim_id in src.support_claim_ids
        if claim_id in claim_text_by_id and claim_text_by_id[claim_id].strip()
    ]
    fragments = _dedupe_preserve_order(
        [
            query.strip(),
            title or "",
            *claim_snippets[:3],
            " ".join(topic_tags[:4]) if topic_tags else "",
            _normalize_domain(src.domain),
            _normalize_url(src.url),
        ]
    )
    summary = " | ".join(fragment for fragment in fragments if fragment)
    return summary[:4000] if summary else None


def _compact_query_normalization_fields(payload: dict | None) -> dict:
    if not payload:
        return {}

    compacted: dict[str, object] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                compacted[key] = cleaned
            continue
        if isinstance(value, list):
            if value:
                compacted[key] = value
            continue
        compacted[key] = value
    return compacted


def _preview_text(value: str | None, limit: int = 180) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return None
    return cleaned[:limit]


def _collect_feedback_keywords(req: FeedbackRequest, query_normalization: dict | None) -> list[str]:
    keyword_candidates: list[str] = []

    def _push(value: str | None) -> None:
        preview = _preview_text(value, limit=64)
        if not preview:
            return
        keyword_candidates.append(preview)
        keyword_candidates.extend(_CJK_SEGMENT_PATTERN.findall(preview))
        keyword_candidates.extend(token.lower() for token in _LATIN_TOKEN_PATTERN.findall(preview))
        keyword_candidates.extend(_SPECIAL_TOKEN_PATTERN.findall(preview))

    _push(req.query)
    if query_normalization:
        _push(str(query_normalization.get("normalized_query") or ""))
        _push(str(query_normalization.get("raw_query") or ""))
        _push(str(query_normalization.get("query_category") or ""))

    for claim in req.claims[:5]:
        _push(claim.subject)
        _push(claim.action)
        _push(claim.location)
        _push(claim.time)
        _push(claim.number)
        _push(claim.version_or_policy_name)

    for src in req.sources[:5]:
        _push(src.domain)
        _push(getattr(src, "title", None))
        _push(getattr(src, "content_summary", None))

    compact_keywords: list[str] = []
    for token in _dedupe_preserve_order([item for item in keyword_candidates if item]):
        normalized = token.strip()
        if len(normalized) < 2:
            continue
        compact_keywords.append(normalized)
        if len(compact_keywords) >= 12:
            break
    return compact_keywords


def _build_feedback_semantic_preview(req: FeedbackRequest, query_normalization: dict | None) -> dict[str, object]:
    claim_preview: list[dict[str, object]] = []
    for claim in req.claims[:3]:
        slots = _dedupe_preserve_order(
            [
                value
                for value in [
                    claim.subject,
                    claim.action,
                    claim.location,
                    claim.time,
                    claim.number,
                    claim.version_or_policy_name,
                ]
                if value and value.strip()
            ]
        )
        claim_preview.append(
            {
                "claim_id": claim.claim_id,
                "text": _preview_text(claim.text),
                "slots": slots[:4],
                "supported_by": claim.supported_by[:3],
            }
        )

    typed_conflict_types = _dedupe_preserve_order(
        [
            item.conflict_type.strip()
            for item in req.typed_conflicts
            if getattr(item, "conflict_type", None) and item.conflict_type.strip()
        ]
    )
    source_domains = _dedupe_preserve_order(
        [_normalize_domain(src.domain) for src in req.sources if getattr(src, "domain", None)]
    )

    return {
        "session_id": req.session_id,
        "query_present": bool(req.query),
        "query_seed_preview": _preview_text(_resolve_feedback_query_seed(req), limit=96),
        "semantic_keywords": _collect_feedback_keywords(req, query_normalization),
        "claim_preview": claim_preview,
        "source_domains": source_domains[:5],
        "typed_conflict_types": typed_conflict_types[:6],
        "candidate_causal_edge_count": len(req.candidate_causal_edges),
        "causal_gap_count": len(req.causal_gaps),
    }


def _resolve_feedback_query_seed(req: FeedbackRequest) -> str | None:
    if req.query:
        return req.query.strip()
    if not req.query_normalization:
        return None
    for candidate in (req.query_normalization.normalized_query, req.query_normalization.raw_query):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _build_feedback_query_normalization(req: FeedbackRequest) -> dict:
    provided = req.query_normalization.model_dump(exclude_none=True) if req.query_normalization else None
    query_seed = _resolve_feedback_query_seed(req)
    if query_seed:
        return normalize_query(query_seed, provided)
    return _compact_query_normalization_fields(provided)


def _build_article_text_from_values(
    title: str | None,
    content_summary: str | None,
    topic_tags: list[str],
    fallback_url: str,
) -> str:
    parts = [
        _normalize_text(title),
        _normalize_text(content_summary),
        " ".join(_normalize_text(tag) for tag in topic_tags),
    ]
    text = " ".join(part for part in parts if part)
    return text or _normalize_text(fallback_url)


def _update_all_signals(
    src,
    source_id: int,
    effective_cited_in_final: bool,
    effective_citation_count: int,
    session_id: str,
    preference_blob: dict | None,
    query_category: str | None = None,
    quality_signal: dict | None = None,
    canonical_topic_tags: list[str] | None = None,
    cur=None,
) -> None:
    """统一更新所有信誉信号：质量评分、偏好声明、话题专精、内容类型。

    传入 `cur`（事务内）时，偏好/话题/内容类型写复用该连接且不吞异常，
    任一失败都会触发外层事务回滚；`cur=None` 时保持独立写 + best-effort 吞异常。
    """
    # ---- 1. 贝叶斯质量评分（内存单例，无 DB 写入，失败不影响事务）----
    text = _build_article_text(src)
    if src.content_summary and src.content_summary.strip():
        try:
            scorer = get_quality_scorer()
            quality_signal = quality_signal or _compute_quality_signal(
                src,
                effective_cited_in_final,
                effective_citation_count,
                preference_blob,
            )
            if quality_signal["label"] == "high":
                scorer.update_high([text], weight=quality_signal["weight"])
            elif quality_signal["label"] == "low":
                scorer.update_low([text], weight=quality_signal["weight"])
        except Exception:
            pass

    # ---- 2. 偏好声明存储 ----
    if preference_blob:
        _store_preferences(
            session_id,
            source_id,
            src.source_id,
            preference_blob,
            query_category=query_category,
            cur=cur,
        )

    # ---- 3. 话题专精 + 4. 内容类型 ----
    is_positive = src.selected_as_evidence or effective_cited_in_final
    is_negative = src.discard_reason is not None

    if is_positive or is_negative:
        topic_tags = canonical_topic_tags or resolve_tags(src.topic_tags)["canonical_tags"]
        if topic_tags:
            if cur is not None:
                update_topic_reputation(source_id, topic_tags, is_positive=is_positive, cur=cur)
            else:
                try:
                    update_topic_reputation(source_id, topic_tags, is_positive=is_positive)
                except Exception:
                    pass

        if src.content_type:
            if cur is not None:
                update_content_type_reputation(src.content_type, is_positive=is_positive, cur=cur)
            else:
                try:
                    update_content_type_reputation(
                        src.content_type, is_positive=is_positive,
                    )
                except Exception:
                    pass


def _store_preferences(
    session_id: str,
    source_id: int,
    llm_source_id: str | None,
    blob: dict,
    query_category: str | None = None,
    cur=None,
) -> None:
    """将 LLM 自报偏好声明写入 llm_preferences 表。

    事务内（cur 传入）不吞异常，交给外层事务回滚；独立写（cur=None）保持 best-effort。
    """
    if cur is not None:
        store_llm_preference(
            session_id=session_id,
            source_id=source_id,
            llm_source_id=llm_source_id,
            blob=blob,
            query_category=query_category,
            cur=cur,
        )
        return
    try:
        store_llm_preference(
            session_id=session_id,
            source_id=source_id,
            llm_source_id=llm_source_id,
            blob=blob,
            query_category=query_category,
        )
    except Exception:
        pass


def _compute_simhash(text: str) -> str:
    tokens = _TOKEN_PATTERN.findall(text.lower())
    if not tokens:
        return "0" * 16

    vector = [0] * 64
    for token in tokens:
        digest = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:16], 16)
        for bit in range(64):
            if digest & (1 << bit):
                vector[bit] += 1
            else:
                vector[bit] -= 1

    fingerprint = 0
    for bit, weight in enumerate(vector):
        if weight >= 0:
            fingerprint |= 1 << bit
    return f"{fingerprint:016x}"


def _hex_hamming_distance(lhs: str, rhs: str) -> int:
    return (int(lhs, 16) ^ int(rhs, 16)).bit_count()


def _parse_json_array(value) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [item for item in decoded if isinstance(item, str) and item]
        except json.JSONDecodeError:
            return [value]
    return []


def _merge_string_lists(existing_value, additions: list[str]) -> str | None:
    merged: list[str] = []
    for item in _parse_json_array(existing_value):
        if item not in merged:
            merged.append(item)
    for item in additions:
        if item and item not in merged:
            merged.append(item)
    return json.dumps(merged, ensure_ascii=False) if merged else None


def _merge_alias_urls(existing_aliases, original_url: str, canonical_url: str) -> str | None:
    additions = []
    raw = original_url.strip()
    if raw and raw != canonical_url:
        additions.append(raw)
    return _merge_string_lists(existing_aliases, additions)


def _parse_citation_counts(final_answer: str | None) -> dict[str, int]:
    if not final_answer:
        return {}

    counts: dict[str, int] = {}
    for bracket_content in re.findall(r"\[([^\]]+)\]", final_answer):
        matches = set(_CITATION_ID_PATTERN.findall(bracket_content))
        for source_id in matches:
            counts[source_id] = counts.get(source_id, 0) + 1
    return counts


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _get_preference_rating(
    preference_blob: dict | None,
    llm_source_id: str | None,
) -> float | None:
    if not preference_blob or not llm_source_id:
        return None
    usefulness = preference_blob.get("source_usefulness_ratings", {})
    if not isinstance(usefulness, dict):
        return None
    raw = usefulness.get(llm_source_id)
    if raw is None:
        return None
    try:
        return _clamp_unit(float(raw))
    except (TypeError, ValueError):
        return None


def _compute_llm_signal_score(
    src,
    cited_in_final: bool,
    citation_count: int,
    preference_blob: dict | None,
) -> float:
    base = 0.1
    if src.selected_as_evidence:
        base = 0.65
    if cited_in_final:
        base = 0.9
    base = max(base, min(1.0, 0.25 + min(citation_count, 3) * 0.2))
    base = max(base, _clamp_unit(0.2 + src.contribution_weight * 0.8))

    preference_rating = _get_preference_rating(preference_blob, src.source_id)
    if preference_rating is not None:
        return round(_clamp_unit(base * 0.7 + preference_rating * 0.3), 4)
    return round(_clamp_unit(base), 4)


def _compute_freshness_score(src) -> float:
    if src.discard_reason == "outdated":
        return 0.0
    if src.content_age_days is None:
        return 0.55
    age = max(0, src.content_age_days)
    if age <= 7:
        return 1.0
    if age <= 30:
        return 0.95
    if age <= 90:
        return 0.85
    if age <= 180:
        return 0.75
    if age <= 365:
        return 0.6
    if age <= 730:
        return 0.4
    return 0.2


def _compute_authority_score(src) -> float:
    base = _QUALITY_CONTENT_TYPE_SCORES.get(src.content_type or "", 0.5)
    penalty = 0.0
    penalty += src.impersonation_risk * 0.7
    if not src.accessible:
        penalty += 0.15
    if src.has_paywall:
        penalty += 0.1
    if src.has_login_wall:
        penalty += 0.1
    return round(_clamp_unit(base - penalty), 4)


def _compute_relevance_score(src, cited_in_final: bool, citation_count: int) -> float:
    base = 0.15
    if src.support_claim_ids:
        base += min(len(src.support_claim_ids), 3) * 0.15
    if src.selected_as_evidence:
        base += 0.2
    if cited_in_final:
        base += 0.2
    if citation_count > 0:
        base += min(citation_count, 3) * 0.08
    base = max(base, _clamp_unit(0.15 + src.contribution_weight * 0.7))
    return round(_clamp_unit(base), 4)


def _compute_quality_signal(
    src,
    cited_in_final: bool,
    citation_count: int,
    preference_blob: dict | None,
) -> dict:
    llm_score = _compute_llm_signal_score(
        src,
        cited_in_final,
        citation_count,
        preference_blob,
    )
    freshness_score = _compute_freshness_score(src)
    authority_score = _compute_authority_score(src)
    relevance_score = _compute_relevance_score(src, cited_in_final, citation_count)

    total_score = (
        llm_score * 0.4
        + freshness_score * 0.2
        + authority_score * 0.2
        + relevance_score * 0.2
    )
    floor = _QUALITY_DISCARD_REASON_FLOORS.get(src.discard_reason or "")
    if floor is not None:
        total_score = min(total_score, floor)
    total_score = round(_clamp_unit(total_score), 4)

    if total_score >= _QUALITY_HIGH_THRESHOLD:
        return {
            "label": "high",
            "score": total_score,
            "weight": round(max(0.1, total_score), 4),
            "components": {
                "llm": llm_score,
                "freshness": freshness_score,
                "authority": authority_score,
                "relevance": relevance_score,
            },
        }
    if total_score <= _QUALITY_LOW_THRESHOLD:
        return {
            "label": "low",
            "score": total_score,
            "weight": round(max(0.1, 1.0 - total_score), 4),
            "components": {
                "llm": llm_score,
                "freshness": freshness_score,
                "authority": authority_score,
                "relevance": relevance_score,
            },
        }
    return {
        "label": None,
        "score": total_score,
        "weight": 0.0,
        "components": {
            "llm": llm_score,
            "freshness": freshness_score,
            "authority": authority_score,
            "relevance": relevance_score,
        },
    }


def _compute_article_score(selected_as_evidence: bool, cited_in_final: bool, citation_count: int, contribution_weight: float) -> float:
    score = 0.0
    if selected_as_evidence:
        score += 1.2
    if cited_in_final:
        score += 2.0
    score += min(citation_count, 3) * 0.25
    score += contribution_weight * 1.5
    return round(min(score, 4.0), 2)


_SOURCE_TIER_RULES = {
    "official_docs": ("primary", 0),
    "official_blog": ("secondary", 1),
    "third_party": ("secondary", 1),
    "forum": ("tertiary", 2),
    "social": ("tertiary", 2),
}
_SOURCE_TIER_WEIGHTS = {"primary": 1.0, "secondary": 0.82, "tertiary": 0.64}


def _infer_source_tier(src) -> tuple[str, int]:
    return _SOURCE_TIER_RULES.get((src.content_type or "").strip().lower(), ("tertiary", 2))


def _normalize_match_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"https?://\S+", " ", str(value).lower())
    normalized = re.sub(r"[\s\W_]+", " ", normalized, flags=re.UNICODE)
    return normalized.strip()


def _extract_special_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {token.lower() for token in _SPECIAL_TOKEN_PATTERN.findall(str(text))}


def _extract_match_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    normalized = _normalize_match_text(text)
    tokens = {token for token in _LATIN_TOKEN_PATTERN.findall(normalized)}
    for segment in _CJK_SEGMENT_PATTERN.findall(normalized):
        trimmed = segment.strip()
        if len(trimmed) <= 4:
            tokens.add(trimmed)
            continue
        tokens.add(trimmed[:4])
        tokens.add(trimmed[-4:])
        for width in (2, 3):
            limit = min(len(trimmed) - width + 1, 6)
            for index in range(max(0, limit)):
                tokens.add(trimmed[index:index + width])
    return {token for token in tokens if len(token) >= 2}


def _extract_claim_slots(payload: dict) -> dict[str, str]:
    slots: dict[str, str] = {}
    for key in _CLAIM_SLOT_WEIGHTS:
        value = payload.get(key)
        if value and str(value).strip():
            slots[key] = str(value).strip()
    return slots


def _match_slot_value(slot_key: str, slot_value: str, evidence_snippet: str) -> bool:
    normalized_slot = _normalize_match_text(slot_value)
    normalized_snippet = _normalize_match_text(evidence_snippet)
    if not normalized_slot or not normalized_snippet:
        return False

    if normalized_slot in normalized_snippet:
        return True

    slot_tokens = _extract_match_tokens(slot_value)
    snippet_tokens = _extract_match_tokens(evidence_snippet)
    shared_tokens = slot_tokens.intersection(snippet_tokens)

    if slot_key in {"time", "number", "version_or_policy_name"}:
        special_slot_tokens = _extract_special_tokens(slot_value)
        special_snippet_tokens = _extract_special_tokens(evidence_snippet)
        if special_slot_tokens.intersection(special_snippet_tokens):
            return True

    if not slot_tokens:
        return False

    required_hits = 1 if len(slot_tokens) == 1 else min(2, len(slot_tokens))
    return len(shared_tokens) >= required_hits


def _compute_slot_coverage(
    claim_slots: dict[str, str],
    evidence_snippet: str | None,
    *,
    supported_slots: list[str] | None = None,
) -> dict:
    snippet = (evidence_snippet or "").strip()
    if not claim_slots or not snippet:
        return {
            "slot_coverage_score": 0.0,
            "slot_hits": {},
            "evaluated_slot_count": 0,
            "hard_anchor_hit": False,
        }

    slot_keys = [key for key in (supported_slots or list(claim_slots)) if key in claim_slots]
    if not slot_keys:
        slot_keys = list(claim_slots)

    total_weight = 0.0
    hit_weight = 0.0
    slot_hits: dict[str, bool] = {}
    hard_anchor_hit = False
    for key in slot_keys:
        slot_value = claim_slots.get(key)
        if not slot_value:
            continue
        weight = _CLAIM_SLOT_WEIGHTS.get(key, 0.1)
        total_weight += weight
        matched = _match_slot_value(key, slot_value, snippet)
        slot_hits[key] = matched
        if matched:
            hit_weight += weight
            if key in {"time", "number", "version_or_policy_name"}:
                hard_anchor_hit = True

    coverage = round(hit_weight / total_weight, 4) if total_weight > 0 else 0.0
    return {
        "slot_coverage_score": coverage,
        "slot_hits": slot_hits,
        "evaluated_slot_count": len(slot_hits),
        "hard_anchor_hit": hard_anchor_hit,
    }


def _compute_exact_match_signal(
    claim_text: str,
    evidence_snippet: str | None,
    *,
    claim_slots: dict[str, str] | None = None,
    supported_slots: list[str] | None = None,
    snippet_span_type: str | None = None,
) -> dict:
    snippet = (evidence_snippet or "").strip()
    if not claim_text.strip() or not snippet:
        return {
            "signal": False,
            "score": 0.0,
            "shared_token_count": 0,
            "shared_special_token_count": 0,
            "slot_coverage_score": 0.0,
            "slot_hits": {},
            "evaluated_slot_count": 0,
        }

    slot_result = _compute_slot_coverage(
        claim_slots or {},
        snippet,
        supported_slots=supported_slots or [],
    )
    score = 0.15 + slot_result["slot_coverage_score"] * 0.85
    score += _SNIPPET_SPAN_BONUS.get((snippet_span_type or "").strip().lower(), 0.0)
    score = round(_clamp_unit(score), 4)

    signal = (
        slot_result["evaluated_slot_count"] >= 1
        and (
            slot_result["slot_coverage_score"] >= 0.70
            or (
                slot_result["hard_anchor_hit"]
                and slot_result["slot_coverage_score"] >= 0.55
            )
        )
    )
    return {
        "signal": signal,
        "score": score if signal else round(min(score, 0.49), 4),
        "shared_token_count": 0,
        "shared_special_token_count": 0,
        "slot_coverage_score": slot_result["slot_coverage_score"],
        "slot_hits": slot_result["slot_hits"],
        "evaluated_slot_count": slot_result["evaluated_slot_count"],
    }


def _compute_verifiable_carrier_signal(src) -> dict:
    path = urlsplit(_normalize_url(src.url)).path.lower()
    score = 0.0
    if src.content_type == "official_docs":
        score += 0.70
    elif src.content_type == "official_blog":
        score += 0.40
    if path.endswith(".pdf"):
        score += 0.20
    if any(keyword in path for keyword in _VERIFIABLE_PATH_KEYWORDS):
        score += 0.15
    score += _DOCUMENT_FORM_BONUS.get((src.document_form or "").strip().lower(), 0.0)
    if src.is_official_like is True:
        score += 0.10
    elif src.is_official_like is False:
        score -= 0.05
    score += min(
        0.16,
        sum(
            _STRUCTURED_MARKER_BONUS.get(marker, 0.0)
            for marker in (src.structured_markers or [])
        ),
    )
    if src.content_date:
        score += 0.05
    if src.accessible:
        score += 0.10
    if src.has_paywall or src.has_login_wall:
        score -= 0.20
    if src.is_derivative:
        score -= 0.20
    score -= _clamp_unit(src.impersonation_risk) * 0.35
    score = round(_clamp_unit(score), 4)
    return {
        "signal": score >= 0.75,
        "score": score,
    }


def _annotate_positive_signal_edges(req, edges: list[dict], source_runtime_by_llm_id: dict[str, dict]) -> list[dict]:
    if not edges:
        return []

    claim_slots_by_id = {claim.claim_id: _extract_claim_slots(claim.model_dump()) for claim in req.claims}
    claim_numeric_facts_by_id = {
        claim.claim_id: [fact.model_dump() for fact in claim.numeric_facts]
        for claim in req.claims
    }
    _, dag_edges = _build_provenance_dag(req, edges)
    edges_by_claim: dict[str, list[dict]] = defaultdict(list)

    for edge in dag_edges:
        runtime = source_runtime_by_llm_id.get(edge.get("source_id", ""), {})
        edge["claim_slots"] = claim_slots_by_id[edge["claim_id"]]
        edge["claim_numeric_facts"] = claim_numeric_facts_by_id.get(edge["claim_id"], [])
        edge["supported_slots"] = list(edge["supported_slots"])
        edge["snippet_span_type"] = edge["snippet_span_type"]
        exact_match = _compute_exact_match_signal(
            edge.get("claim_text", ""),
            edge.get("evidence_snippet"),
            claim_slots=edge.get("claim_slots", {}),
            supported_slots=edge.get("supported_slots", []),
            snippet_span_type=edge.get("snippet_span_type"),
        )
        edge["exact_match_signal"] = exact_match["signal"]
        edge["exact_match_score"] = exact_match["score"]
        edge["slot_coverage_score"] = exact_match["slot_coverage_score"]
        edge["slot_hits"] = exact_match["slot_hits"]
        edge["verifiable_carrier_signal"] = bool(runtime.get("verifiable_carrier_signal"))
        edge["independent_consensus_signal"] = False
        numeric_eval = evaluate_numeric_edge(
            claim_id=edge["claim_id"],
            source_id=edge["source_id"],
            claim_facts=edge.get("claim_numeric_facts", []),
            evidence_facts=edge.get("numeric_facts", []),
        )
        edge["numeric_verdict"] = numeric_eval["edge_verdict"]
        edge["numeric_reason"] = numeric_eval["edge_reason"]
        edge["numeric_results"] = numeric_eval["results"]
        edge["numeric_comparable_pair_count"] = numeric_eval["comparable_pair_count"]
        edge["edge_confidence"] = _compute_edge_confidence(edge, runtime)
        edges_by_claim[edge["claim_id"]].append(edge)

    consensus_roots_by_claim: dict[str, set[str]] = {}
    for claim_id, claim_edges in edges_by_claim.items():
        support_by_root: dict[str, float] = {}
        oppose_score = 0.0
        support_score = 0.0
        for edge in claim_edges:
            edge_confidence = edge.get("edge_confidence", 0.0)
            if edge["stance"] == "oppose":
                oppose_score += edge_confidence
                continue
            if edge["stance"] not in {"support", "partial"}:
                continue
            weight = 0.5 if edge["stance"] == "partial" else 1.0
            support_score += edge_confidence * weight
            if edge_confidence < 0.55:
                continue
            root_signature = edge.get("root_signature") or edge["source_id"]
            support_by_root[root_signature] = max(support_by_root.get(root_signature, 0.0), edge_confidence)
        if len(support_by_root) >= 2 and support_score >= max(0.85, oppose_score + 0.2):
            consensus_roots_by_claim[claim_id] = set(support_by_root)

    for edge in dag_edges:
        root_signature = edge.get("root_signature") or edge["source_id"]
        if edge["stance"] in {"support", "partial"} and root_signature in consensus_roots_by_claim.get(edge["claim_id"], set()):
            edge["independent_consensus_signal"] = True
            runtime = source_runtime_by_llm_id.get(edge.get("source_id", ""), {})
            edge["edge_confidence"] = _compute_edge_confidence(edge, runtime)

    numeric_consensus_summary = aggregate_numeric_consensus(dag_edges)
    for edge in dag_edges:
        edge["numeric_consensus_states"] = []
        for item in edge.get("numeric_results", []):
            key = (edge["claim_id"], item.get("metric_signature"))
            summary = numeric_consensus_summary.get(key)
            if not summary:
                continue
            edge["numeric_consensus_states"].append(
                {
                    "metric_signature": item.get("metric_signature"),
                    "state": summary["state"],
                    "root_count": summary["root_count"],
                }
            )
    return dag_edges


def _build_source_positive_signal_counts(
    edges: list[dict],
    source_runtime_by_llm_id: dict[str, dict],
) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = {}
    for runtime in source_runtime_by_llm_id.values():
        source_pk = runtime.get("source_pk")
        if not source_pk or not runtime.get("used_in_final"):
            continue
        counts[source_pk] = {
            "verifiable_carrier_count": 1 if runtime.get("verifiable_carrier_signal") else 0,
            "exact_match_count": 0,
            "independent_consensus_count": 0,
        }

    for edge in edges:
        runtime = source_runtime_by_llm_id.get(edge.get("source_id", ""), {})
        source_pk = runtime.get("source_pk")
        if not source_pk or not runtime.get("used_in_final"):
            continue
        if edge["stance"] not in {"support", "partial"}:
            continue
        bucket = counts.setdefault(
            source_pk,
            {
                "verifiable_carrier_count": 1 if runtime.get("verifiable_carrier_signal") else 0,
                "exact_match_count": 0,
                "independent_consensus_count": 0,
            },
        )
        if edge.get("exact_match_signal"):
            bucket["exact_match_count"] = 1
        if edge.get("independent_consensus_signal"):
            bucket["independent_consensus_count"] = 1
    return counts


def _build_claim_evidence_edges(req, source_runtime_by_llm_id: dict[str, dict]) -> tuple[list[dict], str]:
    claim_text_by_id = {claim.claim_id: claim.text for claim in req.claims}
    claim_slots_by_id = {claim.claim_id: _extract_claim_slots(claim.model_dump()) for claim in req.claims}
    claim_numeric_facts_by_id = {
        claim.claim_id: [fact.model_dump() for fact in claim.numeric_facts]
        for claim in req.claims
    }
    result: list[dict] = []
    for edge in req.claim_evidence_edges:
        runtime = source_runtime_by_llm_id.get(edge.source_id, {})
        result.append(
            {
                "claim_id": edge.claim_id,
                "claim_text": claim_text_by_id[edge.claim_id],
                "source_id": edge.source_id,
                "source_pk": runtime.get("source_pk"),
                "article_id": runtime.get("article_id"),
                "source_domain": runtime.get("source_domain"),
                "stance": edge.stance,
                "evidence_snippet": edge.evidence_snippet,
                "support_score": round(edge.support_score, 4),
                "source_tier": edge.source_tier,
                "trace_depth": edge.trace_depth,
                "claim_slots": claim_slots_by_id[edge.claim_id],
                "claim_numeric_facts": claim_numeric_facts_by_id[edge.claim_id],
                "supported_slots": list(edge.supported_slots),
                "snippet_span_type": edge.snippet_span_type,
                "numeric_facts": [fact.model_dump() for fact in edge.numeric_facts],
                "used_in_final": bool(edge.used_in_final),
            }
        )
    return result, ("provided" if result else "explicit_empty")


def _compute_edge_confidence(edge: dict, source_runtime: dict | None) -> float:
    runtime = source_runtime or {}
    support_score = _clamp_unit(edge.get("support_score", 0.5))
    quality_score = _clamp_unit(runtime.get("quality_score", 0.5))
    tier_weight = _SOURCE_TIER_WEIGHTS.get(edge.get("source_tier", "tertiary"), 0.64)
    confidence = 0.20 + support_score * 0.45 + quality_score * 0.20 + tier_weight * 0.15
    if edge.get("used_in_final"):
        confidence += 0.05
    if edge.get("verifiable_carrier_signal"):
        confidence += 0.04
    if edge.get("exact_match_signal"):
        exact_match_basis = max(
            _clamp_unit(edge.get("exact_match_score", 0.0)),
            _clamp_unit(edge.get("slot_coverage_score", 0.0)),
        )
        confidence += 0.04 + min(0.06, exact_match_basis * 0.06)
    if edge.get("independent_consensus_signal"):
        confidence += 0.03
    numeric_verdict = edge.get("numeric_verdict")
    if numeric_verdict == "exact_match":
        confidence += 0.08
    elif numeric_verdict in {"range_match", "bound_satisfied"}:
        confidence += 0.05
    elif numeric_verdict == "partial_match":
        confidence += 0.02
    elif numeric_verdict == "conflict":
        confidence -= 0.10
    if not runtime.get("accessible", True):
        confidence -= 0.10
    confidence -= _clamp_unit(runtime.get("impersonation_risk", 0.0)) * 0.18
    if runtime.get("discard_reason") in {"low_quality", "unsafe", "outdated", "derivative_only"}:
        confidence -= 0.12
    elif runtime.get("discard_reason") in {"contradiction", "contradiction_unresolved", "unsupported"}:
        confidence -= 0.08
    return round(_clamp_unit(confidence), 4)


def _build_provenance_dag(req, edges: list[dict]) -> tuple[dict, list[dict]]:
    source_ids = [src.source_id for src in req.sources if src.source_id]
    if not source_ids:
        return (
            {
                "status": "not_involved",
                "document_node_count": 0,
                "claim_node_count": len(req.claims),
                "provenance_edge_count": 0,
                "cycle_detected": False,
                "root_source_count": 0,
                "root_source_ids": [],
            },
            [dict(edge) for edge in edges],
        )

    parents_by_source: dict[str, list[str]] = {}
    for relation in req.provenance_edges:
        parents_by_source.setdefault(relation.source_id, []).append(relation.parent_source_id)

    cycle_detected = False
    visiting: set[str] = set()
    root_cache: dict[str, list[str]] = {}

    def resolve_roots(source_id: str) -> list[str]:
        nonlocal cycle_detected
        if source_id in root_cache:
            return root_cache[source_id]
        if source_id in visiting:
            cycle_detected = True
            root_cache[source_id] = [source_id]
            return root_cache[source_id]
        visiting.add(source_id)
        parents = parents_by_source.get(source_id, [])
        if not parents:
            roots = [source_id]
        else:
            roots = sorted(
                {
                    root
                    for parent_id in parents
                    for root in resolve_roots(parent_id)
                }
            )
            if not roots:
                roots = [source_id]
        visiting.remove(source_id)
        root_cache[source_id] = roots
        return roots

    for source_id in source_ids:
        resolve_roots(source_id)

    root_source_ids = sorted({root for roots in root_cache.values() for root in roots})
    enriched_edges: list[dict] = []
    for edge in edges:
        roots = root_cache.get(edge["source_id"], [edge["source_id"]])
        enriched = dict(edge)
        enriched["root_source_ids"] = roots
        enriched["root_signature"] = "|".join(roots)
        enriched_edges.append(enriched)

    dag_summary = {
        "status": "ready" if req.provenance_edges else "no_provenance_edges",
        "document_node_count": len(source_ids),
        "claim_node_count": len(req.claims),
        "provenance_edge_count": len(req.provenance_edges),
        "cycle_detected": cycle_detected,
        "root_source_count": len(root_source_ids),
        "root_source_ids": root_source_ids[:12],
    }
    return dag_summary, enriched_edges


def _evaluate_claim_edges(req, edges: list[dict], edge_mode: str) -> dict:
    edges_by_claim: dict[str, list[dict]] = {}
    for edge in edges:
        edges_by_claim.setdefault(edge["claim_id"], []).append(edge)

    claim_reviews: list[dict] = []
    supported_count = 0
    refuted_count = 0
    insufficient_count = 0
    conflicted_count = 0

    for claim in req.claims:
        claim_edges = edges_by_claim.get(claim.claim_id, [])
        support_score = sum(
            edge["edge_confidence"]
            for edge in claim_edges
            if edge["stance"] == "support"
        )
        partial_score = sum(
            edge["edge_confidence"] * 0.5
            for edge in claim_edges
            if edge["stance"] == "partial"
        )
        oppose_score = sum(
            edge["edge_confidence"]
            for edge in claim_edges
            if edge["stance"] == "oppose"
        )
        effective_support = round(support_score + partial_score, 4)
        consensus_root_ids = sorted(
            {
                root_id
                for edge in claim_edges
                if edge.get("independent_consensus_signal")
                for root_id in edge.get("root_source_ids", [edge.get("source_id")])
                if root_id
            }
        )

        if effective_support < 0.35 and oppose_score < 0.35:
            verdict = "insufficient"
            insufficient_count += 1
        elif oppose_score >= max(0.85, effective_support + 0.2):
            verdict = "refuted"
            refuted_count += 1
        elif effective_support >= max(0.85, oppose_score + 0.2):
            verdict = "supported"
            supported_count += 1
        else:
            verdict = "conflicted"
            conflicted_count += 1

        top_domains = _dedupe_preserve_order(
            [edge.get("source_domain") for edge in claim_edges if edge.get("source_domain")]
        )[:3]
        root_ids = sorted(
            {
                root_id
                for edge in claim_edges
                for root_id in edge.get("root_source_ids", [edge.get("source_id")])
                if root_id
            }
        )
        margin = abs(effective_support - oppose_score)
        confidence = round(
            _clamp_unit(
                0.2
                + min(1.0, max(effective_support, oppose_score)) * 0.5
                + min(0.3, margin * 0.25)
                + (0.08 if len(consensus_root_ids) >= 2 else 0.0)
            ),
            4,
        )
        reasons: list[str] = []
        if verdict == "supported":
            reasons.append(f"支持证据强度为 {effective_support:.2f}，高于反证 {oppose_score:.2f}")
        elif verdict == "refuted":
            reasons.append(f"反证强度为 {oppose_score:.2f}，高于支持 {effective_support:.2f}")
        elif verdict == "conflicted":
            reasons.append(f"支持 {effective_support:.2f} 与反证 {oppose_score:.2f} 同时存在，当前无法单边裁定")
        else:
            reasons.append("当前缺少足够强的支持或反证，无法给出确定判断")
        if len(consensus_root_ids) >= 2:
            reasons.append(f"存在 {len(consensus_root_ids)} 个独立根来源的一致支持")
        if top_domains:
            reasons.append(f"主要涉及证据域名：{'、'.join(top_domains)}")
        numeric_review = _summarize_claim_numeric_review(claim_edges)
        if numeric_review:
            reasons.append(f"数字核验：{numeric_review['summary']}")

        claim_reviews.append(
            {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "verdict": verdict,
                "confidence": confidence,
                "support_score": round(effective_support, 4),
                "oppose_score": round(oppose_score, 4),
                "edge_count": len(claim_edges),
                "effective_root_source_count": len(root_ids),
                "root_source_ids": root_ids[:8],
                "independent_consensus": len(consensus_root_ids) >= 2,
                "top_domains": top_domains,
                "numeric_review": numeric_review,
                "reasons": reasons[:3],
            }
        )

    total_claims = len(req.claims)
    if refuted_count > 0 and refuted_count >= supported_count and refuted_count >= max(conflicted_count, insufficient_count):
        session_verdict = "refuted"
    elif conflicted_count > 0 or (supported_count > 0 and refuted_count > 0):
        session_verdict = "conflicting_evidence"
    elif supported_count == total_claims and total_claims > 0:
        session_verdict = "supported"
    elif insufficient_count == total_claims or (supported_count == 0 and refuted_count == 0 and conflicted_count == 0):
        session_verdict = "not_enough_evidence"
    elif supported_count >= max(refuted_count, insufficient_count) and supported_count / max(total_claims, 1) >= 0.6:
        session_verdict = "supported"
    else:
        session_verdict = "not_enough_evidence"

    session_confidence = round(
        sum(item["confidence"] for item in claim_reviews) / max(len(claim_reviews), 1),
        4,
    )
    return {
        "status": "ready" if edges else "limited",
        "edge_mode": edge_mode,
        "claim_count": total_claims,
        "edge_count": len(edges),
        "session_verdict": session_verdict,
        "confidence": session_confidence,
        "claim_reviews": claim_reviews,
        "summary": {
            "supported_claim_count": supported_count,
            "refuted_claim_count": refuted_count,
            "insufficient_claim_count": insufficient_count,
            "conflicted_claim_count": conflicted_count,
        },
    }


def _collapse_same_root_edges(edges: list[dict]) -> list[dict]:
    best_by_group: dict[tuple[str, str, str], dict] = {}
    for edge in edges:
        key = (
            edge["claim_id"],
            edge["stance"],
            edge.get("root_signature") or edge["source_id"],
        )
        incumbent = best_by_group.get(key)
        if incumbent is None or edge["edge_confidence"] > incumbent["edge_confidence"]:
            best_by_group[key] = edge
    return list(best_by_group.values())


def _build_counterfactual_audit(req, edges: list[dict], base_result: dict) -> dict:
    if not req.claims:
        return {
            "status": "not_involved",
            "effective_root_source_count": 0,
            "primary_source_dependency": False,
            "scenarios": {},
            "claim_flip_count": 0,
            "session_verdict_flip": False,
        }

    primary_root_ids = {
        root_id
        for edge in edges
        if edge.get("source_tier") == "primary" or edge.get("trace_depth", 9) == 0
        for root_id in edge.get("root_source_ids", [edge.get("source_id")])
        if root_id
    }
    scenarios = {
        "collapse_same_root": _collapse_same_root_edges(edges),
        "remove_primary_sources": [
            edge
            for edge in edges
            if not primary_root_ids.intersection(edge.get("root_source_ids", [edge.get("source_id")]))
        ],
        "remove_low_confidence_edges": [
            edge
            for edge in edges
            if edge.get("edge_confidence", 0.0) >= 0.55
        ],
    }

    scenario_results = {
        name: _evaluate_claim_edges(req, scenario_edges, edge_mode=name)
        for name, scenario_edges in scenarios.items()
    }
    base_reviews = {item["claim_id"]: item for item in base_result["claim_reviews"]}
    claim_flip_count = 0
    primary_source_dependency = False
    for claim in req.claims:
        base_verdict = base_reviews.get(claim.claim_id, {}).get("verdict")
        remove_primary_verdict = next(
            (
                item["verdict"]
                for item in scenario_results["remove_primary_sources"]["claim_reviews"]
                if item["claim_id"] == claim.claim_id
            ),
            None,
        )
        if remove_primary_verdict and base_verdict != remove_primary_verdict:
            claim_flip_count += 1
            if base_verdict == "supported" and remove_primary_verdict != "supported":
                primary_source_dependency = True

    session_verdict_flip = any(
        result["session_verdict"] != base_result["session_verdict"]
        for result in scenario_results.values()
    )
    effective_root_source_count = len(
        {
            root_id
            for edge in edges
            for root_id in edge.get("root_source_ids", [edge.get("source_id")])
            if root_id
        }
    )
    return {
        "status": "ready",
        "effective_root_source_count": effective_root_source_count,
        "primary_source_dependency": primary_source_dependency,
        "claim_flip_count": claim_flip_count,
        "session_verdict_flip": session_verdict_flip,
        "scenarios": {
            name: {
                "edge_count": result["edge_count"],
                "session_verdict": result["session_verdict"],
                "confidence": result["confidence"],
                "flipped": result["session_verdict"] != base_result["session_verdict"],
            }
            for name, result in scenario_results.items()
        },
    }


def _summarize_claim_numeric_review(claim_edges: list[dict]) -> dict | None:
    if not claim_edges:
        return None
    states: list[str] = []
    reasons: list[str] = []
    for edge in claim_edges:
        verdict = edge.get("numeric_verdict")
        if verdict:
            states.append(verdict)
        if edge.get("numeric_reason"):
            reasons.append(edge["numeric_reason"])
        for state in edge.get("numeric_consensus_states", []):
            states.append(state.get("state"))
    if not states:
        return None

    if "hard_conflict" in states or "conflict" in states:
        status = "conflicted"
        summary = "可比口径下的数字之间存在冲突，当前不能直接采纳单一数值。"
    elif "independent_consensus" in states or "exact_match" in states:
        status = "verified"
        summary = "关键数字拿到了可比证据支持，其中至少一部分形成了稳定一致。"
    elif "source_divergence" in states or "partial_match" in states:
        status = "weakly_verified"
        summary = "数字大体有支持，但不同来源或表达之间仍存在差异。"
    elif any(state.startswith("incomparable_") for state in states):
        status = "incomparable"
        summary = "当前数字不能直接比较，主要是指标、单位或口径并不完全一致。"
    else:
        status = "unresolved"
        summary = "当前数字证据还不够，暂时无法做稳定判断。"
    if reasons:
        summary = reasons[0]
    return {"status": status, "summary": summary}


def _apply_stability_penalty(base_confidence: float, counterfactual_audit: dict) -> float:
    penalty = 0.0
    scenarios = counterfactual_audit.get("scenarios", {})
    if scenarios.get("collapse_same_root", {}).get("flipped"):
        penalty += 0.15
    if scenarios.get("remove_primary_sources", {}).get("flipped"):
        penalty += 0.20
    if scenarios.get("remove_low_confidence_edges", {}).get("flipped"):
        penalty += 0.10
    if counterfactual_audit.get("primary_source_dependency"):
        penalty += 0.05
    return round(_clamp_unit(base_confidence - penalty), 4)


def _build_claim_verification(req, edges: list[dict], source_runtime_by_llm_id: dict[str, dict], edge_mode: str) -> dict:
    if not req.claims:
        return {
            "status": "not_involved",
            "edge_mode": edge_mode,
            "claim_count": 0,
            "edge_count": len(edges),
            "session_verdict": "not_involved",
            "confidence": 0.0,
            "stability_adjusted_confidence": 0.0,
            "claim_reviews": [],
            "summary": {
                "supported_claim_count": 0,
                "refuted_claim_count": 0,
                "insufficient_claim_count": 0,
                "conflicted_claim_count": 0,
            },
            "dag_audit": {
                "status": "not_involved",
                "document_node_count": 0,
                "claim_node_count": 0,
                "provenance_edge_count": 0,
                "cycle_detected": False,
                "root_source_count": 0,
                "root_source_ids": [],
            },
            "counterfactual_audit": {
                "status": "not_involved",
                "effective_root_source_count": 0,
                "primary_source_dependency": False,
                "scenarios": {},
                "claim_flip_count": 0,
                "session_verdict_flip": False,
            },
        }

    for edge in edges:
        enriched = dict(edge)
        runtime = source_runtime_by_llm_id.get(edge.get("source_id", ""))
        enriched["edge_confidence"] = _compute_edge_confidence(edge, runtime)
        edge.update(enriched)

    dag_audit, dag_edges = _build_provenance_dag(req, edges)
    base_result = _evaluate_claim_edges(req, dag_edges, edge_mode=edge_mode)
    counterfactual_audit = _build_counterfactual_audit(req, dag_edges, base_result)
    base_result["dag_audit"] = dag_audit
    base_result["counterfactual_audit"] = counterfactual_audit
    base_result["stability_adjusted_confidence"] = _apply_stability_penalty(
        base_result["confidence"],
        counterfactual_audit,
    )
    return base_result


def _ensure_source(cur, domain: str, category: str = "unknown") -> tuple[int, bool]:
    return repo_ensure_source(cur, _normalize_domain(domain), category)


def _upsert_daily_stats(
    cur,
    source_id: int,
    stat_date: str,
    *,
    cited: bool,
    adopted: bool,
    discarded: bool,
    contradiction: bool,
    quality_label: str | None,
    verifiable_carrier_count: int = 0,
) -> None:
    record_daily_signal(
        cur,
        source_id=source_id,
        stat_date=date.fromisoformat(stat_date),
        cited_in_final=cited,
        adopted=adopted,
        discarded=discarded,
        contradiction=contradiction,
        quality_label=quality_label,
        verifiable_carrier_count=verifiable_carrier_count,
    )


def _record_positive_signal_boosts(
    cur,
    stat_date: str,
    signal_counts: dict[int, dict[str, int]],
) -> None:
    bucket_date = date.fromisoformat(stat_date)
    for source_id, counts in signal_counts.items():
        if not counts.get("exact_match_count") and not counts.get("independent_consensus_count"):
            continue
        record_daily_signal(
            cur,
            source_id=source_id,
            stat_date=bucket_date,
            cited_in_final=False,
            adopted=False,
            discarded=False,
            contradiction=False,
            quality_label=None,
            exact_match_count=counts.get("exact_match_count", 0),
            independent_consensus_count=counts.get("independent_consensus_count", 0),
        )


def _record_contradiction(cur, source_id: int, stat_date: str) -> None:
    record_daily_contradiction(
        cur,
        source_id=source_id,
        stat_date=date.fromisoformat(stat_date),
    )


def _update_last_verified(cur, source_id: int) -> None:
    repo_update_last_verified(cur, source_id)


def _resolve_existing_article(cur, canonical_url: str, simhash_fingerprint: str):
    return repo_resolve_existing_article(
        cur,
        canonical_url=canonical_url,
        simhash_fingerprint=simhash_fingerprint,
        simhash_buckets=list(_simhash_buckets(simhash_fingerprint)),
        hamming_distance=_hex_hamming_distance,
    )


def _upsert_article_source(
    cur,
    src,
    cited_in_final: bool,
    citation_count: int,
    *,
    query: str,
    claim_text_by_id: dict[str, str],
) -> tuple[bool, str]:
    canonical_url = _normalize_url(src.url)
    derived_title = _derive_article_title(src)
    topic_tag_values = _derive_topic_tags(src, claim_text_by_id)
    derived_content_summary = _derive_content_summary(
        src,
        query=query,
        claim_text_by_id=claim_text_by_id,
        title=derived_title,
        topic_tags=topic_tag_values,
    )
    article_text = _build_article_text_from_values(
        derived_title,
        derived_content_summary,
        topic_tag_values,
        src.url,
    )
    simhash_fingerprint = _compute_simhash(article_text)
    article_id = _stable_hex(f"{canonical_url}|{simhash_fingerprint}", "article")
    topic_tags_json = json.dumps(topic_tag_values, ensure_ascii=False) if topic_tag_values else None
    interface_signature = json.dumps(
        {
            "source_id": src.source_id,
            "content_type": src.content_type,
            "http_status": src.http_status,
            "selected_as_evidence": src.selected_as_evidence,
            "cited_in_final": cited_in_final,
            "citation_count": citation_count,
            "support_claim_ids": src.support_claim_ids,
        },
        ensure_ascii=False,
    )
    content_date = _safe_content_date(src.content_date)
    article_score = _compute_article_score(
        src.selected_as_evidence,
        cited_in_final,
        citation_count,
        src.contribution_weight,
    )
    retention_reason = classify_retention_reason(
        content_type=src.content_type,
        cited_in_final=cited_in_final,
        citation_count=citation_count,
        selected_as_evidence=src.selected_as_evidence,
        contribution_weight=src.contribution_weight,
        article_score=article_score,
    )
    canonical_topic_tag = topic_tag_values[0] if topic_tag_values else None
    positive_adoption = 1 if (src.selected_as_evidence or cited_in_final) else 0
    contradiction_count = 1 if src.discard_reason in {"contradiction", "contradiction_unresolved"} else 0

    row = _resolve_existing_article(cur, canonical_url, simhash_fingerprint)
    if row:
        existing_article_id, existing_aliases, existing_tags = row
        repo_update_article_source(
            cur,
            simhash_fingerprint=simhash_fingerprint,
            merged_alias_urls_json=_merge_alias_urls(existing_aliases, src.url, canonical_url),
            derived_title=derived_title,
            merged_topic_tags_json=(
                _merge_string_lists(existing_tags, topic_tag_values) if topic_tag_values else None
            ),
            canonical_topic_tag=canonical_topic_tag,
            derived_content_summary=derived_content_summary,
            interface_signature=interface_signature,
            content_type=src.content_type,
            content_date=content_date,
            article_score=article_score,
            positive_adoption=positive_adoption,
            cited_count_increment=1 if cited_in_final else 0,
            contradiction_count=contradiction_count,
            retention_reason=retention_reason,
            existing_article_id=existing_article_id,
        )
        article_id = existing_article_id
        created = False
    else:
        repo_insert_article_source(
            cur,
            article_id=article_id,
            simhash_fingerprint=simhash_fingerprint,
            canonical_url=canonical_url,
            alias_urls_json=_merge_alias_urls(None, src.url, canonical_url),
            parent_domain=_normalize_domain(src.domain),
            title=derived_title,
            topic_tags_json=topic_tags_json,
            canonical_topic_tag=canonical_topic_tag,
            content_summary=derived_content_summary,
            interface_signature=interface_signature,
            content_type=src.content_type,
            article_score=article_score,
            positive_adoption=positive_adoption,
            content_date=content_date,
            cited_count_increment=1 if cited_in_final else 0,
            contradiction_count=contradiction_count,
            retention_reason=retention_reason,
        )
        created = True

    repo_upsert_simhash_buckets(
        cur,
        article_id=article_id,
        simhash_fingerprint=simhash_fingerprint,
        simhash_buckets=list(_simhash_buckets(simhash_fingerprint)),
    )

    return created, article_id


def _default_semantic_storage() -> dict:
    return {
        "enabled": False,
        "mode": "disabled",
        "status": "disabled",
        "missing_tables": [],
        "canonical_source_records": 0,
        "claim_records": 0,
        "provenance_cluster_records": 0,
        "typed_conflict_records": 0,
        "claim_slot_evidence_records": 0,
        "accepted_causal_edge_records": 0,
        "candidate_causal_edge_records": 0,
        "causal_gap_records": 0,
        "runtime_graph": {
            "claim_graph": {
                "status": "not_involved",
                "claim_node_count": 0,
                "evidence_edge_count": 0,
                "provenance_edge_count": 0,
                "supports_evidence_structuring": False,
                "supports_typed_conflict": False,
            },
            "causal_graph": {
                "status": "reserved",
                "candidate_edge_count": 0,
                "accepted_edge_count": 0,
                "causal_gap_count": 0,
            },
        },
    }


def _default_claim_verification() -> dict:
    return {
        "status": "not_involved",
        "edge_mode": "none",
        "claim_count": 0,
        "edge_count": 0,
        "session_verdict": "not_involved",
        "confidence": 0.0,
        "claim_reviews": [],
        "summary": {
            "supported_claim_count": 0,
            "refuted_claim_count": 0,
            "insufficient_claim_count": 0,
            "conflicted_claim_count": 0,
        },
    }


def _execute_persist(
    req: FeedbackRequest,
    *,
    citation_counts: dict,
    query_normalization: dict,
    article_summary_query: str,
    claim_text_by_id: dict,
    today: str,
) -> dict:
    """执行 research-feedback 的写路径，返回统一结果 dict（含 ok/error）。

    写路径分层：
    1. 原子事务（`get_write_transaction`）：sources 及其派生外键写
       （daily_stats / last_verified / article_source / 话题 / 内容类型 /
       llm_preference / contradiction / claim 证据边），任一失败整体回滚。
    2. 事务外 best-effort：语义存储（fail-open）与信源信誉重算（可补偿）。
    """
    processed = 0
    new_domains_registered = 0
    evidence_urls_recorded = 0
    evidence_urls_created = 0
    contradiction_count = len(req.contradictions)
    citations_verified = 0
    citation_overrides = 0
    touched_source_ids: set[int] = set()
    auto_profiled_domains: list[str] = []
    evidence_domains: list[str] = []
    key_domains: list[str] = []
    cited_domains: list[str] = []
    selected_domains: list[str] = []
    accessible_adopted_domains: list[str] = []
    inaccessible_domains: list[str] = []
    impersonation_risk_domains: list[str] = []
    paywall_domains: list[str] = []
    login_wall_domains: list[str] = []
    total_citation_count = 0
    source_runtime_by_llm_id: dict[str, dict] = {}
    source_facts_by_domain: dict[str, dict] = {}
    claim_edges: list[dict] = []
    claim_edge_mode = "none"
    edges_written = 0
    numeric_summary = None
    semantic_storage = _default_semantic_storage()
    claim_verification = _default_claim_verification()
    recalc_summary = {"requested": 0, "processed": 0, "changed": 0, "sources": []}

    try:
        log_trace_node("feedback.persist", "research-feedback persistence started")
        sync_static_taxonomy()
        with get_write_transaction(reason="feedback.persist") as conn:
            with conn.cursor() as cur:
                for src in req.sources:
                    source_id, created = _ensure_source(cur, src.domain)
                    touched_source_ids.add(source_id)
                    if created:
                        new_domains_registered += 1
                        auto_profiled_domains.append(_normalize_domain(src.domain))

                    effective_citation_count = src.citation_count
                    effective_cited_in_final = src.cited_in_final
                    if req.final_answer:
                        effective_citation_count = citation_counts.get(src.source_id, 0)
                        effective_cited_in_final = effective_citation_count > 0
                        citations_verified += 1
                        if (
                            effective_cited_in_final != src.cited_in_final
                            or effective_citation_count != src.citation_count
                        ):
                            citation_overrides += 1

                    normalized_domain = _normalize_domain(src.domain)
                    canonical_topic_tags = _derive_topic_tags(src, claim_text_by_id)
                    if src.impersonation_risk >= 0.5:
                        impersonation_risk_domains.append(normalized_domain)
                    if not src.accessible:
                        inaccessible_domains.append(normalized_domain)
                    if src.has_paywall:
                        paywall_domains.append(normalized_domain)
                    if src.has_login_wall:
                        login_wall_domains.append(normalized_domain)

                    is_discarded = src.discard_reason is not None
                    verifiable_carrier = _compute_verifiable_carrier_signal(src)
                    quality_signal = _compute_quality_signal(
                        src,
                        effective_cited_in_final,
                        effective_citation_count,
                        req.preference_blob,
                    )
                    _upsert_daily_stats(
                        cur,
                        source_id,
                        today,
                        cited=effective_cited_in_final,
                        adopted=bool(src.selected_as_evidence or effective_cited_in_final),
                        discarded=is_discarded,
                        contradiction=src.discard_reason in {"contradiction", "contradiction_unresolved"},
                        quality_label=quality_signal["label"],
                        verifiable_carrier_count=(
                            1
                            if verifiable_carrier["signal"] and bool(src.selected_as_evidence or effective_cited_in_final)
                            else 0
                        ),
                    )

                    if src.accessible:
                        _update_last_verified(cur, source_id)

                    if src.selected_as_evidence or effective_cited_in_final:
                        evidence_urls_recorded += 1
                        evidence_domains.append(normalized_domain)
                        key_domains.append(normalized_domain)
                        total_citation_count += effective_citation_count
                        if src.selected_as_evidence:
                            selected_domains.append(normalized_domain)
                        if effective_cited_in_final:
                            cited_domains.append(normalized_domain)
                        if src.accessible:
                            accessible_adopted_domains.append(normalized_domain)
                        article_created, article_id = _upsert_article_source(
                            cur,
                            src,
                            effective_cited_in_final,
                            effective_citation_count,
                            query=article_summary_query,
                            claim_text_by_id=claim_text_by_id,
                        )
                        if article_created:
                            evidence_urls_created += 1
                    else:
                        article_id = None

                    source_tier, trace_depth = _infer_source_tier(src)
                    source_runtime_by_llm_id[src.source_id] = {
                        "source_pk": source_id,
                        "article_id": article_id,
                        "source_domain": normalized_domain,
                        "quality_score": quality_signal["score"],
                        "accessible": src.accessible,
                        "impersonation_risk": src.impersonation_risk,
                        "discard_reason": src.discard_reason,
                        "used_in_final": bool(src.selected_as_evidence or effective_cited_in_final),
                        "source_tier": source_tier,
                        "trace_depth": trace_depth,
                        "verifiable_carrier_signal": verifiable_carrier["signal"],
                    }
                    existing_source_fact = source_facts_by_domain.get(normalized_domain)
                    source_facts_by_domain[normalized_domain] = {
                        "source_pk": source_id,
                        "selected_as_evidence": bool(src.selected_as_evidence) or bool(existing_source_fact and existing_source_fact["selected_as_evidence"]),
                        "cited_in_final": bool(effective_cited_in_final) or bool(existing_source_fact and existing_source_fact["cited_in_final"]),
                        "citation_count": int(effective_citation_count) + int(existing_source_fact["citation_count"] if existing_source_fact else 0),
                        "accessible": bool(src.accessible) or bool(existing_source_fact and existing_source_fact["accessible"]),
                        "impersonation_risk": max(float(src.impersonation_risk), float(existing_source_fact["impersonation_risk"]) if existing_source_fact else 0.0),
                        "has_paywall": bool(src.has_paywall) or bool(existing_source_fact and existing_source_fact["has_paywall"]),
                        "has_login_wall": bool(src.has_login_wall) or bool(existing_source_fact and existing_source_fact["has_login_wall"]),
                        "is_official_like": bool(src.is_official_like) or bool(existing_source_fact and existing_source_fact["is_official_like"]),
                        "verifiable_carrier_signal": bool(verifiable_carrier["signal"]) or bool(existing_source_fact and existing_source_fact["verifiable_carrier_signal"]),
                        "is_derivative": bool(src.is_derivative) or bool(existing_source_fact and existing_source_fact["is_derivative"]),
                        "newly_registered": bool(created) or bool(existing_source_fact and existing_source_fact["newly_registered"]),
                        "evidence_edge_count": int(existing_source_fact["evidence_edge_count"]) if existing_source_fact else 0,
                        "exact_match_edge_count": int(existing_source_fact["exact_match_edge_count"]) if existing_source_fact else 0,
                        "independent_consensus_edge_count": int(existing_source_fact["independent_consensus_edge_count"]) if existing_source_fact else 0,
                    }

                    # ---- 信誉信号统一更新（质量 + 偏好 + 话题 + 内容类型）----
                    _update_all_signals(
                        src, source_id, effective_cited_in_final,
                        effective_citation_count,
                        req.session_id, req.preference_blob,
                        query_category=query_normalization.get("query_category"),
                        quality_signal=quality_signal,
                        canonical_topic_tags=canonical_topic_tags,
                        cur=cur,
                    )

                    processed += 1

                for contradiction in req.contradictions:
                    discarded_id, created = _ensure_source(cur, contradiction.discarded_source)
                    touched_source_ids.add(discarded_id)
                    if created:
                        new_domains_registered += 1
                        auto_profiled_domains.append(_normalize_domain(contradiction.discarded_source))
                    _record_contradiction(cur, discarded_id, today)

                claim_edges, claim_edge_mode = _build_claim_evidence_edges(req, source_runtime_by_llm_id)
                log_trace_node(
                    "feedback.numeric.prepare",
                    "numeric verification inputs prepared",
                    data={
                        "claim_edge_count": len(claim_edges),
                        "claim_numeric_fact_count": sum(len(edge.get("claim_numeric_facts", [])) for edge in claim_edges),
                        "evidence_numeric_fact_count": sum(len(edge.get("numeric_facts", [])) for edge in claim_edges),
                    },
                )
                claim_edges = _annotate_positive_signal_edges(req, claim_edges, source_runtime_by_llm_id)
                log_trace_node(
                    "feedback.numeric.evaluate",
                    "numeric verification completed",
                    data={
                        "exact_match_edges": sum(1 for edge in claim_edges if edge.get("numeric_verdict") == "exact_match"),
                        "conflict_edges": sum(1 for edge in claim_edges if edge.get("numeric_verdict") == "conflict"),
                        "comparable_edges": sum(1 for edge in claim_edges if edge.get("numeric_comparable_pair_count", 0) > 0),
                    },
                )
                _record_positive_signal_boosts(
                    cur,
                    today,
                    _build_source_positive_signal_counts(claim_edges, source_runtime_by_llm_id),
                )
                edges_written = persist_claim_evidence_edges(cur, req.session_id, claim_edges)
                claim_verification = _build_claim_verification(
                    req,
                    claim_edges,
                    source_runtime_by_llm_id,
                    claim_edge_mode,
                )
                numeric_summary = _build_numeric_reasoning(
                    claim_verification.get("claim_reviews", []),
                    aggregate_numeric_consensus(claim_edges),
                )
                if numeric_summary:
                    claim_verification["numeric_reasoning"] = numeric_summary

        semantic_storage = persist_semantic_feedback_best_effort(
            req=req,
            claim_edges=claim_edges,
            claim_verification=claim_verification,
        )
        log_trace_node(
            "feedback.semantic_storage",
            "semantic storage result captured",
            data={
                "session_id": req.session_id,
                "status": semantic_storage.get("status"),
                "mode": semantic_storage.get("mode"),
                "claim_records": semantic_storage.get("claim_records"),
                "typed_conflict_records": semantic_storage.get("typed_conflict_records"),
                "accepted_causal_edge_records": semantic_storage.get("accepted_causal_edge_records"),
                "candidate_causal_edge_records": semantic_storage.get("candidate_causal_edge_records"),
                "causal_gap_records": semantic_storage.get("causal_gap_records"),
                "missing_tables": semantic_storage.get("missing_tables"),
            },
        )

        recalc_summary = recalculate_source_reputation(
            sorted(touched_source_ids),
            reason="research_feedback",
            operator="system",
        )
        for edge in claim_edges:
            domain = edge.get("source_domain")
            if not domain or domain not in source_facts_by_domain:
                continue
            source_facts_by_domain[domain]["evidence_edge_count"] += 1
            if edge.get("exact_match_signal"):
                source_facts_by_domain[domain]["exact_match_edge_count"] += 1
            if edge.get("independent_consensus_signal"):
                source_facts_by_domain[domain]["independent_consensus_edge_count"] += 1
    except Exception as exc:
        log_trace_exception(
            "feedback.persist",
            exc,
            message="research-feedback processing failed",
            data={"session_id": getattr(req, "session_id", None)},
        )
        reason = _error_text(exc)
        logger.error("Failed to process research feedback for session %s: %s", req.session_id, reason)
        return {
            "ok": False,
            "error": reason,
            "processed": processed,
            "new_domains_registered": new_domains_registered,
            "evidence_urls_recorded": evidence_urls_recorded,
            "evidence_urls_created": evidence_urls_created,
            "contradiction_count": contradiction_count,
            "citations_verified": citations_verified,
            "citation_overrides": citation_overrides,
            "total_citation_count": total_citation_count,
            "claim_edge_mode": claim_edge_mode,
            "edges_written": edges_written,
            "numeric_summary": numeric_summary,
            "semantic_storage": semantic_storage,
            "claim_verification": claim_verification,
            "recalc_summary": recalc_summary,
            "source_facts_by_domain": source_facts_by_domain,
        }

    return {
        "ok": True,
        "error": None,
        "processed": processed,
        "new_domains_registered": new_domains_registered,
        "auto_profiled_domains": auto_profiled_domains,
        "evidence_urls_recorded": evidence_urls_recorded,
        "evidence_urls_created": evidence_urls_created,
        "evidence_domains": evidence_domains,
        "contradiction_count": contradiction_count,
        "citations_verified": citations_verified,
        "citation_overrides": citation_overrides,
        "key_domains": key_domains,
        "cited_domains": cited_domains,
        "selected_domains": selected_domains,
        "accessible_adopted_domains": accessible_adopted_domains,
        "inaccessible_domains": inaccessible_domains,
        "impersonation_risk_domains": impersonation_risk_domains,
        "paywall_domains": paywall_domains,
        "login_wall_domains": login_wall_domains,
        "total_citation_count": total_citation_count,
        "claim_edge_mode": claim_edge_mode,
        "edges_written": edges_written,
        "numeric_summary": numeric_summary,
        "semantic_storage": semantic_storage,
        "claim_verification": claim_verification,
        "recalc_summary": recalc_summary,
        "source_facts_by_domain": source_facts_by_domain,
    }


def _derive_and_persist(req: FeedbackRequest) -> dict:
    """从已验证请求派生写路径输入，并执行单事务落库。

    供 write worker（异步）复用。handler（同步）路径因需在落库前记录
    semantic_preview / query_normalization，改为内联派生后直接调用 _execute_persist。
    """
    citation_counts = _parse_citation_counts(req.final_answer)
    query_normalization = _build_feedback_query_normalization(req)
    article_summary_query = (
        _resolve_feedback_query_seed(req)
        or query_normalization.get("normalized_query")
        or query_normalization.get("query_category")
        or ""
    )
    claim_text_by_id = {
        claim.claim_id: claim.text.strip()
        for claim in req.claims
        if claim.claim_id and claim.text.strip()
    }
    today = date.today().isoformat()
    return _execute_persist(
        req,
        citation_counts=citation_counts,
        query_normalization=query_normalization,
        article_summary_query=article_summary_query,
        claim_text_by_id=claim_text_by_id,
        today=today,
    )


def handler(event, _context):
    req = None
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        log_trace_node("feedback.parse", "invalid research-feedback json", level="warning")
        return _json_response(400, {"error": "invalid JSON"})

    if isinstance(body, dict):
        bind_trace_fields(
            session_id=body.get("session_id"),
            query=body.get("query"),
            final_answer=body.get("final_answer"),
        )
    log_trace_node("feedback.parse", "research-feedback json parsed")

    try:
        req = FeedbackRequest(**body)
    except ValidationError as exc:
        error_details = _format_validation_errors(exc)
        log_trace_node(
            "feedback.validate",
            "research-feedback payload validation failed",
            level="warning",
            data={
                "detail_count": len(error_details),
                "details": error_details,
                "body_shape": {
                    "type": type(body).__name__,
                    "top_level_keys": sorted(body.keys())[:20] if isinstance(body, dict) else [],
                    "source_item_count": len(body.get("sources", [])) if isinstance(body.get("sources"), list) else None,
                    "claim_item_count": len(body.get("claims", [])) if isinstance(body.get("claims"), list) else None,
                },
            },
        )
        return _json_response(
            400,
            {
                "error": "invalid_feedback_payload",
                "message": "research-feedback JSON does not match payload_version=v2 contract",
                "details": error_details,
            },
        )

    bind_trace_fields(session_id=req.session_id, query=req.query, final_answer=req.final_answer)
    log_trace_node(
        "feedback.validate",
        "research-feedback payload validated",
        data={
            "source_count": len(req.sources),
            "claim_count": len(req.claims),
            "claim_numeric_fact_count": sum(len(claim.numeric_facts) for claim in req.claims),
            "edge_numeric_fact_count": sum(len(edge.numeric_facts) for edge in req.claim_evidence_edges),
        },
    )

    citation_counts = _parse_citation_counts(req.final_answer)
    query_normalization = _build_feedback_query_normalization(req)
    article_summary_query = (
        _resolve_feedback_query_seed(req)
        or query_normalization.get("normalized_query")
        or query_normalization.get("query_category")
        or ""
    )
    log_trace_node(
        "feedback.semantic_preview",
        "research-feedback semantic preview captured",
        data=_build_feedback_semantic_preview(req, query_normalization),
    )

    if feedback_write_async_enabled():
        try:
            message_id = enqueue_feedback_write(
                req.model_dump(mode="json"),
                session_id=req.session_id,
            )
        except Exception as exc:
            log_trace_exception(
                "feedback.enqueue",
                exc,
                message="research-feedback enqueue failed, falling back to sync persist",
                data={"session_id": req.session_id},
            )
        else:
            log_trace_node(
                "feedback.enqueue",
                "research-feedback accepted for async write",
                data={
                    "session_id": req.session_id,
                    "message_id": message_id,
                    "deduplicated": message_id is None,
                },
            )
            return _json_response(
                202,
                {
                    "ok": True,
                    "accepted": True,
                    "async": True,
                    "message_id": message_id,
                    "deduplicated": message_id is None,
                    "session_id": req.session_id,
                },
            )

    provided_source_ids = {src.source_id for src in req.sources}
    unmatched_citation_ids = sorted(source_id for source_id in citation_counts if source_id not in provided_source_ids)
    claim_text_by_id = {
        claim.claim_id: claim.text.strip()
        for claim in req.claims
        if claim.claim_id and claim.text.strip()
    }
    today = date.today().isoformat()

    result = _execute_persist(
        req,
        citation_counts=citation_counts,
        query_normalization=query_normalization,
        article_summary_query=article_summary_query,
        claim_text_by_id=claim_text_by_id,
        today=today,
    )

    if not result["ok"]:
        reason = result["error"]
        record_status, record_status_lines = _build_failure_record_status(reason)
        explainability = _build_failure_explainability(reason)
        return _json_response(
            500,
            {
                "ok": False,
                "error": "feedback_persistence_failed",
                "reason": reason,
                "citation_verification_mode": "server_parsed" if req.final_answer else "client_fallback",
                "query_normalization": query_normalization,
                "citations_verified": result["citations_verified"],
                "citation_overrides": result["citation_overrides"],
                "unmatched_citation_ids": unmatched_citation_ids,
                "record_status": record_status,
                "record_status_lines": record_status_lines,
                "explainability": explainability,
                "claim_verification": {
                    "status": "failed",
                    "reason": reason,
                },
                "semantic_storage": {
                    **result["semantic_storage"],
                    "status": "failed",
                    "reason": reason,
                },
            },
        )

    processed = result["processed"]
    new_domains_registered = result["new_domains_registered"]
    auto_profiled_domains = _dedupe_preserve_order(result["auto_profiled_domains"])
    evidence_domains = _dedupe_preserve_order(result["evidence_domains"])
    key_domains = _dedupe_preserve_order(result["key_domains"])
    cited_domains = _dedupe_preserve_order(result["cited_domains"])
    selected_domains = _dedupe_preserve_order(result["selected_domains"])
    accessible_adopted_domains = _dedupe_preserve_order(result["accessible_adopted_domains"])
    inaccessible_domains = _dedupe_preserve_order(result["inaccessible_domains"])
    impersonation_risk_domains = _dedupe_preserve_order(result["impersonation_risk_domains"])
    paywall_domains = _dedupe_preserve_order(result["paywall_domains"])
    login_wall_domains = _dedupe_preserve_order(result["login_wall_domains"])
    contradiction_count = result["contradiction_count"]
    evidence_urls_recorded = result["evidence_urls_recorded"]
    evidence_urls_created = result["evidence_urls_created"]
    total_citation_count = result["total_citation_count"]
    edges_written = result["edges_written"]
    numeric_summary = result["numeric_summary"]
    claim_verification = result["claim_verification"]
    semantic_storage = result["semantic_storage"]
    recalc_summary = result["recalc_summary"]
    source_facts_by_domain = result["source_facts_by_domain"]
    claim_edge_mode = result["claim_edge_mode"]

    record_status, record_status_lines = _build_success_record_status(
        processed=processed,
        evidence_urls_recorded=evidence_urls_recorded,
        evidence_urls_created=evidence_urls_created,
        contradiction_count=contradiction_count,
        new_domains=auto_profiled_domains,
        evidence_domains=evidence_domains,
        recalc_summary=recalc_summary,
    )
    try:
        reputation_snapshot_by_pk = _fetch_source_reputation_snapshot(
            {facts["source_pk"] for facts in source_facts_by_domain.values()}
        )
    except Exception as exc:
        log_trace_exception(
            "feedback.explainability",
            exc,
            message="source reputation snapshot lookup failed",
            data={"source_count": len(source_facts_by_domain)},
        )
        reputation_snapshot_by_pk = {}
    source_cards = _build_source_cards(
        source_facts_by_domain=source_facts_by_domain,
        reputation_snapshot_by_pk=reputation_snapshot_by_pk,
    )
    explainability = _build_success_explainability(
        key_domains=key_domains,
        cited_domain_count=len(cited_domains),
        selected_domain_count=len(selected_domains),
        citation_count=total_citation_count,
        evidence_count=evidence_urls_recorded,
        accessible_adopted_domains=accessible_adopted_domains,
        auto_profiled_domains=auto_profiled_domains,
        inaccessible_domains=inaccessible_domains,
        impersonation_risk_domains=impersonation_risk_domains,
        paywall_domains=paywall_domains,
        login_wall_domains=login_wall_domains,
        contradiction_count=contradiction_count,
        unmatched_citation_ids=unmatched_citation_ids,
        recalc_summary=recalc_summary,
        source_cards=source_cards,
        numeric_summary=numeric_summary,
    )
    log_trace_node(
        "feedback.finish",
        "research-feedback processed successfully",
        data={
            "session_id": req.session_id,
            "processed_sources": processed,
            "edges_written": edges_written,
            "contradiction_count": contradiction_count,
            "claim_edge_mode": claim_edge_mode,
        },
    )

    return _json_response(
        200,
        {
            "ok": True,
            "sources_processed": processed,
            "new_domains_auto_registered": new_domains_registered,
            "new_domains_auto_profiled": auto_profiled_domains,
            "new_domains_skipped": 0,
            "evidence_urls_recorded": evidence_urls_recorded,
            "evidence_urls_created": evidence_urls_created,
            "evidence_domains": evidence_domains,
            "contradictions_recorded": contradiction_count,
            "citation_verification_mode": "server_parsed" if req.final_answer else "client_fallback",
            "query_normalization": query_normalization,
            "citations_verified": result["citations_verified"],
            "citation_overrides": result["citation_overrides"],
            "unmatched_citation_ids": unmatched_citation_ids,
            "claim_evidence_edges_recorded": edges_written,
            "reputation_recalc": {
                "processed": recalc_summary["processed"],
                "changed": recalc_summary["changed"],
            },
            "record_status": record_status,
            "record_status_lines": record_status_lines,
            "explainability": explainability,
            "claim_verification": claim_verification,
            "semantic_storage": semantic_storage,
        },
    )
