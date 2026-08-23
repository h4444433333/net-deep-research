# Feedback Contract

Bundle context: `net-deep-research`

## Default Public Flow

Whenever at least one external source was fetched, backend feedback is MANDATORY — skipping the submission is a protocol violation.

Default rule:

- send `POST /v1/research-feedback`
- do not send `query`
- do not send `final_answer`
- do not send `offnet-analysis`

Default payload keeps only the minimum structured evidence layer:

- `payload_version`
- `session_id`
- `sources`
- `claims`
- `claim_evidence_edges`
- `typed_conflicts` (required key; `[]` only when no conflict exists)
- `candidate_causal_edges` (required key; `[]` only when no causal statement exists)
- `causal_gaps` (required key; `[]` only when no mechanism gap exists)
- `provenance_edges` when applicable
- `contradictions` when applicable
- `query_normalization`
- `session_confidence`
- `preference_blob`

## High-Sensitivity Mode

Use only when the user explicitly requests a diagnostic path.

Then you may:

- call `POST /v1/offnet-analysis`
- or send explicit full `research-feedback`
- include `query` or `final_answer` only when the requested diagnostic actually needs them

## research-feedback Example

```json
{
  "payload_version": "v2",
  "session_id": "7b54f4ff-9f83-45e5-9724-7ef8d836a7dd",
  "sources": [
    {
      "source_id": "src_001",
      "url": "https://react.dev/blog/2024/12/05/react-19",
      "domain": "react.dev",
      "title": "React 19 Release",
      "content_summary": "Official release notes covering React 19 features and rollout.",
      "topic_tags": ["react", "release"],
      "accessible": true,
      "http_status": 200,
      "content_type": "official_blog",
      "content_date": "2024-12-05",
      "content_age_days": 220,
      "impersonation_risk": 0.0,
      "has_paywall": false,
      "has_login_wall": false,
      "document_form": "release_note",
      "is_official_like": true,
      "structured_markers": ["date", "version"],
      "is_derivative": false,
      "selected_as_evidence": true,
      "cited_in_final": true,
      "citation_count": 2,
      "contribution_weight": 0.4,
      "support_claim_ids": ["c1"],
      "discard_reason": null
    }
  ],
  "claims": [
    {
      "claim_id": "c1",
      "text": "React 19 is officially released and documented by react.dev",
      "subject": "React 19",
      "action": "is officially released",
      "time": "December 2024",
      "numeric_facts": [],
      "supported_by": ["src_001"]
    }
  ],
  "claim_evidence_edges": [
    {
      "claim_id": "c1",
      "source_id": "src_001",
      "stance": "support",
      "evidence_snippet": "React 19 is now stable. Released on December 5, 2024.",
      "support_score": 0.92,
      "source_tier": "primary",
      "trace_depth": 0,
      "supported_slots": ["subject", "action", "time"],
      "snippet_span_type": "original_sentence",
      "numeric_facts": [],
      "used_in_final": true
    }
  ],
  "provenance_edges": [],
  "contradictions": [],
  "typed_conflicts": [],
  "candidate_causal_edges": [],
  "causal_gaps": [],
  "query_normalization": {
    "query_category": "technical_framework_selection",
    "topic_tags": ["software_tools", "engineering"]
  },
  "session_confidence": 0.75,
  "preference_blob": {
    "query_category": "technical_framework_selection",
    "source_usefulness_ratings": {
      "src_001": 0.9
    },
    "answer_quality_gap": "SSR production performance was not independently verified beyond the official announcement"
  }
}
```

## offnet-analysis Example

```json
{
  "payload_version": "v2",
  "analysis_mode": "offnet",
  "session_id": "d4d15b31-a418-4278-a6d1-f0cebf6a1c4b",
  "query": "optional raw query only in explicit diagnostic mode",
  "answer_text": "full answer text only in explicit diagnostic mode",
  "claims": [
    {
      "claim_id": "c1",
      "text": "one concrete claim extracted from the answer",
      "supporting_evidence": ["support actually present inside the answer text"],
      "source_basis": ["official source"],
      "confidence": 0.72,
      "risk_flags": []
    }
  ],
  "answer_signals": {
    "has_external_citations": false,
    "has_uncertainty_disclosure": true,
    "has_counterarguments": false,
    "has_structured_reasoning": true
  }
}
```

## Hard Rules

### Raw JSON

- send raw JSON only
- no prose
- no Markdown fences in the actual request body
- no trailing commas
- always send `"payload_version": "v2"`

### Identifier Rules

- `source_id` uses `src_001`, `src_002`, ...
- `claim_id` uses `c1`, `c2`, ...
- all cross-references must point to existing ids

### Structural Rules

- when `claims` is non-empty, `claim_evidence_edges` must be explicitly present
- `claim_evidence_edges[*].stance` only: `support`, `oppose`, `partial`
- `claim_evidence_edges[*].source_tier` only: `primary`, `secondary`, `tertiary`
- `provenance_edges[*].relation` only: `derived_from`
- `domain` must be a bare hostname, not a full URL

### Negative Evidence Rules (mandatory)

- every fetched source must be listed in `sources`; a fetched-but-not-adopted source MUST set a non-null `discard_reason` from: `contradiction`, `contradiction_unresolved`, `derivative_only`, `low_quality`, `outdated`, `unsupported`
- when any source contradicts a claim, at least one edge for that claim MUST have `stance: "oppose"` with the contradicting snippet in `evidence_snippet`
- when sources disagree on the same fact/metric, `typed_conflicts` MUST be present with `conflicting_values` (one per source reading) and `resolution` (`null` when unresolved)
- do not silently drop conflicting or unused sources — silent omission is treated as a contract violation by downstream auditing

### Source Rules

- mandatory v2 source fields: `content_type`, `document_form`, `is_official_like`, `structured_markers`, `is_derivative`
- `content_type` only: `official_docs`, `official_blog`, `third_party`, `forum`, `social`, `null`
- `document_form` only: `pdf`, `official_notice`, `policy_page`, `release_note`, `spec_page`, `table_page`, `article_page`, `other`
- `structured_markers` only: `date`, `version`, `identifier`, `table`
- never emit `structured_markers: []`

### Claim Rules

- every claim must include `subject` and `action`
- every claim must also include at least one of:
  - `time`
  - `location`
  - `number`
  - `version_or_policy_name`

### Evidence Rules

- `evidence_snippet`, `supported_slots`, and `snippet_span_type` are mandatory
- `supported_slots` only:
  - `subject`
  - `action`
  - `time`
  - `location`
  - `number`
  - `version_or_policy_name`
- `snippet_span_type` only:
  - `original_sentence`
  - `summary`
  - `table_cell`
  - `title`

### Numeric Sanitization Gate

- symbolic comparators are forbidden
- allowed comparator enums only:
  - `eq`
  - `gt`
  - `gte`
  - `lt`
  - `lte`
  - `range`
  - `approx`
- if a claim has a non-empty `number`, its `numeric_facts` must be present and non-empty
- if a `claim_evidence_edge` has `"number"` in `supported_slots`, its `numeric_facts` must be present and non-empty, extracted from that edge's own snippet
- the edge `numeric_facts` metric signature (`subject` + `metric`) and `unit` should align with the linked claim so the backend can compare them

Edge example (valid):

```json
{
  "claim_id": "c1",
  "source_id": "src_001",
  "stance": "support",
  "evidence_snippet": "Non-local households must pay 1 year of social security.",
  "support_score": 0.9,
  "source_tier": "primary",
  "trace_depth": 0,
  "supported_slots": ["subject", "action", "number"],
  "snippet_span_type": "original_sentence",
  "numeric_facts": [
    {
      "numeric_fact_id": "nf_e1_1",
      "subject": "Beijing non-local households",
      "metric": "social_security_payment_years",
      "value_raw": "1",
      "unit": "years",
      "comparator": "eq"
    }
  ],
  "used_in_final": true
}
```

### Evidence Quality Gate

- `evidence_snippet` must be the most direct passage actually seen
- do not use a page title as snippet unless the title itself is decisive evidence
- if a source is derivative and the parent source is identifiable, add `provenance_edges`
- do not assign a high `support_score` to a snippet that does not directly ground the claim

## Semantic Fields (typed_conflicts / candidate_causal_edges / causal_gaps)

These three keys are REQUIRED in every feedback payload that carries claims. The backend hard-rejects (400) wrong enum values, so copy the allowed lists exactly. Empty arrays are allowed only when the checklist genuinely found nothing.

### typed_conflicts

Recorded when sources disagree on the same claim slot (a number, a date, a value, ...).

- `claim_id` (required): the conflicting claim, e.g. `c1`
- `slot_name` (required): one of `subject`, `action`, `time`, `location`, `number`, `version_or_policy_name`, `claim`
- `conflict_type` (required): one of `value_conflict`, `temporal_conflict`, `logical_conflict`, `derivative_conflict`
- `source_ids` (required, non-empty): canonical ids of the disagreeing sources, e.g. `["src_001", "src_003"]`
- `conflicting_values` (required, non-empty): one reading per source, e.g. `["5.2%", "4.8%"]`
- `severity` (optional, default `medium`): `low` | `medium` | `high`
- `confidence` (optional, default `0.5`): 0.0-1.0
- `recommended_action` (optional), `cluster_aware` (optional, default `true`)

```json
{
  "typed_conflicts": [
    {
      "claim_id": "c1",
      "slot_name": "number",
      "conflict_type": "value_conflict",
      "source_ids": ["src_001", "src_003"],
      "conflicting_values": ["5.2%", "4.8%"],
      "severity": "medium",
      "confidence": 0.7
    }
  ]
}
```

When a `typed_conflicts` entry exists, the conflicting source MUST also appear as at least one `claim_evidence_edge` with `stance: "oppose"`.

### candidate_causal_edges

Recorded when the research surfaces a causal statement ("X causes / leads to / results in Y"). These are candidates, not facts — the backend only promotes them after independent cross-session observations, so low confidence is fine and expected.

- `from_claim_id` / `to_claim_id` (required): cause claim -> effect claim, both must exist in `claims`
- `relation_type` (required): one of `caused`, `influenced`, `precedent_for`
- `time_basis` (optional): when the causal link holds, e.g. `2026 flood season`
- `mechanism_claim_ids` (optional): claim ids explaining the mechanism; `[]` when unknown
- `supporting_source_ids` (optional): sources backing the link
- `confidence` (optional, default `0.5`): 0.0-1.0

```json
{
  "candidate_causal_edges": [
    {
      "from_claim_id": "c1",
      "to_claim_id": "c2",
      "relation_type": "caused",
      "time_basis": "2026 flood season",
      "mechanism_claim_ids": [],
      "supporting_source_ids": ["src_001"],
      "confidence": 0.5
    }
  ]
}
```

### causal_gaps

Recorded when a correlation is observed but the mechanism / time anchor / independent support is missing.

- `from_claim_id` / `to_claim_id` (required): the correlated claims
- `gap_type` (required): one of `missing_mechanism`, `missing_time_anchor`, `insufficient_independent_support`
- `reason` (required): one sentence explaining what is missing
- `supporting_source_ids` (optional)

```json
{
  "causal_gaps": [
    {
      "from_claim_id": "c1",
      "to_claim_id": "c2",
      "gap_type": "missing_mechanism",
      "reason": "Both sources report the correlation but neither explains the transmission channel.",
      "supporting_source_ids": ["src_001"]
    }
  ]
}
```

## preference_blob Contract

This field distinguishes LLM utility signals from explicit user votes.

Required shape:

- `query_category`
- `source_usefulness_ratings`
- `answer_quality_gap`

Usefulness scoring guide:

- `0.9-1.0` -> direct primary source confirming the core conclusion
- `0.7-0.9` -> strong independent support
- `0.5-0.7` -> useful secondary support
- `0.3-0.5` -> weak support
- `0.0-0.3` -> should not be relied on
