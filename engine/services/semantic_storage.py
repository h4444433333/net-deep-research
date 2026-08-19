"""
Minimal semantic storage for test-scoped research feedback.

This layer is intentionally additive:
- keep the current provenance DAG and claim_evidence_edge flow unchanged
- write semantic long-term/process assets only when explicitly enabled
- reserve claim graph / causal graph metadata without introducing a causal engine
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from urllib.parse import parse_qsl, urlsplit, urlunsplit

try:
    from db.connection import get_write_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_write_connection
from utils.logger import get_logger

from psycopg2.extras import execute_values


_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_SEMANTIC_PROCESS_RETENTION_DAYS = int(os.environ.get("SEMANTIC_PROCESS_RETENTION_DAYS", "30"))
_SEMANTIC_WRITE_MODE = {"disabled", "test", "enabled"}
_SEMANTIC_CAUSAL_PLACEHOLDER_WRITE_MODE = {"disabled", "test", "enabled"}
_BASE_REQUIRED_TABLES = (
    "canonical_source",
    "claim",
    "provenance_cluster",
    "typed_conflict",
    "claim_slot_evidence",
)
logger = get_logger("semantic_storage")


def get_semantic_storage_write_mode() -> str:
    raw_mode = os.environ.get("SEMANTIC_STORAGE_WRITE_MODE", "disabled").strip().lower()
    if raw_mode in _SEMANTIC_WRITE_MODE:
        return raw_mode
    return "disabled"


def _is_test_environment() -> bool:
    env_name = (
        os.environ.get("NET_INFO_ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or ""
    ).strip().lower()
    db_name = os.environ.get("DB_NAME", "").strip().lower()
    return env_name in {"test", "testing"} or db_name.endswith("_test")


def _feature_enabled(mode: str) -> bool:
    if mode == "enabled":
        return True
    if mode != "test":
        return False
    return _is_test_environment()


def semantic_storage_enabled() -> bool:
    return _feature_enabled(get_semantic_storage_write_mode())


def get_semantic_causal_placeholder_write_mode() -> str:
    raw_mode = os.environ.get("SEMANTIC_CAUSAL_PLACEHOLDER_WRITE_MODE", "disabled").strip().lower()
    if raw_mode in _SEMANTIC_CAUSAL_PLACEHOLDER_WRITE_MODE:
        return raw_mode
    return "disabled"


def causal_placeholder_persistence_enabled() -> bool:
    return semantic_storage_enabled() and _feature_enabled(get_semantic_causal_placeholder_write_mode())


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _stable_hex(seed: str, prefix: str) -> str:
    return hashlib.sha256(f"{prefix}:{seed}".encode("utf-8")).hexdigest()[:16]


def _normalize_url(raw_url: str) -> str:
    candidate = (raw_url or "").strip()
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
    query = "&".join(f"{key}={value}" if value else key for key, value in kept_query)
    scheme = (parts.scheme or "https").lower()
    return urlunsplit((scheme, host, path, query, ""))[:2048]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _resolve_source_roots(req) -> dict[str, list[str]]:
    source_ids = [src.source_id for src in req.sources if src.source_id]
    parents_by_source: dict[str, list[str]] = {}
    confidence_by_edge: dict[tuple[str, str], float] = {}
    for relation in req.provenance_edges:
        parents_by_source.setdefault(relation.source_id, []).append(relation.parent_source_id)
        confidence_by_edge[(relation.source_id, relation.parent_source_id)] = float(relation.confidence)

    root_cache: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def resolve(source_id: str) -> list[str]:
        if source_id in root_cache:
            return root_cache[source_id]
        if source_id in visiting:
            root_cache[source_id] = [source_id]
            return root_cache[source_id]
        visiting.add(source_id)
        parents = parents_by_source.get(source_id, [])
        if not parents:
            roots = [source_id]
        else:
            roots = sorted({root for parent_id in parents for root in resolve(parent_id)})
            if not roots:
                roots = [source_id]
        visiting.remove(source_id)
        root_cache[source_id] = roots
        return roots

    for source_id in source_ids:
        resolve(source_id)
    return root_cache


def _build_runtime_graph(
    req,
    claim_edges: list[dict],
    accepted_causal_edges: list[dict],
    candidate_causal_edges: list[dict],
    causal_gaps: list[dict],
) -> dict:
    claim_node_count = len(req.claims)
    return {
        "claim_graph": {
            "status": "ready" if claim_node_count > 0 else "not_involved",
            "claim_node_count": claim_node_count,
            "evidence_edge_count": len(claim_edges),
            "provenance_edge_count": len(req.provenance_edges),
            "supports_evidence_structuring": claim_node_count > 0,
            "supports_typed_conflict": claim_node_count > 0,
        },
        "causal_graph": {
            "status": "ready" if causal_placeholder_persistence_enabled() else "reserved",
            "candidate_edge_count": len(candidate_causal_edges),
            "accepted_edge_count": len(accepted_causal_edges),
            "causal_gap_count": len(causal_gaps),
        },
    }


def _build_provenance_clusters(req, source_roots: dict[str, list[str]]) -> tuple[list[dict], dict[str, dict]]:
    confidence_pairs = {
        (edge.source_id, edge.parent_source_id): float(edge.confidence)
        for edge in req.provenance_edges
    }
    groups: dict[str, list[str]] = defaultdict(list)
    for src in req.sources:
        roots = source_roots.get(src.source_id, [src.source_id])
        root_signature = "|".join(roots)
        groups[root_signature].append(src.source_id)

    cluster_records: list[dict] = []
    source_cluster_index: dict[str, dict] = {}
    for root_signature, member_source_ids in groups.items():
        roots = root_signature.split("|") if root_signature else []
        relation_confidences = [
            confidence
            for (source_id, _parent_id), confidence in confidence_pairs.items()
            if source_id in member_source_ids
        ]
        if len(member_source_ids) == 1:
            cluster_type = "independent"
        elif len(roots) == 1:
            cluster_type = "derived"
        else:
            cluster_type = "aggregation"
        cluster_record = {
            "cluster_id": _stable_hex(root_signature or member_source_ids[0], "provenance_cluster"),
            "cluster_key": root_signature or member_source_ids[0],
            "root_source_id": roots[0] if roots else member_source_ids[0],
            "root_source_ids": roots or [member_source_ids[0]],
            "member_source_ids": sorted(member_source_ids),
            "cluster_type": cluster_type,
            "confidence": round(
                sum(relation_confidences) / len(relation_confidences),
                4,
            )
            if relation_confidences
            else 1.0,
            "rationale": (
                "derived from provenance_edges"
                if req.provenance_edges
                else "single-source cluster without provenance_edges"
            ),
            "last_session_id": req.session_id,
        }
        cluster_records.append(cluster_record)
        for source_id in member_source_ids:
            source_cluster_index[source_id] = {
                "cluster_id": cluster_record["cluster_id"],
                "root_signature": cluster_record["cluster_key"],
                "root_source_ids": cluster_record["root_source_ids"],
                "cluster_type": cluster_record["cluster_type"],
            }
    return cluster_records, source_cluster_index


def _build_canonical_sources(req, source_cluster_index: dict[str, dict]) -> tuple[list[dict], dict[str, dict]]:
    grouped: dict[str, dict] = {}
    per_source_index: dict[str, dict] = {}
    for src in req.sources:
        canonical_url = _normalize_url(src.url)
        entry = grouped.setdefault(
            canonical_url,
            {
                "canonical_source_id": _stable_hex(canonical_url, "canonical_source"),
                "canonical_key": canonical_url,
                "canonical_url": canonical_url,
                "parent_domain": src.domain,
                "alias_urls": [],
                "status": "active",
                "first_session_id": req.session_id,
                "last_session_id": req.session_id,
                "cluster_hint": source_cluster_index.get(src.source_id, {}).get("cluster_id"),
            },
        )
        entry["alias_urls"] = _dedupe_preserve_order(entry["alias_urls"] + [src.url, canonical_url])
        per_source_index[src.source_id] = {
            "canonical_source_id": entry["canonical_source_id"],
            "canonical_key": entry["canonical_key"],
        }
    return list(grouped.values()), per_source_index


def _build_promoted_claims(req, claim_verification: dict) -> tuple[list[dict], dict[str, dict]]:
    reviews_by_id = {
        item["claim_id"]: item
        for item in claim_verification.get("claim_reviews", [])
        if item.get("claim_id")
    }
    records: list[dict] = []
    claim_index: dict[str, dict] = {}
    for claim in req.claims:
        claim_seed = "|".join(
            [
                _normalize_text(claim.subject),
                _normalize_text(claim.action),
                _normalize_text(claim.time),
                _normalize_text(claim.location),
                _normalize_text(claim.number),
                _normalize_text(claim.version_or_policy_name),
            ]
        )
        claim_key = _stable_hex(claim_seed, "claim_key")
        claim_uid = _stable_hex(claim_key, "claim")
        review = reviews_by_id.get(claim.claim_id, {})
        record = {
            "claim_uid": claim_uid,
            "claim_key": claim_key,
            "claim_business_id": claim.claim_id,
            "claim_text": claim.text,
            "subject": claim.subject,
            "action": claim.action,
            "time": claim.time,
            "location": claim.location,
            "number": claim.number,
            "version_or_policy_name": claim.version_or_policy_name,
            "status": "active",
            "promotion_reason": "test_feedback_minimal",
            "first_session_id": req.session_id,
            "last_session_id": req.session_id,
            "supporting_source_count": len(claim.supported_by),
            "claim_verdict": review.get("verdict"),
            "valid_to": None,
            "superseded_by": None,
        }
        records.append(record)
        claim_index[claim.claim_id] = record
    return records, claim_index


def _derive_claim_slot_evidence(
    claim_edges: list[dict],
    claim_index: dict[str, dict],
    canonical_source_index: dict[str, dict],
    source_cluster_index: dict[str, dict],
) -> list[dict]:
    records: list[dict] = []
    for edge in claim_edges:
        claim_record = claim_index.get(edge["claim_id"])
        if not claim_record:
            continue
        slot_map = edge.get("claim_slots") or {}
        for slot_name in edge.get("supported_slots") or []:
            slot_value = (slot_map.get(slot_name) or "").strip()
            if not slot_value:
                continue
            cluster_meta = source_cluster_index.get(edge["source_id"], {})
            canonical_meta = canonical_source_index.get(edge["source_id"], {})
            records.append(
                {
                    "session_id": edge["session_id"],
                    "claim_business_id": edge["claim_id"],
                    "claim_uid": claim_record["claim_uid"],
                    "slot_name": slot_name,
                    "slot_value": slot_value,
                    "source_pk": edge.get("source_pk"),
                    "llm_source_id": edge["source_id"],
                    "canonical_source_id": canonical_meta.get("canonical_source_id"),
                    "provenance_cluster_id": cluster_meta.get("cluster_id"),
                    "root_signature": cluster_meta.get("root_signature") or edge.get("root_signature") or edge["source_id"],
                    "evidence_snippet": edge.get("evidence_snippet"),
                    "page": None,
                    "section": None,
                    "line": None,
                    "snippet_span_type": edge.get("snippet_span_type"),
                    "confidence": round(
                        float(edge.get("edge_confidence", edge.get("support_score", 0.5))),
                        4,
                    ),
                }
            )
    return records


def _build_claim_slot_evidence(
    req,
    claim_edges: list[dict],
    claim_index: dict[str, dict],
    canonical_source_index: dict[str, dict],
    source_cluster_index: dict[str, dict],
) -> list[dict]:
    records: dict[tuple[str, str, str, str], dict] = {}

    for item in req.claim_slot_evidences:
        claim_record = claim_index.get(item.claim_id)
        if not claim_record:
            continue
        cluster_meta = source_cluster_index.get(item.source_id, {})
        canonical_meta = canonical_source_index.get(item.source_id, {})
        record = {
            "session_id": req.session_id,
            "claim_business_id": item.claim_id,
            "claim_uid": claim_record["claim_uid"],
            "slot_name": item.slot_name,
            "slot_value": item.slot_value,
            "source_pk": None,
            "llm_source_id": item.source_id,
            "canonical_source_id": canonical_meta.get("canonical_source_id"),
            "provenance_cluster_id": cluster_meta.get("cluster_id"),
            "root_signature": cluster_meta.get("root_signature") or item.source_id,
            "evidence_snippet": item.evidence_snippet,
            "page": item.page,
            "section": item.section,
            "line": item.line,
            "snippet_span_type": item.snippet_span_type,
            "confidence": round(float(item.confidence), 4),
        }
        key = (
            record["claim_business_id"],
            record["llm_source_id"],
            record["slot_name"],
            record["evidence_snippet"],
        )
        records[key] = record

    for record in _derive_claim_slot_evidence(
        claim_edges,
        claim_index,
        canonical_source_index,
        source_cluster_index,
    ):
        key = (
            record["claim_business_id"],
            record["llm_source_id"],
            record["slot_name"],
            record["evidence_snippet"],
        )
        records.setdefault(key, record)

    # 方案 1：原文降级为 slot_name='text' 证据，避免原文措辞差异产生重复 claim
    for claim in req.claims:
        claim_record = claim_index.get(claim.claim_id)
        if not claim_record or not (claim.text or "").strip():
            continue
        supported_by = claim.supported_by or []
        primary_source = supported_by[0] if supported_by else None
        cluster_meta = source_cluster_index.get(primary_source, {}) if primary_source else {}
        canonical_meta = canonical_source_index.get(primary_source, {}) if primary_source else {}
        text_record = {
            "session_id": req.session_id,
            "claim_business_id": claim.claim_id,
            "claim_uid": claim_record["claim_uid"],
            "slot_name": "text",
            "slot_value": claim.text[:512],
            "source_pk": None,
            "llm_source_id": primary_source or claim.claim_id,
            "canonical_source_id": canonical_meta.get("canonical_source_id"),
            "provenance_cluster_id": cluster_meta.get("cluster_id"),
            "root_signature": cluster_meta.get("root_signature") or primary_source or claim.claim_id,
            "evidence_snippet": claim.text,
            "page": None,
            "section": None,
            "line": None,
            "snippet_span_type": None,
            "confidence": 1.0,
        }
        key = (
            text_record["claim_business_id"],
            text_record["llm_source_id"],
            text_record["slot_name"],
            text_record["evidence_snippet"],
        )
        records.setdefault(key, text_record)

    return list(records.values())


def _build_typed_conflicts(
    req,
    claim_slot_evidence: list[dict],
    claim_index: dict[str, dict],
    source_cluster_index: dict[str, dict],
) -> list[dict]:
    results: dict[str, dict] = {}
    for item in req.typed_conflicts:
        claim_record = claim_index.get(item.claim_id)
        if not claim_record:
            continue
        conflict_key = item.conflict_id or _stable_hex(
            "|".join(
                [
                    item.claim_id,
                    item.slot_name,
                    item.conflict_type,
                    ",".join(sorted(item.source_ids)),
                    ",".join(item.conflicting_values),
                ]
            ),
            "typed_conflict",
        )
        results[conflict_key] = {
            "conflict_id": conflict_key,
            "conflict_key": conflict_key,
            "claim_uid": claim_record["claim_uid"],
            "claim_business_id": item.claim_id,
            "slot_name": item.slot_name,
            "conflict_type": item.conflict_type,
            "source_ids": list(item.source_ids),
            "conflicting_values": list(item.conflicting_values),
            "severity": item.severity,
            "confidence": round(float(item.confidence), 4),
            "recommended_action": item.recommended_action or "manual_review",
            "cluster_aware": bool(item.cluster_aware),
            "status": "active",
            "first_session_id": req.session_id,
            "last_session_id": req.session_id,
            "valid_to": None,
            "superseded_by": None,
        }

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in claim_slot_evidence:
        grouped[(record["claim_business_id"], record["slot_name"])].append(record)

    for (claim_id, slot_name), records in grouped.items():
        distinct_value_sources: dict[str, set[str]] = defaultdict(set)
        distinct_value_roots: dict[str, set[str]] = defaultdict(set)
        distinct_value_display: dict[str, str] = {}
        for record in records:
            normalized_value = _normalize_text(record["slot_value"])
            if not normalized_value:
                continue
            distinct_value_display.setdefault(normalized_value, record["slot_value"])
            distinct_value_sources[normalized_value].add(record["llm_source_id"])
            distinct_value_roots[normalized_value].add(record.get("root_signature") or record["llm_source_id"])
        if len(distinct_value_display) <= 1:
            continue

        all_roots = {root for roots in distinct_value_roots.values() for root in roots}
        if len(all_roots) >= 2:
            conflict_type = "temporal_conflict" if slot_name == "time" else "value_conflict"
            severity = "high" if slot_name in {"time", "number"} else "medium"
            recommended_action = "manual_review"
        else:
            conflict_type = "derivative_conflict"
            severity = "medium"
            recommended_action = "prefer_root_source"

        source_ids = sorted(
            {
                source_id
                for source_ids_for_value in distinct_value_sources.values()
                for source_id in source_ids_for_value
            }
        )
        conflict_seed = "|".join(
            [
                claim_id,
                slot_name,
                conflict_type,
                ",".join(source_ids),
                ",".join(sorted(distinct_value_display)),
            ]
        )
        claim_record = claim_index.get(claim_id)
        if not claim_record:
            continue
        results.setdefault(
            _stable_hex(conflict_seed, "typed_conflict"),
            {
                "conflict_id": _stable_hex(conflict_seed, "typed_conflict"),
                "conflict_key": _stable_hex(conflict_seed, "typed_conflict"),
                "claim_uid": claim_record["claim_uid"],
                "claim_business_id": claim_id,
                "slot_name": slot_name,
                "conflict_type": conflict_type,
                "source_ids": source_ids,
                "conflicting_values": [
                    distinct_value_display[key]
                    for key in sorted(distinct_value_display)
                ],
                "severity": severity,
                "confidence": round(min(0.95, 0.55 + len(all_roots) * 0.1), 4),
                "recommended_action": recommended_action,
                "cluster_aware": True,
                "status": "active",
                "first_session_id": req.session_id,
                "last_session_id": req.session_id,
                "valid_to": None,
                "superseded_by": None,
            },
        )

    domain_to_sources: dict[str, list[str]] = defaultdict(list)
    for src in req.sources:
        domain_to_sources[src.domain].append(src.source_id)
    claim_text_to_id = {claim.text: claim.claim_id for claim in req.claims}
    for contradiction in req.contradictions:
        claim_id = claim_text_to_id.get(contradiction.claim)
        if not claim_id:
            continue
        source_ids = _dedupe_preserve_order(
            domain_to_sources.get(contradiction.source_a, [])
            + domain_to_sources.get(contradiction.source_b, [])
        )
        if not source_ids:
            continue
        roots = {
            source_cluster_index.get(source_id, {}).get("root_signature") or source_id
            for source_id in source_ids
        }
        conflict_type = "derivative_conflict" if len(roots) <= 1 else "logical_conflict"
        recommended_action = "prefer_root_source" if conflict_type == "derivative_conflict" else "manual_review"
        conflict_id = _stable_hex(
            f"{claim_id}|claim|{conflict_type}|{','.join(sorted(source_ids))}",
            "typed_conflict",
        )
        claim_record = claim_index.get(claim_id)
        if not claim_record:
            continue
        results.setdefault(
            conflict_id,
            {
                "conflict_id": conflict_id,
                "conflict_key": conflict_id,
                "claim_uid": claim_record["claim_uid"],
                "claim_business_id": claim_id,
                "slot_name": "claim",
                "conflict_type": conflict_type,
                "source_ids": source_ids,
                "conflicting_values": [contradiction.source_a, contradiction.source_b],
                "severity": "high" if conflict_type == "logical_conflict" else "medium",
                "confidence": 0.7 if conflict_type == "logical_conflict" else 0.55,
                "recommended_action": recommended_action,
                "cluster_aware": True,
                "status": "active",
                "first_session_id": req.session_id,
                "last_session_id": req.session_id,
                "valid_to": None,
                "superseded_by": None,
            },
        )
    return list(results.values())


def _build_candidate_causal_edges(
    req,
    claim_index: dict[str, dict],
) -> list[dict]:
    if not causal_placeholder_persistence_enabled():
        return []

    records: list[dict] = []
    for item in req.candidate_causal_edges:
        from_claim = claim_index.get(item.from_claim_id)
        to_claim = claim_index.get(item.to_claim_id)
        if not from_claim or not to_claim:
            continue
        edge_seed = "|".join(
            [
                item.from_claim_id,
                item.to_claim_id,
                item.relation_type,
                item.time_basis or "",
                ",".join(item.mechanism_claim_ids),
                ",".join(item.supporting_source_ids),
            ]
        )
        records.append(
            {
                "edge_id": _stable_hex(edge_seed, "candidate_causal_edge"),
                "from_claim_uid": from_claim["claim_uid"],
                "from_claim_id": item.from_claim_id,
                "to_claim_uid": to_claim["claim_uid"],
                "to_claim_id": item.to_claim_id,
                "relation_type": item.relation_type,
                "time_basis": item.time_basis,
                "mechanism_claim_ids": item.mechanism_claim_ids,
                "supporting_source_ids": item.supporting_source_ids,
                "confidence": round(float(item.confidence), 4),
                "status": "candidate",
                "last_session_id": req.session_id,
            }
        )
    return records


def _build_causal_gaps(
    req,
    claim_index: dict[str, dict],
) -> list[dict]:
    if not causal_placeholder_persistence_enabled():
        return []

    records: list[dict] = []
    for item in req.causal_gaps:
        from_claim = claim_index.get(item.from_claim_id)
        to_claim = claim_index.get(item.to_claim_id)
        if not from_claim or not to_claim:
            continue
        gap_seed = "|".join(
            [
                item.from_claim_id,
                item.to_claim_id,
                item.gap_type,
                item.reason,
                ",".join(item.supporting_source_ids),
            ]
        )
        records.append(
            {
                "gap_id": _stable_hex(gap_seed, "causal_gap"),
                "from_claim_uid": from_claim["claim_uid"],
                "from_claim_id": item.from_claim_id,
                "to_claim_uid": to_claim["claim_uid"],
                "to_claim_id": item.to_claim_id,
                "gap_type": item.gap_type,
                "reason": item.reason,
                "supporting_source_ids": item.supporting_source_ids,
                "status": "open",
                "last_session_id": req.session_id,
            }
        )
    return records


def _claim_review_index(claim_verification: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in claim_verification.get("claim_reviews", []) or []:
        claim_id = (item.get("claim_id") or "").strip()
        if claim_id:
            result[claim_id] = item
    return result


def _build_accepted_causal_edges(
    req,
    candidate_causal_edges: list[dict],
    typed_conflicts: list[dict],
    source_cluster_index: dict[str, dict],
    claim_verification: dict,
) -> tuple[list[dict], list[dict]]:
    if not causal_placeholder_persistence_enabled():
        return [], []

    claim_reviews = _claim_review_index(claim_verification)
    high_conflict_claim_ids = {
        record["claim_business_id"]
        for record in typed_conflicts
        if record.get("severity") == "high"
    }
    explicit_gap_keys = {
        (
            item.from_claim_id,
            item.to_claim_id,
            item.gap_type,
            item.reason,
        )
        for item in req.causal_gaps
    }
    accepted_edges: list[dict] = []
    derived_gaps: list[dict] = []

    for record in candidate_causal_edges:
        reasons: list[tuple[str, str]] = []
        from_claim_id = record["from_claim_id"]
        to_claim_id = record["to_claim_id"]
        supporting_source_ids = record["supporting_source_ids"]
        root_signatures = {
            source_cluster_index.get(source_id, {}).get("root_signature") or source_id
            for source_id in supporting_source_ids
        }
        independent_root_count = len(root_signatures)

        if not (record.get("time_basis") or "").strip():
            reasons.append(("missing_time_anchor", "缺少可验证的时间锚点。"))
        if not record.get("mechanism_claim_ids"):
            reasons.append(("missing_mechanism", "缺少机制 claim，无法解释因果路径。"))
        if independent_root_count < 1:
            reasons.append(("insufficient_independent_support", "缺少独立来源支撑。"))
        if independent_root_count == 1 and not supporting_source_ids:
            reasons.append(("insufficient_independent_support", "候选因果边没有支撑来源。"))
        if float(record.get("confidence", 0.0)) < 0.6:
            reasons.append(("insufficient_independent_support", "候选因果边置信度不足。"))

        from_review = (claim_reviews.get(from_claim_id) or {}).get("verdict")
        to_review = (claim_reviews.get(to_claim_id) or {}).get("verdict")
        if from_review in {"conflicted", "unsupported"} or to_review in {"conflicted", "unsupported"}:
            reasons.append(("insufficient_independent_support", "相关 claim 仍存在未消解冲突或支撑不足。"))
        if from_claim_id in high_conflict_claim_ids or to_claim_id in high_conflict_claim_ids:
            reasons.append(("insufficient_independent_support", "高严重度 typed conflict 尚未消解。"))

        if reasons:
            for gap_type, reason in reasons:
                gap_key = (from_claim_id, to_claim_id, gap_type, reason)
                if gap_key in explicit_gap_keys:
                    continue
                gap_seed = "|".join(
                    [
                        from_claim_id,
                        to_claim_id,
                        gap_type,
                        reason,
                        ",".join(supporting_source_ids),
                    ]
                )
                derived_gaps.append(
                    {
                        "gap_id": _stable_hex(gap_seed, "causal_gap"),
                        "from_claim_uid": record["from_claim_uid"],
                        "from_claim_id": from_claim_id,
                        "to_claim_uid": record["to_claim_uid"],
                        "to_claim_id": to_claim_id,
                        "gap_type": gap_type,
                        "reason": reason,
                        "supporting_source_ids": supporting_source_ids,
                        "status": "open",
                        "last_session_id": req.session_id,
                    }
                )
            continue

        accepted_edges.append(
            {
                "edge_id": record["edge_id"],
                "from_claim_uid": record["from_claim_uid"],
                "from_claim_id": from_claim_id,
                "to_claim_uid": record["to_claim_uid"],
                "to_claim_id": to_claim_id,
                "relation_type": record["relation_type"],
                "time_basis": record.get("time_basis"),
                "mechanism_claim_ids": list(record["mechanism_claim_ids"]),
                "supporting_source_ids": list(supporting_source_ids),
                "independent_root_count": independent_root_count,
                "confidence": round(float(record["confidence"]), 4),
                "acceptance_reason": "time_anchor+mechanism+independent_support",
                "status": "active",
                "first_session_id": req.session_id,
                "last_session_id": req.session_id,
                "valid_to": None,
                "superseded_by": None,
            }
        )

    return accepted_edges, derived_gaps


def plan_semantic_feedback_persistence(
    *,
    req,
    claim_edges: list[dict],
    claim_verification: dict,
) -> dict:
    mode = get_semantic_storage_write_mode()
    if not semantic_storage_enabled():
        runtime_graph = _build_runtime_graph(req, claim_edges, [], [], [])
        return {
            "enabled": False,
            "mode": mode,
            "status": "disabled",
            "runtime_graph": runtime_graph,
            "canonical_sources": [],
            "promoted_claims": [],
            "provenance_clusters": [],
            "typed_conflicts": [],
            "claim_slot_evidence": [],
            "accepted_causal_edges": [],
            "candidate_causal_edges": [],
            "causal_gaps": [],
        }

    claim_edges_with_session = []
    for edge in claim_edges:
        enriched = dict(edge)
        enriched.setdefault("session_id", req.session_id)
        claim_edges_with_session.append(enriched)

    source_roots = _resolve_source_roots(req)
    provenance_clusters, source_cluster_index = _build_provenance_clusters(req, source_roots)
    canonical_sources, canonical_source_index = _build_canonical_sources(req, source_cluster_index)
    promoted_claims, claim_index = _build_promoted_claims(req, claim_verification)
    claim_slot_evidence = _build_claim_slot_evidence(
        req,
        claim_edges_with_session,
        claim_index,
        canonical_source_index,
        source_cluster_index,
    )
    typed_conflicts = _build_typed_conflicts(
        req,
        claim_slot_evidence,
        claim_index,
        source_cluster_index,
    )
    candidate_causal_edges = _build_candidate_causal_edges(req, claim_index)
    explicit_causal_gaps = _build_causal_gaps(req, claim_index)
    accepted_causal_edges, derived_causal_gaps = _build_accepted_causal_edges(
        req,
        candidate_causal_edges,
        typed_conflicts,
        source_cluster_index,
        claim_verification,
    )
    causal_gaps = explicit_causal_gaps + [
        gap for gap in derived_causal_gaps if gap["gap_id"] not in {item["gap_id"] for item in explicit_causal_gaps}
    ]
    runtime_graph = _build_runtime_graph(
        req,
        claim_edges,
        accepted_causal_edges,
        candidate_causal_edges,
        causal_gaps,
    )
    return {
        "enabled": True,
        "mode": mode,
        "status": "planned",
        "runtime_graph": runtime_graph,
        "canonical_sources": canonical_sources,
        "promoted_claims": promoted_claims,
        "provenance_clusters": provenance_clusters,
        "typed_conflicts": typed_conflicts,
        "claim_slot_evidence": claim_slot_evidence,
        "accepted_causal_edges": accepted_causal_edges,
        "candidate_causal_edges": candidate_causal_edges,
        "causal_gaps": causal_gaps,
    }


def _required_tables_for_plan(plan: dict) -> tuple[str, ...]:
    required_tables = list(_BASE_REQUIRED_TABLES)
    if plan["accepted_causal_edges"]:
        required_tables.append("accepted_causal_edge")
    if plan["candidate_causal_edges"]:
        required_tables.append("candidate_causal_edge")
    if plan["causal_gaps"]:
        required_tables.append("causal_gap")
    return tuple(required_tables)


def _dedupe_last_wins(
    records: list[dict],
    key_field: str,
    preserve_first_fields: tuple[str, ...] = (),
) -> list[dict]:
    """按 conflict key 去重，模拟逐条 `ON CONFLICT DO UPDATE` 的“后者覆盖”语义。

    批量 `execute_values` 时，同一条 INSERT 内出现重复 conflict key 会触发
    PostgreSQL 报错（cannot affect row a second time），而逐条执行是后者覆盖前者。
    因此批量前必须先把同 key 记录合并为一条：

    - 默认字段保留最后一条（对应 `DO UPDATE SET ... = EXCLUDED.*` 覆盖语义）。
    - `preserve_first_fields` 中的字段不在 `DO UPDATE SET` 内，逐条时保留第一条，
      这里同样保留第一条。
    """
    merged: dict[str, dict] = {}
    for record in records:
        key = record[key_field]
        if key not in merged:
            merged[key] = dict(record)
        else:
            target = merged[key]
            for field, value in record.items():
                if field in preserve_first_fields:
                    continue
                target[field] = value
    return list(merged.values())


def _missing_tables(cur, plan: dict) -> list[str]:
    table_names = _required_tables_for_plan(plan)
    if not table_names:
        return []
    columns = ", ".join(f"to_regclass('public.{name}')" for name in table_names)
    cur.execute(f"SELECT {columns}")
    row = cur.fetchone()
    return [name for name, value in zip(table_names, row) if value is None]


def _semantic_summary_from_plan(plan: dict) -> dict:
    return {
        "enabled": plan["enabled"],
        "mode": plan["mode"],
        "status": plan["status"],
        "missing_tables": [],
        "canonical_source_records": 0,
        "claim_records": 0,
        "provenance_cluster_records": 0,
        "typed_conflict_records": 0,
        "claim_slot_evidence_records": 0,
        "accepted_causal_edge_records": 0,
        "candidate_causal_edge_records": 0,
        "causal_gap_records": 0,
        "runtime_graph": plan["runtime_graph"],
    }


def persist_semantic_feedback(
    cur,
    *,
    req,
    claim_edges: list[dict],
    claim_verification: dict,
) -> dict:
    plan = plan_semantic_feedback_persistence(
        req=req,
        claim_edges=claim_edges,
        claim_verification=claim_verification,
    )
    summary = _semantic_summary_from_plan(plan)
    if not plan["enabled"]:
        return summary

    missing_tables = _missing_tables(cur, plan)
    if missing_tables:
        summary["status"] = "schema_missing"
        summary["missing_tables"] = missing_tables
        return summary

    canonical_sources = _dedupe_last_wins(plan["canonical_sources"], "canonical_key")
    if canonical_sources:
        execute_values(
            cur,
            """
            INSERT INTO canonical_source (
                canonical_source_id,
                canonical_key,
                canonical_url,
                parent_domain,
                alias_urls,
                status,
                first_session_id,
                last_session_id,
                cluster_hint
            )
            VALUES %s
            ON CONFLICT (canonical_key) DO UPDATE SET
                canonical_url = EXCLUDED.canonical_url,
                parent_domain = EXCLUDED.parent_domain,
                alias_urls = EXCLUDED.alias_urls,
                status = EXCLUDED.status,
                last_session_id = EXCLUDED.last_session_id,
                cluster_hint = COALESCE(EXCLUDED.cluster_hint, canonical_source.cluster_hint)
            """,
            [
                (
                    record["canonical_source_id"],
                    record["canonical_key"],
                    record["canonical_url"],
                    record["parent_domain"],
                    json.dumps(record["alias_urls"], ensure_ascii=False),
                    record["status"],
                    record["first_session_id"],
                    record["last_session_id"],
                    record.get("cluster_hint"),
                )
                for record in canonical_sources
            ],
            template="(%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)",
        )
    summary["canonical_source_records"] = len(canonical_sources)

    promoted_claims = _dedupe_last_wins(
        plan["promoted_claims"],
        "claim_key",
        ("claim_text", "claim_business_id"),
    )
    if promoted_claims:
        execute_values(
            cur,
            """
            INSERT INTO "claim" (
                claim_uid,
                claim_key,
                claim_business_id,
                claim_text,
                subject,
                action,
                time,
                location,
                number,
                version_or_policy_name,
                status,
                promotion_reason,
                first_session_id,
                last_session_id,
                supporting_source_count,
                claim_verdict,
                valid_to,
                superseded_by
            )
            VALUES %s
            ON CONFLICT (claim_key) DO UPDATE SET
                subject = EXCLUDED.subject,
                action = EXCLUDED.action,
                time = EXCLUDED.time,
                location = EXCLUDED.location,
                number = EXCLUDED.number,
                version_or_policy_name = EXCLUDED.version_or_policy_name,
                status = EXCLUDED.status,
                promotion_reason = EXCLUDED.promotion_reason,
                last_session_id = EXCLUDED.last_session_id,
                supporting_source_count = GREATEST("claim".supporting_source_count, EXCLUDED.supporting_source_count),
                claim_verdict = COALESCE(EXCLUDED.claim_verdict, "claim".claim_verdict),
                valid_to = EXCLUDED.valid_to,
                superseded_by = EXCLUDED.superseded_by
            """,
            [
                (
                    record["claim_uid"],
                    record["claim_key"],
                    record["claim_business_id"],
                    record["claim_text"],
                    record["subject"],
                    record["action"],
                    record["time"],
                    record["location"],
                    record["number"],
                    record["version_or_policy_name"],
                    record["status"],
                    record["promotion_reason"],
                    record["first_session_id"],
                    record["last_session_id"],
                    record["supporting_source_count"],
                    record.get("claim_verdict"),
                    record.get("valid_to"),
                    record.get("superseded_by"),
                )
                for record in promoted_claims
            ],
        )
    summary["claim_records"] = len(promoted_claims)

    provenance_clusters = _dedupe_last_wins(
        plan["provenance_clusters"],
        "cluster_key",
        ("cluster_id",),
    )
    if provenance_clusters:
        execute_values(
            cur,
            """
            INSERT INTO provenance_cluster (
                cluster_id,
                cluster_key,
                root_source_id,
                root_source_ids,
                member_source_ids,
                cluster_type,
                confidence,
                rationale,
                last_session_id
            )
            VALUES %s
            ON CONFLICT (cluster_key) DO UPDATE SET
                root_source_id = EXCLUDED.root_source_id,
                root_source_ids = EXCLUDED.root_source_ids,
                member_source_ids = EXCLUDED.member_source_ids,
                cluster_type = EXCLUDED.cluster_type,
                confidence = EXCLUDED.confidence,
                rationale = EXCLUDED.rationale,
                last_session_id = EXCLUDED.last_session_id
            """,
            [
                (
                    record["cluster_id"],
                    record["cluster_key"],
                    record["root_source_id"],
                    json.dumps(record["root_source_ids"], ensure_ascii=False),
                    json.dumps(record["member_source_ids"], ensure_ascii=False),
                    record["cluster_type"],
                    record["confidence"],
                    record["rationale"],
                    record["last_session_id"],
                )
                for record in provenance_clusters
            ],
            template="(%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)",
        )
    summary["provenance_cluster_records"] = len(provenance_clusters)

    typed_conflicts = _dedupe_last_wins(
        plan["typed_conflicts"],
        "conflict_key",
        ("conflict_id", "claim_uid", "claim_business_id", "first_session_id"),
    )
    if typed_conflicts:
        execute_values(
            cur,
            """
            INSERT INTO typed_conflict (
                conflict_id,
                conflict_key,
                claim_uid,
                claim_business_id,
                slot_name,
                conflict_type,
                source_ids,
                conflicting_values,
                severity,
                confidence,
                recommended_action,
                cluster_aware,
                status,
                first_session_id,
                last_session_id,
                valid_to,
                superseded_by
            )
            VALUES %s
            ON CONFLICT (conflict_key) DO UPDATE SET
                slot_name = EXCLUDED.slot_name,
                conflict_type = EXCLUDED.conflict_type,
                source_ids = EXCLUDED.source_ids,
                conflicting_values = EXCLUDED.conflicting_values,
                severity = EXCLUDED.severity,
                confidence = EXCLUDED.confidence,
                recommended_action = EXCLUDED.recommended_action,
                cluster_aware = EXCLUDED.cluster_aware,
                status = EXCLUDED.status,
                last_session_id = EXCLUDED.last_session_id,
                valid_to = EXCLUDED.valid_to,
                superseded_by = EXCLUDED.superseded_by
            """,
            [
                (
                    record["conflict_id"],
                    record["conflict_key"],
                    record["claim_uid"],
                    record["claim_business_id"],
                    record["slot_name"],
                    record["conflict_type"],
                    json.dumps(record["source_ids"], ensure_ascii=False),
                    json.dumps(record["conflicting_values"], ensure_ascii=False),
                    record["severity"],
                    record["confidence"],
                    record["recommended_action"],
                    record["cluster_aware"],
                    record["status"],
                    record["first_session_id"],
                    record["last_session_id"],
                    record.get("valid_to"),
                    record.get("superseded_by"),
                )
                for record in typed_conflicts
            ],
            template="(%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        )
    summary["typed_conflict_records"] = len(typed_conflicts)

    cur.execute("DELETE FROM claim_slot_evidence WHERE session_id = %s", (req.session_id,))
    if plan["claim_slot_evidence"]:
        execute_values(
            cur,
            """
            INSERT INTO claim_slot_evidence (
                session_id,
                claim_business_id,
                claim_uid,
                slot_name,
                slot_value,
                source_id,
                llm_source_id,
                canonical_source_id,
                provenance_cluster_id,
                evidence_snippet,
                page,
                section,
                line,
                snippet_span_type,
                confidence,
                expires_at
            )
            VALUES %s
            """,
            [
                (
                    record["session_id"],
                    record["claim_business_id"],
                    record["claim_uid"],
                    record["slot_name"],
                    record["slot_value"],
                    record.get("source_pk"),
                    record["llm_source_id"],
                    record.get("canonical_source_id"),
                    record.get("provenance_cluster_id"),
                    record.get("evidence_snippet"),
                    record.get("page"),
                    record.get("section"),
                    record.get("line"),
                    record.get("snippet_span_type"),
                    record["confidence"],
                    _SEMANTIC_PROCESS_RETENTION_DAYS,
                )
                for record in plan["claim_slot_evidence"]
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP + (%s || ' days')::interval)",
        )
    summary["claim_slot_evidence_records"] = len(plan["claim_slot_evidence"])

    accepted_causal_edges = _dedupe_last_wins(
        plan["accepted_causal_edges"],
        "edge_id",
        ("edge_id", "first_session_id"),
    )
    if accepted_causal_edges:
        execute_values(
            cur,
            """
            INSERT INTO accepted_causal_edge (
                edge_id,
                from_claim_uid,
                from_claim_id,
                to_claim_uid,
                to_claim_id,
                relation_type,
                time_basis,
                mechanism_claim_ids,
                supporting_source_ids,
                independent_root_count,
                confidence,
                acceptance_reason,
                status,
                first_session_id,
                last_session_id,
                valid_to,
                superseded_by
            )
            VALUES %s
            ON CONFLICT (edge_id) DO UPDATE SET
                from_claim_uid = EXCLUDED.from_claim_uid,
                from_claim_id = EXCLUDED.from_claim_id,
                to_claim_uid = EXCLUDED.to_claim_uid,
                to_claim_id = EXCLUDED.to_claim_id,
                relation_type = EXCLUDED.relation_type,
                time_basis = EXCLUDED.time_basis,
                mechanism_claim_ids = EXCLUDED.mechanism_claim_ids,
                supporting_source_ids = EXCLUDED.supporting_source_ids,
                independent_root_count = EXCLUDED.independent_root_count,
                confidence = EXCLUDED.confidence,
                acceptance_reason = EXCLUDED.acceptance_reason,
                status = EXCLUDED.status,
                last_session_id = EXCLUDED.last_session_id,
                valid_to = EXCLUDED.valid_to,
                superseded_by = EXCLUDED.superseded_by
            """,
            [
                (
                    record["edge_id"],
                    record["from_claim_uid"],
                    record["from_claim_id"],
                    record["to_claim_uid"],
                    record["to_claim_id"],
                    record["relation_type"],
                    record.get("time_basis"),
                    json.dumps(record["mechanism_claim_ids"], ensure_ascii=False),
                    json.dumps(record["supporting_source_ids"], ensure_ascii=False),
                    record["independent_root_count"],
                    record["confidence"],
                    record["acceptance_reason"],
                    record["status"],
                    record["first_session_id"],
                    record["last_session_id"],
                    record.get("valid_to"),
                    record.get("superseded_by"),
                )
                for record in accepted_causal_edges
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)",
        )
    summary["accepted_causal_edge_records"] = len(accepted_causal_edges)

    if plan["candidate_causal_edges"]:
        cur.execute("DELETE FROM candidate_causal_edge WHERE last_session_id = %s", (req.session_id,))
        candidate_causal_edges = _dedupe_last_wins(
            plan["candidate_causal_edges"],
            "edge_id",
            ("edge_id",),
        )
        execute_values(
            cur,
            """
            INSERT INTO candidate_causal_edge (
                edge_id,
                from_claim_uid,
                from_claim_id,
                to_claim_uid,
                to_claim_id,
                relation_type,
                time_basis,
                mechanism_claim_ids,
                supporting_source_ids,
                confidence,
                status,
                last_session_id,
                expires_at
            )
            VALUES %s
            ON CONFLICT (edge_id) DO UPDATE SET
                from_claim_uid = EXCLUDED.from_claim_uid,
                from_claim_id = EXCLUDED.from_claim_id,
                to_claim_uid = EXCLUDED.to_claim_uid,
                to_claim_id = EXCLUDED.to_claim_id,
                relation_type = EXCLUDED.relation_type,
                time_basis = EXCLUDED.time_basis,
                mechanism_claim_ids = EXCLUDED.mechanism_claim_ids,
                supporting_source_ids = EXCLUDED.supporting_source_ids,
                confidence = EXCLUDED.confidence,
                status = EXCLUDED.status,
                last_session_id = EXCLUDED.last_session_id,
                expires_at = EXCLUDED.expires_at
            """,
            [
                (
                    record["edge_id"],
                    record["from_claim_uid"],
                    record["from_claim_id"],
                    record["to_claim_uid"],
                    record["to_claim_id"],
                    record["relation_type"],
                    record.get("time_basis"),
                    json.dumps(record["mechanism_claim_ids"], ensure_ascii=False),
                    json.dumps(record["supporting_source_ids"], ensure_ascii=False),
                    record["confidence"],
                    record["status"],
                    record["last_session_id"],
                    _SEMANTIC_PROCESS_RETENTION_DAYS,
                )
                for record in candidate_causal_edges
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, CURRENT_TIMESTAMP + (%s || ' days')::interval)",
        )
    summary["candidate_causal_edge_records"] = len(plan["candidate_causal_edges"])

    if plan["causal_gaps"]:
        cur.execute("DELETE FROM causal_gap WHERE last_session_id = %s", (req.session_id,))
        causal_gaps = _dedupe_last_wins(
            plan["causal_gaps"],
            "gap_id",
            ("gap_id",),
        )
        execute_values(
            cur,
            """
            INSERT INTO causal_gap (
                gap_id,
                from_claim_uid,
                from_claim_id,
                to_claim_uid,
                to_claim_id,
                gap_type,
                reason,
                supporting_source_ids,
                status,
                last_session_id,
                expires_at
            )
            VALUES %s
            ON CONFLICT (gap_id) DO UPDATE SET
                from_claim_uid = EXCLUDED.from_claim_uid,
                from_claim_id = EXCLUDED.from_claim_id,
                to_claim_uid = EXCLUDED.to_claim_uid,
                to_claim_id = EXCLUDED.to_claim_id,
                gap_type = EXCLUDED.gap_type,
                reason = EXCLUDED.reason,
                supporting_source_ids = EXCLUDED.supporting_source_ids,
                status = EXCLUDED.status,
                last_session_id = EXCLUDED.last_session_id,
                expires_at = EXCLUDED.expires_at
            """,
            [
                (
                    record["gap_id"],
                    record["from_claim_uid"],
                    record["from_claim_id"],
                    record["to_claim_uid"],
                    record["to_claim_id"],
                    record["gap_type"],
                    record["reason"],
                    json.dumps(record["supporting_source_ids"], ensure_ascii=False),
                    record["status"],
                    record["last_session_id"],
                    _SEMANTIC_PROCESS_RETENTION_DAYS,
                )
                for record in causal_gaps
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, CURRENT_TIMESTAMP + (%s || ' days')::interval)",
        )
    summary["causal_gap_records"] = len(plan["causal_gaps"])
    summary["status"] = "written"
    return summary


def persist_semantic_feedback_best_effort(
    *,
    req,
    claim_edges: list[dict],
    claim_verification: dict,
) -> dict:
    plan = plan_semantic_feedback_persistence(
        req=req,
        claim_edges=claim_edges,
        claim_verification=claim_verification,
    )
    summary = _semantic_summary_from_plan(plan)
    if not plan["enabled"]:
        return summary

    logger.info(
        "semantic persistence planned, session_id=%s mode=%s canonical_sources=%s claims=%s provenance_clusters=%s typed_conflicts=%s claim_slot_evidence=%s candidate_causal_edges=%s accepted_causal_edges=%s causal_gaps=%s",
        getattr(req, "session_id", None),
        plan["mode"],
        len(plan["canonical_sources"]),
        len(plan["promoted_claims"]),
        len(plan["provenance_clusters"]),
        len(plan["typed_conflicts"]),
        len(plan["claim_slot_evidence"]),
        len(plan["candidate_causal_edges"]),
        len(plan["accepted_causal_edges"]),
        len(plan["causal_gaps"]),
    )
    try:
        with get_write_connection(role="primary", reason="semantic_storage.persist") as conn:
            with conn.cursor() as cur:
                result = persist_semantic_feedback(
                    cur,
                    req=req,
                    claim_edges=claim_edges,
                    claim_verification=claim_verification,
                )
                logger.info(
                    "semantic persistence finished, session_id=%s status=%s canonical_source_records=%s claim_records=%s provenance_cluster_records=%s typed_conflict_records=%s claim_slot_evidence_records=%s candidate_causal_edge_records=%s accepted_causal_edge_records=%s causal_gap_records=%s missing_tables=%s",
                    getattr(req, "session_id", None),
                    result.get("status"),
                    result.get("canonical_source_records"),
                    result.get("claim_records"),
                    result.get("provenance_cluster_records"),
                    result.get("typed_conflict_records"),
                    result.get("claim_slot_evidence_records"),
                    result.get("candidate_causal_edge_records"),
                    result.get("accepted_causal_edge_records"),
                    result.get("causal_gap_records"),
                    ",".join(result.get("missing_tables", [])) or "-",
                )
                return result
    except Exception as exc:
        logger.exception(
            "semantic storage best-effort persistence failed, session_id=%s",
            getattr(req, "session_id", None),
        )
        summary["status"] = "failed_open"
        summary["reason"] = f"{type(exc).__name__}: {exc}"
        return summary
