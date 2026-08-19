"""
请求与响应的数据模型。

使用 pydantic 做请求校验，拒绝非法输入。
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ALLOWED_CONTENT_TYPES = {
    "official_docs",
    "official_blog",
    "third_party",
    "forum",
    "social",
}
_ALLOWED_DISCARD_REASONS = {
    "contradiction",
    "contradiction_unresolved",
    "derivative_only",
    "unsafe",
    "low_quality",
    "outdated",
    "unsupported",
}
_ALLOWED_EDGE_STANCES = {"support", "oppose", "partial"}
_ALLOWED_SOURCE_TIERS = {"primary", "secondary", "tertiary"}
_ALLOWED_PROVENANCE_RELATIONS = {"derived_from"}
_ALLOWED_DOCUMENT_FORMS = {
    "article_page",
    "official_notice",
    "other",
    "pdf",
    "policy_page",
    "release_note",
    "spec_page",
    "table_page",
}
_ALLOWED_STRUCTURED_MARKERS = {"date", "identifier", "table", "version"}
_ALLOWED_CLAIM_SLOT_KEYS = {
    "action",
    "location",
    "number",
    "subject",
    "time",
    "version_or_policy_name",
}
_ALLOWED_CONFLICT_SLOT_KEYS = _ALLOWED_CLAIM_SLOT_KEYS | {"claim"}
_ALLOWED_SNIPPET_SPAN_TYPES = {"original_sentence", "summary", "table_cell", "title"}
_ALLOWED_NUMERIC_COMPARATORS = {"eq", "gt", "gte", "lt", "lte", "range", "approx"}
_ALLOWED_CONFLICT_TYPES = {
    "derivative_conflict",
    "logical_conflict",
    "temporal_conflict",
    "value_conflict",
}
_ALLOWED_CONFLICT_SEVERITIES = {"low", "medium", "high"}
_ALLOWED_CAUSAL_RELATION_TYPES = {"caused", "influenced", "precedent_for"}
_ALLOWED_CAUSAL_GAP_TYPES = {
    "insufficient_independent_support",
    "missing_mechanism",
    "missing_time_anchor",
}


def _strip_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_domain(value: str) -> str:
    candidate = value.strip().lower()
    if "://" in candidate or "/" in candidate:
        parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
        candidate = (parsed.hostname or "").strip().lower()
    if candidate.startswith("www."):
        candidate = candidate[4:]
    candidate = candidate.strip(".")
    if not candidate or "." not in candidate or " " in candidate:
        raise ValueError("domain must be a bare hostname such as example.com")
    return candidate[:255]


def _validate_http_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("url must not be empty")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("url must be a valid http or https URL")
    return candidate[:2048]


def _dedupe_string_list(values: list[str], *, lower: bool = False, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        normalized = cleaned.lower() if lower else cleaned
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if limit is not None and len(result) >= limit:
            break
    return result


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceResponse(StrictModel):
    """GET /sources?domain=X 的响应"""

    domain: str
    reputation_score: float
    confidence: float
    authority_base: int
    category: str
    subcategory: Optional[str] = None
    status: str
    trust_votes: int
    untrust_votes: int


class VoteRequest(StrictModel):
    """POST /vote 的请求体"""

    domain: str = Field(..., min_length=3, max_length=255)
    vote: str = Field(..., pattern="^(trust|untrust)$")
    target_url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("domain")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return _normalize_domain(v)

    @field_validator("target_url")
    @classmethod
    def strip_target_url(cls, v: Optional[str]) -> Optional[str]:
        return _strip_optional_text(v)


class SearchRequest(StrictModel):
    """GET /sources/search?category=X&min_score=Y 的查询参数"""

    category: Optional[str] = None
    min_score: Optional[float] = Field(default=1.0, ge=0.0, le=2.0)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class FeedbackSource(StrictModel):
    """research-feedback 中单个信源的反馈数据"""

    source_id: str = Field(..., min_length=5, max_length=64, pattern=r"^src_[A-Za-z0-9_-]+$")
    url: str = Field(..., min_length=1, max_length=2048)
    domain: str = Field(..., min_length=3, max_length=255)
    title: Optional[str] = Field(default=None, max_length=1024)
    content_summary: Optional[str] = None
    topic_tags: list[str] = Field(default_factory=list)
    accessible: bool = True
    http_status: Optional[int] = None
    content_type: str
    content_date: Optional[str] = None
    content_age_days: Optional[int] = None
    impersonation_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    has_paywall: bool = False
    has_login_wall: bool = False
    quality_signals: Optional[dict[str, Any]] = None
    document_form: str
    is_official_like: bool
    structured_markers: list[str] = Field(..., min_length=1)
    is_derivative: bool
    selected_as_evidence: bool = False
    cited_in_final: bool = False
    citation_count: int = Field(default=0, ge=0)
    contribution_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    support_claim_ids: list[str] = Field(default_factory=list)
    discard_reason: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_http_url(v)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, v: str) -> str:
        return _normalize_domain(v)

    @field_validator(
        "title",
        "content_summary",
        "content_type",
        "content_date",
        "discard_reason",
        "document_form",
        mode="before",
    )
    @classmethod
    def clean_optional_text_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.lower()
        if normalized not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"content_type must be one of {sorted(_ALLOWED_CONTENT_TYPES)}")
        return normalized

    @field_validator("discard_reason")
    @classmethod
    def validate_discard_reason(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.lower()
        if normalized not in _ALLOWED_DISCARD_REASONS:
            raise ValueError(f"discard_reason must be one of {sorted(_ALLOWED_DISCARD_REASONS)}")
        return normalized

    @field_validator("document_form")
    @classmethod
    def validate_document_form(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.lower()
        if normalized not in _ALLOWED_DOCUMENT_FORMS:
            raise ValueError(f"document_form must be one of {sorted(_ALLOWED_DOCUMENT_FORMS)}")
        return normalized

    @field_validator("topic_tags")
    @classmethod
    def normalize_topic_tags(cls, v: list[str]) -> list[str]:
        return _dedupe_string_list(v, lower=True, limit=8)

    @field_validator("structured_markers")
    @classmethod
    def normalize_structured_markers(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, lower=True, limit=8)
        unknown = sorted({item for item in normalized if item not in _ALLOWED_STRUCTURED_MARKERS})
        if unknown:
            raise ValueError(
                f"structured_markers must be drawn from {sorted(_ALLOWED_STRUCTURED_MARKERS)}"
            )
        return normalized

    @field_validator("support_claim_ids")
    @classmethod
    def normalize_support_claim_ids(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, limit=16)
        for claim_id in normalized:
            if not claim_id.startswith("c"):
                raise ValueError("support_claim_ids must use canonical claim ids like c1")
        return normalized


class ContradictionItem(StrictModel):
    """research-feedback 中的矛盾记录"""

    claim: str
    source_a: str
    source_b: str
    resolution: str
    discarded_source: str

    @field_validator("claim", "resolution")
    @classmethod
    def clean_text_fields(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("text fields must not be empty")
        return cleaned

    @field_validator("source_a", "source_b", "discarded_source")
    @classmethod
    def normalize_domains(cls, v: str) -> str:
        return _normalize_domain(v)


class ClaimItem(StrictModel):
    """research-feedback 中的 claim 与 source 映射"""

    claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    text: str = Field(..., min_length=1, max_length=4096)
    subject: str = Field(..., min_length=1, max_length=512)
    action: str = Field(..., min_length=1, max_length=512)
    time: Optional[str] = Field(default=None, max_length=256)
    location: Optional[str] = Field(default=None, max_length=256)
    number: Optional[str] = Field(default=None, max_length=256)
    version_or_policy_name: Optional[str] = Field(default=None, max_length=512)
    numeric_facts: list["NumericFactItem"] = Field(default_factory=list)
    supported_by: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def clean_text(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("claim text must not be empty")
        return cleaned

    @field_validator(
        "subject",
        "action",
        "time",
        "location",
        "number",
        "version_or_policy_name",
        mode="before",
    )
    @classmethod
    def clean_optional_slot_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("supported_by")
    @classmethod
    def normalize_supported_by(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, limit=32)
        for source_id in normalized:
            if not source_id.startswith("src_"):
                raise ValueError("supported_by must use canonical source ids like src_001")
        return normalized

    @model_validator(mode="after")
    def validate_required_slot_coverage(self) -> "ClaimItem":
        if not any([self.time, self.location, self.number, self.version_or_policy_name]):
            raise ValueError(
                "claim must include at least one of time/location/number/version_or_policy_name"
            )
        if self.number and not self.numeric_facts:
            raise ValueError("claim with key numeric content must include numeric_facts")
        return self


class NumericFactItem(StrictModel):
    numeric_fact_id: str = Field(..., min_length=3, max_length=64, pattern=r"^nf_[A-Za-z0-9_-]+$")
    subject: str = Field(..., min_length=1, max_length=512)
    metric: str = Field(..., min_length=1, max_length=512)
    value_raw: str = Field(..., min_length=1, max_length=128)
    unit: str = Field(..., min_length=1, max_length=128)
    comparator: str = Field(default="eq", min_length=1, max_length=32)
    time: Optional[str] = Field(default=None, max_length=256)
    location: Optional[str] = Field(default=None, max_length=256)
    scope: Optional[str] = Field(default=None, max_length=128)
    evidence_span: Optional[str] = Field(default=None, max_length=512)

    @field_validator(
        "subject",
        "metric",
        "value_raw",
        "unit",
        "comparator",
        "time",
        "location",
        "scope",
        "evidence_span",
        mode="before",
    )
    @classmethod
    def clean_text_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("comparator")
    @classmethod
    def validate_comparator(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_NUMERIC_COMPARATORS:
            raise ValueError(f"comparator must be one of {sorted(_ALLOWED_NUMERIC_COMPARATORS)}")
        return normalized


class ClaimEvidenceEdgeItem(StrictModel):
    """research-feedback 中 claim 到证据的原子边。"""

    claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    source_id: str = Field(..., min_length=5, max_length=64, pattern=r"^src_[A-Za-z0-9_-]+$")
    stance: str = Field(default="support")
    evidence_snippet: str = Field(..., min_length=1, max_length=2000)
    support_score: float = Field(default=0.5, ge=0.0, le=1.0)
    source_tier: str = Field(default="tertiary")
    trace_depth: int = Field(default=2, ge=0, le=8)
    supported_slots: list[str] = Field(..., min_length=1)
    snippet_span_type: str
    numeric_facts: list[NumericFactItem] = Field(default_factory=list)
    used_in_final: bool = False

    @field_validator("stance")
    @classmethod
    def validate_stance(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in _ALLOWED_EDGE_STANCES:
            raise ValueError(f"stance must be one of {sorted(_ALLOWED_EDGE_STANCES)}")
        return normalized

    @field_validator("source_tier")
    @classmethod
    def validate_source_tier(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in _ALLOWED_SOURCE_TIERS:
            raise ValueError(f"source_tier must be one of {sorted(_ALLOWED_SOURCE_TIERS)}")
        return normalized

    @field_validator("evidence_snippet", mode="before")
    @classmethod
    def clean_evidence_snippet(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("supported_slots")
    @classmethod
    def normalize_supported_slots(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, lower=True, limit=8)
        unknown = sorted({item for item in normalized if item not in _ALLOWED_CLAIM_SLOT_KEYS})
        if unknown:
            raise ValueError(
                f"supported_slots must be drawn from {sorted(_ALLOWED_CLAIM_SLOT_KEYS)}"
            )
        return normalized

    @field_validator("snippet_span_type", mode="before")
    @classmethod
    def clean_snippet_span_type(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("snippet_span_type")
    @classmethod
    def validate_snippet_span_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.lower()
        if normalized not in _ALLOWED_SNIPPET_SPAN_TYPES:
            raise ValueError(
                f"snippet_span_type must be one of {sorted(_ALLOWED_SNIPPET_SPAN_TYPES)}"
            )
        return normalized

    @model_validator(mode="after")
    def validate_numeric_fact_presence(self) -> "ClaimEvidenceEdgeItem":
        if "number" in self.supported_slots and not self.numeric_facts:
            raise ValueError("claim_evidence_edge with key numeric content must include numeric_facts")
        return self


class ClaimSlotEvidenceItem(StrictModel):
    """research-feedback 中 claim 槽位到证据的可选显式映射。"""

    claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    source_id: str = Field(..., min_length=5, max_length=64, pattern=r"^src_[A-Za-z0-9_-]+$")
    slot_name: str = Field(..., min_length=1, max_length=64)
    slot_value: str = Field(..., min_length=1, max_length=512)
    evidence_snippet: str = Field(..., min_length=1, max_length=2000)
    page: Optional[str] = Field(default=None, max_length=64)
    section: Optional[str] = Field(default=None, max_length=255)
    line: Optional[str] = Field(default=None, max_length=64)
    snippet_span_type: Optional[str] = Field(default=None, max_length=32)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator(
        "slot_name",
        "slot_value",
        "evidence_snippet",
        "page",
        "section",
        "line",
        "snippet_span_type",
        mode="before",
    )
    @classmethod
    def clean_text_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("slot_name")
    @classmethod
    def validate_slot_name(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_CLAIM_SLOT_KEYS:
            raise ValueError(f"slot_name must be one of {sorted(_ALLOWED_CLAIM_SLOT_KEYS)}")
        return normalized

    @field_validator("snippet_span_type")
    @classmethod
    def validate_snippet_span_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.lower()
        if normalized not in _ALLOWED_SNIPPET_SPAN_TYPES:
            raise ValueError(
                f"snippet_span_type must be one of {sorted(_ALLOWED_SNIPPET_SPAN_TYPES)}"
            )
        return normalized


class ProvenanceEdgeItem(StrictModel):
    """research-feedback 中 document 到 document 的 provenance 候选边。"""

    source_id: str = Field(..., min_length=5, max_length=64, pattern=r"^src_[A-Za-z0-9_-]+$")
    parent_source_id: str = Field(..., min_length=5, max_length=64, pattern=r"^src_[A-Za-z0-9_-]+$")
    relation: str = Field(default="derived_from")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    rationale: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("relation")
    @classmethod
    def validate_relation(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in _ALLOWED_PROVENANCE_RELATIONS:
            raise ValueError(f"relation must be one of {sorted(_ALLOWED_PROVENANCE_RELATIONS)}")
        return normalized

    @field_validator("rationale", mode="before")
    @classmethod
    def clean_rationale(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)


class TypedConflictItem(StrictModel):
    """research-feedback 中可选显式提交的类型化冲突对象。"""

    conflict_id: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=64,
        pattern=r"^tc_[A-Za-z0-9_-]+$",
    )
    claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    slot_name: str = Field(..., min_length=1, max_length=64)
    conflict_type: str = Field(..., min_length=1, max_length=64)
    source_ids: list[str] = Field(..., min_length=1)
    conflicting_values: list[str] = Field(..., min_length=1)
    severity: str = Field(default="medium", min_length=1, max_length=32)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    recommended_action: Optional[str] = Field(default=None, max_length=128)
    cluster_aware: bool = True

    @field_validator("slot_name", "conflict_type", "severity", "recommended_action", mode="before")
    @classmethod
    def clean_text_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("slot_name")
    @classmethod
    def validate_slot_name(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_CONFLICT_SLOT_KEYS:
            raise ValueError(f"slot_name must be one of {sorted(_ALLOWED_CONFLICT_SLOT_KEYS)}")
        return normalized

    @field_validator("conflict_type")
    @classmethod
    def validate_conflict_type(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_CONFLICT_TYPES:
            raise ValueError(f"conflict_type must be one of {sorted(_ALLOWED_CONFLICT_TYPES)}")
        return normalized

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_CONFLICT_SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(_ALLOWED_CONFLICT_SEVERITIES)}")
        return normalized

    @field_validator("source_ids")
    @classmethod
    def normalize_source_ids(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, limit=16)
        for source_id in normalized:
            if not source_id.startswith("src_"):
                raise ValueError("source_ids must use canonical source ids like src_001")
        return normalized

    @field_validator("conflicting_values")
    @classmethod
    def normalize_conflicting_values(cls, v: list[str]) -> list[str]:
        return _dedupe_string_list(v, limit=16)


class CandidateCausalEdgeItem(StrictModel):
    """research-feedback 中显式提交的候选因果边占位。"""

    from_claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    to_claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    relation_type: str = Field(..., min_length=1, max_length=32)
    time_basis: Optional[str] = Field(default=None, max_length=256)
    mechanism_claim_ids: list[str] = Field(default_factory=list)
    supporting_source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("relation_type", "time_basis", mode="before")
    @classmethod
    def clean_text_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("relation_type")
    @classmethod
    def validate_relation_type(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_CAUSAL_RELATION_TYPES:
            raise ValueError(
                f"relation_type must be one of {sorted(_ALLOWED_CAUSAL_RELATION_TYPES)}"
            )
        return normalized

    @field_validator("mechanism_claim_ids")
    @classmethod
    def normalize_mechanism_claim_ids(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, limit=16)
        for claim_id in normalized:
            if not claim_id.startswith("c"):
                raise ValueError("mechanism_claim_ids must use canonical claim ids like c1")
        return normalized

    @field_validator("supporting_source_ids")
    @classmethod
    def normalize_supporting_source_ids(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, limit=16)
        for source_id in normalized:
            if not source_id.startswith("src_"):
                raise ValueError("supporting_source_ids must use canonical source ids like src_001")
        return normalized


class CausalGapItem(StrictModel):
    """research-feedback 中显式提交的因果缺口占位。"""

    from_claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    to_claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    gap_type: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=1000)
    supporting_source_ids: list[str] = Field(default_factory=list)

    @field_validator("gap_type", "reason", mode="before")
    @classmethod
    def clean_text_fields(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("gap_type")
    @classmethod
    def validate_gap_type(cls, v: str) -> str:
        normalized = v.lower()
        if normalized not in _ALLOWED_CAUSAL_GAP_TYPES:
            raise ValueError(f"gap_type must be one of {sorted(_ALLOWED_CAUSAL_GAP_TYPES)}")
        return normalized

    @field_validator("supporting_source_ids")
    @classmethod
    def normalize_supporting_source_ids(cls, v: list[str]) -> list[str]:
        normalized = _dedupe_string_list(v, limit=16)
        for source_id in normalized:
            if not source_id.startswith("src_"):
                raise ValueError("supporting_source_ids must use canonical source ids like src_001")
        return normalized


class QueryNormalizationInput(StrictModel):
    """客户端可选提交的 query normalization 结果。"""

    raw_query: Optional[str] = Field(default=None, max_length=2048)
    normalized_query: Optional[str] = Field(default=None, max_length=2048)
    subject: Optional[str] = Field(default=None, max_length=128)
    target_capability: Optional[str] = Field(default=None, max_length=128)
    time_scope: Optional[str] = Field(default=None, max_length=64)
    region_scope: Optional[str] = Field(default=None, max_length=64)
    version_scope: Optional[str] = Field(default=None, max_length=64)
    intent_type: Optional[str] = Field(default=None, max_length=64)
    query_category: Optional[str] = Field(default=None, max_length=100)
    topic_tags: list[str] = Field(default_factory=list)

    @field_validator(
        "raw_query",
        "normalized_query",
        "subject",
        "target_capability",
        "time_scope",
        "region_scope",
        "version_scope",
        "intent_type",
        "query_category",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        return _strip_optional_text(v)

    @field_validator("topic_tags")
    @classmethod
    def normalize_topic_tags(cls, v: list[str]) -> list[str]:
        return _dedupe_string_list(v, lower=True, limit=8)


class FeedbackRequest(StrictModel):
    """POST /v1/research-feedback 的请求体"""

    payload_version: str = Field(default="v2", pattern=r"^v2$")
    session_id: str = Field(..., min_length=1, max_length=64)
    query: Optional[str] = Field(default=None, max_length=2048)
    final_answer: Optional[str] = None
    sources: list[FeedbackSource] = Field(..., min_length=1)
    claims: list[ClaimItem] = Field(default_factory=list)
    claim_evidence_edges: list[ClaimEvidenceEdgeItem] = Field(default_factory=list)
    claim_slot_evidences: list[ClaimSlotEvidenceItem] = Field(default_factory=list)
    provenance_edges: list[ProvenanceEdgeItem] = Field(default_factory=list)
    contradictions: list[ContradictionItem] = Field(default_factory=list)
    typed_conflicts: list[TypedConflictItem] = Field(default_factory=list)
    candidate_causal_edges: list[CandidateCausalEdgeItem] = Field(default_factory=list)
    causal_gaps: list[CausalGapItem] = Field(default_factory=list)
    session_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    preference_blob: Optional[dict[str, Any]] = None
    query_normalization: Optional[QueryNormalizationInput] = None

    @field_validator("session_id", "query", "final_answer", mode="before")
    @classmethod
    def clean_top_level_text(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("text field must not be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_cross_references(self) -> "FeedbackRequest":
        source_ids = [src.source_id for src in self.sources]
        duplicate_source_ids = sorted({item for item in source_ids if source_ids.count(item) > 1})
        if duplicate_source_ids:
            raise ValueError(f"duplicate source_id values are not allowed: {duplicate_source_ids}")

        claim_ids = [claim.claim_id for claim in self.claims]
        duplicate_claim_ids = sorted({item for item in claim_ids if claim_ids.count(item) > 1})
        if duplicate_claim_ids:
            raise ValueError(f"duplicate claim_id values are not allowed: {duplicate_claim_ids}")

        claim_id_set = set(claim_ids)
        if self.claims and not self.claim_evidence_edges:
            raise ValueError("claim_evidence_edges is required and must be non-empty when claims are present")
        unknown_support_claim_ids = sorted(
            {
                claim_id
                for src in self.sources
                for claim_id in src.support_claim_ids
                if claim_id not in claim_id_set
            }
        )
        if unknown_support_claim_ids:
            raise ValueError(
                f"support_claim_ids reference unknown claim ids: {unknown_support_claim_ids}"
            )

        source_id_set = set(source_ids)
        unknown_supported_by = sorted(
            {
                source_id
                for claim in self.claims
                for source_id in claim.supported_by
                if source_id not in source_id_set
            }
        )
        if unknown_supported_by:
            raise ValueError(
                f"claims.supported_by reference unknown source ids: {unknown_supported_by}"
            )

        unknown_edge_claim_ids = sorted(
            {
                edge.claim_id
                for edge in self.claim_evidence_edges
                if edge.claim_id not in claim_id_set
            }
        )
        if unknown_edge_claim_ids:
            raise ValueError(
                f"claim_evidence_edges reference unknown claim ids: {unknown_edge_claim_ids}"
            )

        unknown_edge_source_ids = sorted(
            {
                edge.source_id
                for edge in self.claim_evidence_edges
                if edge.source_id not in source_id_set
            }
        )
        if unknown_edge_source_ids:
            raise ValueError(
                f"claim_evidence_edges reference unknown source ids: {unknown_edge_source_ids}"
            )

        unknown_slot_claim_ids = sorted(
            {
                item.claim_id
                for item in self.claim_slot_evidences
                if item.claim_id not in claim_id_set
            }
        )
        if unknown_slot_claim_ids:
            raise ValueError(
                f"claim_slot_evidences reference unknown claim ids: {unknown_slot_claim_ids}"
            )

        unknown_slot_source_ids = sorted(
            {
                item.source_id
                for item in self.claim_slot_evidences
                if item.source_id not in source_id_set
            }
        )
        if unknown_slot_source_ids:
            raise ValueError(
                f"claim_slot_evidences reference unknown source ids: {unknown_slot_source_ids}"
            )

        unknown_typed_conflict_claim_ids = sorted(
            {
                item.claim_id
                for item in self.typed_conflicts
                if item.claim_id not in claim_id_set
            }
        )
        if unknown_typed_conflict_claim_ids:
            raise ValueError(
                "typed_conflicts reference unknown claim ids: "
                f"{unknown_typed_conflict_claim_ids}"
            )

        unknown_typed_conflict_source_ids = sorted(
            {
                source_id
                for item in self.typed_conflicts
                for source_id in item.source_ids
                if source_id not in source_id_set
            }
        )
        if unknown_typed_conflict_source_ids:
            raise ValueError(
                "typed_conflicts reference unknown source ids: "
                f"{unknown_typed_conflict_source_ids}"
            )

        unknown_candidate_causal_claim_ids = sorted(
            {
                claim_id
                for item in self.candidate_causal_edges
                for claim_id in [item.from_claim_id, item.to_claim_id, *item.mechanism_claim_ids]
                if claim_id not in claim_id_set
            }
        )
        if unknown_candidate_causal_claim_ids:
            raise ValueError(
                "candidate_causal_edges reference unknown claim ids: "
                f"{unknown_candidate_causal_claim_ids}"
            )

        unknown_candidate_causal_source_ids = sorted(
            {
                source_id
                for item in self.candidate_causal_edges
                for source_id in item.supporting_source_ids
                if source_id not in source_id_set
            }
        )
        if unknown_candidate_causal_source_ids:
            raise ValueError(
                "candidate_causal_edges reference unknown source ids: "
                f"{unknown_candidate_causal_source_ids}"
            )

        unknown_causal_gap_claim_ids = sorted(
            {
                claim_id
                for item in self.causal_gaps
                for claim_id in [item.from_claim_id, item.to_claim_id]
                if claim_id not in claim_id_set
            }
        )
        if unknown_causal_gap_claim_ids:
            raise ValueError(
                f"causal_gaps reference unknown claim ids: {unknown_causal_gap_claim_ids}"
            )

        unknown_causal_gap_source_ids = sorted(
            {
                source_id
                for item in self.causal_gaps
                for source_id in item.supporting_source_ids
                if source_id not in source_id_set
            }
        )
        if unknown_causal_gap_source_ids:
            raise ValueError(
                f"causal_gaps reference unknown source ids: {unknown_causal_gap_source_ids}"
            )

        support_edge_pairs = {
            (edge.claim_id, edge.source_id)
            for edge in self.claim_evidence_edges
            if edge.stance in {"support", "partial"}
        }
        inconsistent_support_claim_ids = sorted(
            {
                f"{src.source_id}:{claim_id}"
                for src in self.sources
                if src.source_id
                for claim_id in src.support_claim_ids
                if (claim_id, src.source_id) not in support_edge_pairs
            }
        )
        if inconsistent_support_claim_ids:
            raise ValueError(
                "support_claim_ids must be backed by claim_evidence_edges: "
                f"{inconsistent_support_claim_ids}"
            )

        inconsistent_supported_by = sorted(
            {
                f"{claim.claim_id}:{source_id}"
                for claim in self.claims
                for source_id in claim.supported_by
                if (claim.claim_id, source_id) not in support_edge_pairs
            }
        )
        if inconsistent_supported_by:
            raise ValueError(
                "claims.supported_by must be backed by claim_evidence_edges: "
                f"{inconsistent_supported_by}"
            )

        unsupported_edge_pairs = sorted(
            {
                f"{edge.claim_id}:{edge.source_id}"
                for edge in self.claim_evidence_edges
                if edge.stance in {"support", "partial"}
                and edge.source_id not in next(
                    (claim.supported_by for claim in self.claims if claim.claim_id == edge.claim_id),
                    [],
                )
            }
        )
        if unsupported_edge_pairs:
            raise ValueError(
                "support and partial claim_evidence_edges must be declared in claims.supported_by: "
                f"{unsupported_edge_pairs}"
            )

        unknown_provenance_sources = sorted(
            {
                edge.source_id
                for edge in self.provenance_edges
                if edge.source_id not in source_id_set
            }
        )
        if unknown_provenance_sources:
            raise ValueError(
                "provenance_edges reference unknown source ids: "
                f"{unknown_provenance_sources}"
            )

        unknown_provenance_parents = sorted(
            {
                edge.parent_source_id
                for edge in self.provenance_edges
                if edge.parent_source_id not in source_id_set
            }
        )
        if unknown_provenance_parents:
            raise ValueError(
                "provenance_edges reference unknown parent_source_id values: "
                f"{unknown_provenance_parents}"
            )

        self_edges = sorted(
            {
                f"{edge.source_id}->{edge.parent_source_id}"
                for edge in self.provenance_edges
                if edge.source_id == edge.parent_source_id
            }
        )
        if self_edges:
            raise ValueError(f"provenance_edges must not point to self: {self_edges}")

        return self


ClaimItem.model_rebuild()
ClaimEvidenceEdgeItem.model_rebuild()


class OffnetClaimItem(StrictModel):
    """offnet-analysis 中的单条离线 claim。仅用于 LLM 未搜索外部网页时的回答结构核验。"""

    claim_id: str = Field(..., min_length=1, max_length=64, pattern=r"^c[A-Za-z0-9_-]*$")
    text: str = Field(..., min_length=1, max_length=4096)
    supporting_evidence: list[str] = Field(default_factory=list)
    source_basis: list[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def clean_claim_text(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("claim text must not be empty")
        return cleaned

    @field_validator("supporting_evidence", "source_basis", "risk_flags")
    @classmethod
    def normalize_string_lists(cls, v: list[str]) -> list[str]:
        return _dedupe_string_list(v, limit=16)


class OffnetAnswerSignals(StrictModel):
    """offnet-analysis 中 LLM 自报的回答结构信号"""

    has_external_citations: bool = False
    has_uncertainty_disclosure: bool = False
    has_counterarguments: bool = False
    has_structured_reasoning: bool = False


class OffnetAnalysisRequest(StrictModel):
    """
    POST /v1/offnet-analysis 的请求体。

    用途：LLM 没有实际抓取外部网页时，skill 自动回退到这个端点，
    由后台核验回答自身的支撑结构与风险信号。
    不暴露为用户侧独立命令，始终作为 research-feedback 的无源回退路径。
    """

    payload_version: str = Field(default="v2", pattern=r"^v2$")
    analysis_mode: str = Field(default="offnet", pattern=r"^offnet$")
    session_id: str = Field(..., min_length=1, max_length=64)
    query: Optional[str] = Field(default=None, max_length=2048)
    answer_text: str = Field(..., min_length=1, max_length=20000)
    claims: list[OffnetClaimItem] = Field(..., min_length=1, max_length=12)
    answer_signals: OffnetAnswerSignals = Field(default_factory=OffnetAnswerSignals)

    @field_validator("session_id", "query", "answer_text", mode="before")
    @classmethod
    def clean_offnet_text(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("text field must not be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_offnet_claim_ids(self) -> "OffnetAnalysisRequest":
        claim_ids = [claim.claim_id for claim in self.claims]
        duplicate_claim_ids = sorted({item for item in claim_ids if claim_ids.count(item) > 1})
        if duplicate_claim_ids:
            raise ValueError(f"duplicate claim_id values are not allowed: {duplicate_claim_ids}")
        return self
