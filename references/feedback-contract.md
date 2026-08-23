# Feedback Contract

Bundle context: `net-deep-research-github-1.1.0`

## Default Public Flow

Use backend feedback only when external sources were actually fetched and used.

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
