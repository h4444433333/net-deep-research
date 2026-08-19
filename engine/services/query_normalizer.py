from __future__ import annotations

import re
import unicodedata
from typing import Mapping


_SPACE_RE = re.compile(r"\s+")
_PUNCT_REPEAT_RE = re.compile(r"([?!,.，。？！；;：:])\1+")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+){1,3}\b", re.IGNORECASE)

_REGION_PATTERNS = [
    ("beijing", re.compile(r"北京")),
    ("shanghai", re.compile(r"上海")),
    ("guangdong", re.compile(r"广东")),
    ("china", re.compile(r"全国|国家|我国|中国")),
]

_SUBJECT_PATTERNS = [
    ("flexible_employment_social_security", re.compile(r"灵活就业.*(社保|参保)|灵活就业人员")),
    ("resident_medical_insurance", re.compile(r"居民医保|城乡居民医保")),
    ("resident_pension", re.compile(r"居民养老保险|城乡居民养老")),
    ("personal_social_security", re.compile(r"个人社保|职工社保|社会保险")),
    ("delayed_retirement", re.compile(r"延迟退休|弹性退休")),
    ("medical_insurance", re.compile(r"医保|医疗保险")),
]

_INTENT_PATTERNS = [
    ("payment_standard_lookup", re.compile(r"缴费|标准|多少钱|费用|基数")),
    ("eligibility_check", re.compile(r"条件|资格|能否|可以吗|能不能|适用人群")),
    ("rule_lookup", re.compile(r"流程|规则|办理|口径|材料|怎么交|怎么办")),
    ("policy_confirmation", re.compile(r"政策|规定|办法|通知|落地|实施|确定")),
]

_TIME_PATTERNS = [
    ("latest", re.compile(r"最新|当前|现在|目前")),
    ("future", re.compile(r"未来|以后|后续")),
]

_TARGET_CAPABILITY_PATTERNS = [
    (re.compile(r"支持|兼容|是否有|能否|可以"), "support_or_compatibility"),
    (re.compile(r"怎么做|如何做|实现|集成|部署"), "implementation"),
]

_SUBJECT_TAGS = {
    "flexible_employment_social_security": ["policy_regulation", "finance_insurance", "business_hr"],
    "resident_medical_insurance": ["policy_regulation", "policy_health", "finance_insurance"],
    "resident_pension": ["policy_regulation", "finance_insurance", "business_hr"],
    "personal_social_security": ["policy_regulation", "finance_insurance", "business_hr"],
    "delayed_retirement": ["policy_regulation", "business_hr"],
    "medical_insurance": ["policy_regulation", "policy_health", "finance_insurance"],
}

_INTENT_TAGS = {
    "payment_standard_lookup": ["business_pricing"],
    "eligibility_check": ["policy_regulation"],
    "rule_lookup": ["policy_regulation"],
    "policy_confirmation": ["policy_regulation"],
}


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("“", "\"").replace("”", "\"").replace("’", "'")
    text = _SPACE_RE.sub(" ", text).strip()
    text = _PUNCT_REPEAT_RE.sub(r"\1", text)
    text = re.sub(r"\s*([?!,.，。？！；;：:])\s*", r"\1", text)
    return text


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _clean_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _normalize_text(value)
    return cleaned or None


class QueryNormalizer:
    def normalize(
        self,
        raw_query: str,
        provided: Mapping[str, object] | None = None,
    ) -> dict:
        provided = provided or {}
        raw_value = _normalize_text(raw_query)
        normalized_query = _clean_optional_text(provided.get("normalized_query")) or raw_value

        region_scope = self._prefer_string(provided, "region_scope") or self._extract_region_scope(normalized_query)
        time_scope = self._prefer_string(provided, "time_scope") or self._extract_time_scope(normalized_query)
        version_scope = self._prefer_string(provided, "version_scope") or self._extract_version_scope(normalized_query)
        subject = self._prefer_string(provided, "subject") or self._extract_subject(normalized_query)
        target_capability = self._prefer_string(provided, "target_capability") or self._extract_target_capability(normalized_query)
        intent_type = self._prefer_string(provided, "intent_type") or self._extract_intent(normalized_query)
        query_category = self._prefer_string(provided, "query_category") or self._derive_query_category(
            intent_type=intent_type,
            normalized_query=normalized_query,
        )

        topic_tags = self._build_topic_tags(
            explicit_tags=provided.get("topic_tags"),
            subject=subject,
            intent_type=intent_type,
        )

        return {
            "raw_query": raw_value,
            "normalized_query": normalized_query,
            "subject": subject,
            "target_capability": target_capability,
            "time_scope": time_scope,
            "region_scope": region_scope,
            "version_scope": version_scope,
            "intent_type": intent_type,
            "query_category": query_category,
            "topic_tags": topic_tags,
        }

    def _prefer_string(self, provided: Mapping[str, object], key: str) -> str | None:
        return _clean_optional_text(provided.get(key))

    def _extract_region_scope(self, query: str) -> str | None:
        for label, pattern in _REGION_PATTERNS:
            if pattern.search(query):
                return label
        return None

    def _extract_time_scope(self, query: str) -> str | None:
        year_match = _YEAR_RE.search(query)
        if year_match:
            return f"year_{year_match.group(1)}"
        for label, pattern in _TIME_PATTERNS:
            if pattern.search(query):
                return label
        return None

    def _extract_version_scope(self, query: str) -> str | None:
        version_match = _VERSION_RE.search(query)
        if version_match:
            return version_match.group(0).lower()
        return None

    def _extract_subject(self, query: str) -> str | None:
        for label, pattern in _SUBJECT_PATTERNS:
            if pattern.search(query):
                return label
        return None

    def _extract_target_capability(self, query: str) -> str | None:
        for pattern, label in _TARGET_CAPABILITY_PATTERNS:
            if pattern.search(query):
                return label
        return None

    def _extract_intent(self, query: str) -> str:
        for label, pattern in _INTENT_PATTERNS:
            if pattern.search(query):
                return label
        return "general_lookup"

    def _derive_query_category(self, *, intent_type: str, normalized_query: str) -> str:
        if "政策" in normalized_query or intent_type in {"policy_confirmation", "rule_lookup", "eligibility_check"}:
            return "policy"
        if intent_type == "payment_standard_lookup":
            return "pricing"
        if "docs" in normalized_query.lower():
            return "docs"
        return "general"

    def _build_topic_tags(
        self,
        *,
        explicit_tags: object,
        subject: str | None,
        intent_type: str,
    ) -> list[str]:
        tags: list[str] = []
        if subject:
            tags.extend(_SUBJECT_TAGS.get(subject, []))
        tags.extend(_INTENT_TAGS.get(intent_type, []))
        if isinstance(explicit_tags, list):
            for raw_tag in explicit_tags:
                if isinstance(raw_tag, str):
                    cleaned = raw_tag.strip()
                    if cleaned:
                        tags.append(cleaned)
        return _dedupe(tags)[:8]


def normalize_query(raw_query: str, provided: Mapping[str, object] | None = None) -> dict:
    return QueryNormalizer().normalize(raw_query, provided)
