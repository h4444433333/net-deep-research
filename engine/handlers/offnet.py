"""
POST /v1/offnet-analysis

当 skill 本次研究未实际抓取外部网页时，自动回退到这个端点。
核验 LLM 回答自身的支撑结构与风险信号，不假装离线证明外部事实真伪。

注意：此端点不作为独立用户命令暴露，始终由 skill 在无源情况下自动调用。
"""

from __future__ import annotations

import json
import re

from models.source import OffnetAnalysisRequest
from pydantic import ValidationError
from utils.logger import get_logger
from utils.request_trace import bind_trace_fields, log_trace_node

logger = get_logger("offnet")

_ABSOLUTE_TERMS = ("一定", "必然", "绝对", "100%", "唯一", "完全", "无疑", "肯定")
_UNCERTAINTY_TERMS = ("可能", "或许", "大概率", "需进一步核实", "仅供参考", "视情况", "倾向于", "尚不能")
_COUNTERARGUMENT_TERMS = ("但", "不过", "然而", "另一方面", "同时", "也可能", "例外")
_TIME_SENSITIVE_TERMS = ("目前", "当前", "最新", "今年", "本月", "近日", "截止", "截至", "刚刚")
_YEAR_PATTERN = re.compile(r"20\d{2}")
_NUMERIC_PATTERN = re.compile(r"\d+(?:\.\d+)?%?")
_URL_PATTERN = re.compile(r"https?://", re.I)
_CITATION_PATTERN = re.compile(r"\[[^\]]+\]")


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


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _detect_answer_signals(req: OffnetAnalysisRequest) -> dict:
    answer_text = req.answer_text
    return {
        "has_external_citations": (
            req.answer_signals.has_external_citations
            or bool(_URL_PATTERN.search(answer_text))
            or bool(_CITATION_PATTERN.search(answer_text))
        ),
        "has_uncertainty_disclosure": (
            req.answer_signals.has_uncertainty_disclosure
            or _contains_any(answer_text, _UNCERTAINTY_TERMS)
        ),
        "has_counterarguments": (
            req.answer_signals.has_counterarguments
            or _contains_any(answer_text, _COUNTERARGUMENT_TERMS)
        ),
        "has_structured_reasoning": (
            req.answer_signals.has_structured_reasoning
            or any(marker in answer_text for marker in ("1.", "2.", "- ", "首先", "其次", "最后", "结论"))
        ),
        "absolute_expression_detected": _contains_any(answer_text, _ABSOLUTE_TERMS),
        "time_sensitive_claim_detected": (
            _contains_any(answer_text, _TIME_SENSITIVE_TERMS)
            or bool(_YEAR_PATTERN.search(answer_text))
        ),
    }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return round(max(low, min(high, value)), 2)


def _analyze_claim(claim, *, answer_signals: dict) -> dict:
    text = claim.text
    evidence_count = len(claim.supporting_evidence)
    basis_count = len(claim.source_basis)
    risk_flags = list(claim.risk_flags)
    reasons: list[str] = []

    if evidence_count == 0 and basis_count == 0 and "no_supporting_basis" not in risk_flags:
        risk_flags.append("no_supporting_basis")
    if _contains_any(text, _ABSOLUTE_TERMS) and "absolute_expression" not in risk_flags:
        risk_flags.append("absolute_expression")
    if _NUMERIC_PATTERN.search(text) and evidence_count == 0 and "numeric_without_support" not in risk_flags:
        risk_flags.append("numeric_without_support")
    if (
        (_contains_any(text, _TIME_SENSITIVE_TERMS) or _YEAR_PATTERN.search(text))
        and evidence_count == 0
        and "time_sensitive_without_support" not in risk_flags
    ):
        risk_flags.append("time_sensitive_without_support")
    if claim.confidence is not None and claim.confidence >= 0.9 and evidence_count == 0 and "overconfident_without_support" not in risk_flags:
        risk_flags.append("overconfident_without_support")

    score = 0.12
    if evidence_count > 0:
        score += 0.32
        reasons.append(f"提供了 {evidence_count} 条支撑证据")
    if evidence_count >= 2:
        score += 0.10
    if basis_count > 0:
        score += 0.22
        reasons.append(f"声明了 {basis_count} 个支撑依据来源")
    if answer_signals["has_external_citations"]:
        score += 0.08
    if answer_signals["has_uncertainty_disclosure"]:
        score += 0.08
    if answer_signals["has_counterarguments"]:
        score += 0.05
    if answer_signals["has_structured_reasoning"]:
        score += 0.03

    if "no_supporting_basis" in risk_flags:
        score -= 0.30
        reasons.append("没有给出可核对的支撑依据")
    if "absolute_expression" in risk_flags and not answer_signals["has_uncertainty_disclosure"]:
        score -= 0.12
        reasons.append("表述过满，但缺少不确定性说明")
    if "numeric_without_support" in risk_flags:
        score -= 0.12
        reasons.append("包含数字或比例，但没有配套证据")
    if "time_sensitive_without_support" in risk_flags:
        score -= 0.12
        reasons.append("包含时间敏感判断，但没有支撑材料")
    if "overconfident_without_support" in risk_flags:
        score -= 0.12
        reasons.append("自评置信度过高，但缺少证据")

    score = _clamp(score)

    if score >= 0.75:
        verdict = "support_strong"
    elif score >= 0.55:
        verdict = "support_partial"
    elif score >= 0.35:
        verdict = "support_weak"
    else:
        verdict = "unsupported"

    if not reasons:
        reasons.append("该 claim 缺少足够结构化信息，当前只能给出保守判断")

    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "verdict": verdict,
        "score": score,
        "evidence_count": evidence_count,
        "basis_count": basis_count,
        "risk_flags": risk_flags,
        "reasons": reasons[:4],
    }


def _build_recommendations(claim_reviews: list[dict], *, answer_signals: dict) -> list[str]:
    recommendations: list[str] = []
    unsupported_count = sum(1 for item in claim_reviews if item["verdict"] == "unsupported")
    weak_count = sum(1 for item in claim_reviews if item["verdict"] == "support_weak")

    if unsupported_count > 0:
        recommendations.append("先给 unsupported 的 claim 补充可核对证据，再保留结论性表述。")
    if weak_count > 0:
        recommendations.append('对支撑偏弱的 claim 明确区分"事实""推断""建议"，不要混写。')
    if not answer_signals["has_uncertainty_disclosure"]:
        recommendations.append("对时间敏感或高风险判断补一句限制说明，避免把推断写成确定事实。")
    if not answer_signals["has_external_citations"]:
        recommendations.append("若后续允许补材料，优先补官方来源、原始发布主体或可解析引用。")

    seen: set[str] = set()
    result: list[str] = []
    for item in recommendations:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result[:4]


def _summarize_overall(claim_reviews: list[dict], *, answer_signals: dict) -> tuple[str, str, list[str]]:
    unsupported_count = sum(1 for item in claim_reviews if item["verdict"] == "unsupported")
    weak_count = sum(1 for item in claim_reviews if item["verdict"] == "support_weak")
    strong_count = sum(1 for item in claim_reviews if item["verdict"] == "support_strong")

    basis: list[str] = ["本次是离线核验，只判断回答的支撑结构与风险，不直接证明外部事实真伪。"]
    if strong_count > 0:
        basis.append(f"有 {strong_count} 条 claim 具备相对完整的支撑结构。")
    if answer_signals["has_uncertainty_disclosure"]:
        basis.append("答案中包含限制或不确定性表达，降低了过度断言风险。")
    if answer_signals["has_external_citations"]:
        basis.append("答案文本中出现了可进一步追溯的引用或链接线索。")

    if unsupported_count > 0:
        risk_level = "high"
        credibility_band = "low"
    elif weak_count > 0:
        risk_level = "medium"
        credibility_band = "medium"
    elif strong_count == len(claim_reviews) and answer_signals["has_external_citations"]:
        risk_level = "low"
        credibility_band = "high"
    else:
        risk_level = "medium"
        credibility_band = "medium"

    return risk_level, credibility_band, basis[:3]


def handler(event, context):
    if event.get("httpMethod") != "POST":
        return _json_response(405, {"error": "method not allowed"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        log_trace_node("offnet.parse", "invalid offnet-analysis json", level="warning")
        return _json_response(400, {"error": "invalid JSON"})

    if isinstance(body, dict):
        bind_trace_fields(
            session_id=body.get("session_id"),
            query=body.get("query"),
            answer_text=body.get("answer_text"),
        )
    log_trace_node("offnet.parse", "offnet-analysis json parsed")

    try:
        req = OffnetAnalysisRequest(**body)
    except ValidationError as exc:
        log_trace_node(
            "offnet.validate",
            "offnet-analysis payload validation failed",
            level="warning",
            data={"details": _format_validation_errors(exc)},
        )
        return _json_response(
            400,
            {
                "error": "invalid_offnet_payload",
                "message": "offnet-analysis JSON does not match payload_version=v2 contract",
                "details": _format_validation_errors(exc),
            },
        )

    bind_trace_fields(session_id=req.session_id, query=req.query, answer_text=req.answer_text)
    log_trace_node(
        "offnet.validate",
        "offnet-analysis payload validated",
        data={"claim_count": len(req.claims)},
    )

    answer_signals = _detect_answer_signals(req)
    claim_reviews = [
        _analyze_claim(claim, answer_signals=answer_signals) for claim in req.claims
    ]
    risk_level, credibility_band, adoption_basis = _summarize_overall(
        claim_reviews, answer_signals=answer_signals,
    )
    recommendations = _build_recommendations(claim_reviews, answer_signals=answer_signals)

    unsupported_count = sum(1 for item in claim_reviews if item["verdict"] == "unsupported")
    weak_count = sum(1 for item in claim_reviews if item["verdict"] == "support_weak")

    logger.info(
        "offnet analysis finished: session=%s claims=%d unsupported=%d risk=%s",
        req.session_id,
        len(claim_reviews),
        unsupported_count,
        risk_level,
    )
    log_trace_node(
        "offnet.finish",
        "offnet-analysis processed successfully",
        data={
            "session_id": req.session_id,
            "claim_count": len(claim_reviews),
            "unsupported_claim_count": unsupported_count,
            "risk_level": risk_level,
        },
    )

    return _json_response(
        200,
        {
            "analysis_mode": "offnet",
            "session_id": req.session_id,
            "claim_count": len(claim_reviews),
            "unsupported_claim_count": unsupported_count,
            "weak_claim_count": weak_count,
            "risk_level": risk_level,
            "credibility_band": credibility_band,
            "claim_reviews": claim_reviews,
            "recommendations": recommendations,
            "structured_summary": {
                "adoption_basis": adoption_basis,
                "limitation": (
                    "当前离线核验只能检查支撑结构、表述风险和自洽性，不能替代联网事实核查。"
                ),
                "risk_status": {
                    "level": risk_level,
                    "unsupported_claim_count": unsupported_count,
                    "weak_claim_count": weak_count,
                },
            },
        },
    )
