from __future__ import annotations

import re
from typing import Any


_NUMERIC_PATTERN = re.compile(r"\d")
_VALUE_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_CURRENCY_UNITS = {"cny", "rmb", "yuan", "元", "万元", "亿元", "万", "亿"}
_PERCENT_UNITS = {"%", "percent", "percentage"}
_PERCENTAGE_POINT_UNITS = {"百分点", "bp", "bps", "basis_point", "basis_points"}
_COUNT_UNITS = {"人", "户", "次", "家", "个"}
_TIME_SCOPE_ALIASES = {
    "per_year": "yearly",
    "annual": "yearly",
    "yearly": "yearly",
    "每年": "yearly",
    "per_month": "monthly",
    "monthly": "monthly",
    "每月": "monthly",
    "per_time": "per_event",
    "每次": "per_event",
    "per_person": "per_person",
    "每人": "per_person",
    "per_household": "per_household",
    "每户": "per_household",
}
_COMPARATOR_ALIASES = {
    "=": "eq",
    "eq": "eq",
    "equal": "eq",
    "equals": "eq",
    ">": "gt",
    "gt": "gt",
    ">=": "gte",
    "gte": "gte",
    "at_least": "gte",
    "不低于": "gte",
    "不少于": "gte",
    "<": "lt",
    "lt": "lt",
    "<=": "lte",
    "lte": "lte",
    "不高于": "lte",
    "不超过": "lte",
    "upper_bound": "lte",
    "上限": "lte",
    "lower_bound": "gte",
    "下限": "gte",
    "range": "range",
    "between": "range",
    "区间": "range",
    "approx": "approx",
}
_VERDICT_RANK = {
    "exact_match": 5,
    "range_match": 4,
    "bound_satisfied": 4,
    "partial_match": 3,
    "unresolved": 2,
    "conflict": 1,
}


def contains_numeric_signal(text: str | None) -> bool:
    return bool(text and _NUMERIC_PATTERN.search(text))


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().lower().split())
    return cleaned or None


def _normalize_scope(value: str | None) -> str | None:
    cleaned = _normalize_text(value)
    if not cleaned:
        return None
    return _TIME_SCOPE_ALIASES.get(cleaned, cleaned)


def _normalize_comparator(value: str | None) -> str:
    cleaned = _normalize_text(value) or "eq"
    return _COMPARATOR_ALIASES.get(cleaned, cleaned)


def _metric_signature(subject: str | None, metric: str | None) -> str:
    return " | ".join(part for part in [_normalize_text(subject), _normalize_text(metric)] if part)


def _detect_unit_family(unit: str | None) -> str:
    cleaned = _normalize_text(unit)
    if not cleaned:
        return "generic"
    if cleaned in _PERCENTAGE_POINT_UNITS:
        return "percentage_point"
    if cleaned in _PERCENT_UNITS:
        return "percent"
    if cleaned in _CURRENCY_UNITS:
        return "currency"
    if cleaned in _COUNT_UNITS:
        return "count"
    return "generic"


def _unit_scale(unit: str | None) -> float:
    cleaned = _normalize_text(unit)
    if cleaned in {"亿元", "亿"}:
        return 100000000.0
    if cleaned in {"万元", "万"}:
        return 10000.0
    return 1.0


def _parse_numeric_values(value_raw: str | None) -> dict[str, float | None]:
    text = value_raw or ""
    numbers = [float(item) for item in _VALUE_TOKEN_PATTERN.findall(text)]
    if not numbers:
        return {"value_norm": None, "range_min": None, "range_max": None}
    if len(numbers) >= 2 and re.search(r"(?:-|~|—|至|到)", text):
        return {
            "value_norm": None,
            "range_min": min(numbers[0], numbers[1]),
            "range_max": max(numbers[0], numbers[1]),
        }
    return {"value_norm": numbers[0], "range_min": None, "range_max": None}


def normalize_numeric_fact(fact: dict[str, Any]) -> dict[str, Any]:
    parsed = _parse_numeric_values(fact.get("value_raw"))
    unit = _normalize_text(fact.get("unit"))
    scale = _unit_scale(unit)
    value_norm = parsed["value_norm"]
    range_min = parsed["range_min"]
    range_max = parsed["range_max"]
    return {
        "numeric_fact_id": fact.get("numeric_fact_id"),
        "subject": fact.get("subject"),
        "metric": fact.get("metric"),
        "metric_signature": _metric_signature(fact.get("subject"), fact.get("metric")),
        "value_raw": fact.get("value_raw"),
        "value_norm": round(value_norm * scale, 6) if value_norm is not None else None,
        "range_min": round(range_min * scale, 6) if range_min is not None else None,
        "range_max": round(range_max * scale, 6) if range_max is not None else None,
        "unit": unit,
        "unit_family": _detect_unit_family(unit),
        "comparator": _normalize_comparator(fact.get("comparator")),
        "time": _normalize_text(fact.get("time")),
        "location": _normalize_text(fact.get("location")),
        "scope": _normalize_scope(fact.get("scope")),
        "evidence_span": fact.get("evidence_span"),
        "parse_status": "parsed" if value_norm is not None or range_min is not None else "missing_value",
    }


def determine_comparability(claim_fact: dict[str, Any], evidence_fact: dict[str, Any]) -> dict[str, Any]:
    if claim_fact.get("metric_signature") != evidence_fact.get("metric_signature"):
        return {"status": "incomparable_metric", "comparable": False}
    if claim_fact.get("unit_family") != evidence_fact.get("unit_family"):
        return {"status": "incomparable_unit", "comparable": False}
    if claim_fact.get("time") and evidence_fact.get("time") and claim_fact.get("time") != evidence_fact.get("time"):
        return {"status": "incomparable_time", "comparable": False}
    if claim_fact.get("location") and evidence_fact.get("location") and claim_fact.get("location") != evidence_fact.get("location"):
        return {"status": "incomparable_location", "comparable": False}
    if claim_fact.get("scope") and evidence_fact.get("scope") and claim_fact.get("scope") != evidence_fact.get("scope"):
        return {"status": "incomparable_scope", "comparable": False}
    allowed = {"eq", "gte", "lte", "range", "gt", "lt", "approx"}
    if claim_fact.get("comparator") not in allowed or evidence_fact.get("comparator") not in allowed:
        return {"status": "incomparable_comparator", "comparable": False}
    return {"status": "comparable", "comparable": True}


def _value_interval(fact: dict[str, Any]) -> tuple[float | None, float | None]:
    comparator = fact.get("comparator")
    if comparator == "range":
        return fact.get("range_min"), fact.get("range_max")
    value = fact.get("value_norm")
    return value, value


def _intervals_overlap(lhs: tuple[float | None, float | None], rhs: tuple[float | None, float | None]) -> bool:
    lhs_min, lhs_max = lhs
    rhs_min, rhs_max = rhs
    if None in lhs or None in rhs:
        return False
    return max(lhs_min, rhs_min) <= min(lhs_max, rhs_max)


def compare_numeric_facts(claim_fact: dict[str, Any], evidence_fact: dict[str, Any]) -> dict[str, Any]:
    claim_interval = _value_interval(claim_fact)
    evidence_interval = _value_interval(evidence_fact)
    claim_value = claim_fact.get("value_norm")
    evidence_value = evidence_fact.get("value_norm")
    claim_cmp = claim_fact.get("comparator")
    evidence_cmp = evidence_fact.get("comparator")
    reason = "当前数字证据不足，无法完成稳定比较"

    if claim_cmp == "eq" and evidence_cmp == "eq":
        if claim_value is None or evidence_value is None:
            return {"verdict": "unresolved", "reason": reason}
        if abs(claim_value - evidence_value) < 1e-9:
            return {"verdict": "exact_match", "reason": "同指标同口径且标准化后数值完全一致"}
        return {"verdict": "conflict", "reason": "同指标同口径但标准化后数值冲突"}

    if claim_cmp == "range":
        if evidence_value is not None and claim_interval[0] is not None and claim_interval[1] is not None:
            if claim_interval[0] <= evidence_value <= claim_interval[1]:
                return {"verdict": "range_match", "reason": "证据数值落在 claim 的区间范围内"}
            return {"verdict": "conflict", "reason": "证据数值落在 claim 区间之外"}
        if _intervals_overlap(claim_interval, evidence_interval):
            return {"verdict": "partial_match", "reason": "双方区间存在交叉，但不是完全同值"}
        return {"verdict": "conflict", "reason": "双方区间没有交叉"}

    if claim_cmp in {"gte", "gt"} and evidence_value is not None and claim_value is not None:
        if evidence_value >= claim_value:
            return {"verdict": "bound_satisfied", "reason": "证据数值满足 claim 的下限条件"}
        return {"verdict": "conflict", "reason": "证据数值低于 claim 的下限条件"}

    if claim_cmp in {"lte", "lt"} and evidence_value is not None and claim_value is not None:
        if evidence_value <= claim_value:
            return {"verdict": "bound_satisfied", "reason": "证据数值满足 claim 的上限条件"}
        return {"verdict": "conflict", "reason": "证据数值高于 claim 的上限条件"}

    if evidence_cmp == "range" and claim_value is not None and evidence_interval[0] is not None and evidence_interval[1] is not None:
        if evidence_interval[0] <= claim_value <= evidence_interval[1]:
            return {"verdict": "range_match", "reason": "claim 数值落在证据区间范围内"}
        return {"verdict": "conflict", "reason": "claim 数值落在证据区间之外"}

    if _intervals_overlap(claim_interval, evidence_interval):
        return {"verdict": "partial_match", "reason": "标准化后范围部分重叠，但不足以判定完全一致"}

    return {"verdict": "unresolved", "reason": reason}


def evaluate_numeric_edge(
    *,
    claim_id: str,
    source_id: str,
    claim_facts: list[dict[str, Any]],
    evidence_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_claim_facts = [normalize_numeric_fact(item) for item in claim_facts]
    normalized_evidence_facts = [normalize_numeric_fact(item) for item in evidence_facts]
    if not normalized_claim_facts:
        return {
            "edge_verdict": None,
            "edge_reason": None,
            "comparable_pair_count": 0,
            "results": [],
        }
    results: list[dict[str, Any]] = []
    best_rank = -1
    best_verdict = "unresolved"
    best_reason = "当前没有足够的结构化数字证据"
    incomparable_reasons: list[str] = []

    for claim_fact in normalized_claim_facts:
        best_for_claim: dict[str, Any] | None = None
        for evidence_fact in normalized_evidence_facts:
            comparability = determine_comparability(claim_fact, evidence_fact)
            if not comparability["comparable"]:
                incomparable_reasons.append(comparability["status"])
                candidate = {
                    "claim_id": claim_id,
                    "source_id": source_id,
                    "claim_numeric_fact_id": claim_fact.get("numeric_fact_id"),
                    "evidence_numeric_fact_id": evidence_fact.get("numeric_fact_id"),
                    "comparability": comparability["status"],
                    "verdict": "unresolved",
                    "reason": "当前数字与 claim 不是同一可比较口径",
                    "metric_signature": claim_fact.get("metric_signature"),
                    "claim_fact": claim_fact,
                    "evidence_fact": evidence_fact,
                }
            else:
                comparison = compare_numeric_facts(claim_fact, evidence_fact)
                candidate = {
                    "claim_id": claim_id,
                    "source_id": source_id,
                    "claim_numeric_fact_id": claim_fact.get("numeric_fact_id"),
                    "evidence_numeric_fact_id": evidence_fact.get("numeric_fact_id"),
                    "comparability": comparability["status"],
                    "verdict": comparison["verdict"],
                    "reason": comparison["reason"],
                    "metric_signature": claim_fact.get("metric_signature"),
                    "claim_fact": claim_fact,
                    "evidence_fact": evidence_fact,
                }
            if best_for_claim is None or _VERDICT_RANK[candidate["verdict"]] > _VERDICT_RANK[best_for_claim["verdict"]]:
                best_for_claim = candidate
        if best_for_claim is None:
            best_for_claim = {
                "claim_id": claim_id,
                "source_id": source_id,
                "claim_numeric_fact_id": claim_fact.get("numeric_fact_id"),
                "evidence_numeric_fact_id": None,
                "comparability": "unresolved",
                "verdict": "unresolved",
                "reason": "当前证据边没有对应的结构化数字事实",
                "metric_signature": claim_fact.get("metric_signature"),
                "claim_fact": claim_fact,
                "evidence_fact": None,
            }
        results.append(best_for_claim)
        rank = _VERDICT_RANK.get(best_for_claim["verdict"], 0)
        if rank > best_rank:
            best_rank = rank
            best_verdict = best_for_claim["verdict"]
            best_reason = best_for_claim["reason"]

    if best_verdict == "unresolved" and results:
        unique_incomparable = sorted(set(incomparable_reasons))
        if unique_incomparable and len(unique_incomparable) == len(results):
            best_reason = f"当前数字事实不可直接比较：{', '.join(unique_incomparable)}"

    comparable_count = sum(1 for item in results if item.get("comparability") == "comparable")
    return {
        "edge_verdict": best_verdict,
        "edge_reason": best_reason,
        "comparable_pair_count": comparable_count,
        "results": results,
    }


def aggregate_numeric_consensus(edges: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        for item in edge.get("numeric_results", []):
            metric_signature = item.get("metric_signature")
            if not metric_signature:
                continue
            grouped.setdefault((edge["claim_id"], metric_signature), []).append(
                {
                    "root_signature": edge.get("root_signature") or edge.get("source_id"),
                    "source_id": edge.get("source_id"),
                    "source_domain": edge.get("source_domain"),
                    "verdict": item.get("verdict"),
                    "comparability": item.get("comparability"),
                    "claim_fact": item.get("claim_fact"),
                    "evidence_fact": item.get("evidence_fact"),
                }
            )

    summary: dict[tuple[str, str], dict[str, Any]] = {}
    for key, items in grouped.items():
        unique_roots = {item["root_signature"] for item in items if item.get("root_signature")}
        if len(unique_roots) < 2:
            state = "insufficient_numeric_evidence"
        else:
            comparable_items = [item for item in items if item.get("comparability") == "comparable"]
            exact_values = {
                (item["evidence_fact"] or {}).get("value_norm")
                for item in comparable_items
                if (item["evidence_fact"] or {}).get("value_norm") is not None
            }
            conflict_present = any(item.get("verdict") == "conflict" for item in comparable_items)
            if conflict_present:
                state = "hard_conflict"
            elif len(exact_values) == 1 and comparable_items:
                state = "independent_consensus"
            elif len(exact_values) > 1:
                state = "source_divergence"
            elif len({item["root_signature"] for item in items}) < len(items):
                state = "same_root_duplicate"
            else:
                state = "insufficient_numeric_evidence"
        summary[key] = {
            "claim_id": key[0],
            "metric_signature": key[1],
            "state": state,
            "root_count": len(unique_roots),
            "items": items,
        }
    return summary
