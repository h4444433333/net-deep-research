---
name: net-deep-research
description: Perform deep multi-source internet research for complex web truth-finding tasks. Prefer explicit /net-deep-research invocation. Without the command, activate only for deep online verification, cross-source fact checking, authenticity checks, or complex web research where ordinary browsing is insufficient. Do not use for routine web lookups or simple current-info queries.
---

# Net Deep Research

Bundle version: `1.0.9`

This skill uses the remote backend API at `https://www.shoggoth.vip`.

## Capability Summary

- accesses the public web for research
- calls an external backend API
- performs URL safety checks before fetching
- sends a minimal structured research record after external-source runs
- can send explicit high-sensitivity diagnostics or explicit user votes only when separately requested

## User Notice

- During the default feedback workflow, this skill may transmit cited source metadata, structured evidence links, query classification, and usefulness signals to an external backend for source auditing and quality analysis.
- Raw query text, full answer text, offnet answer audits, and trust/untrust votes are not part of the default public flow; they are sent only when the user explicitly requests a high-sensitivity diagnostic or an explicit vote action.

## Trigger

This skill prefers the explicit `/net-deep-research` command.

If the user message starts with `/net-deep-research`:

- remove the matched command prefix
- trim whitespace
- treat the remainder as the actual research question

If the user does not use `/net-deep-research`, activate this skill only when the request clearly needs deep online truth-finding rather than ordinary web lookup. Typical cases:

- asks for deep web search across multiple sources rather than a quick current fact
- asks to verify whether online information is true, false, misleading, or poorly sourced
- asks to distinguish verified facts from rumor, inference, marketing claims, or stale summaries
- asks for cross-source verification on policy, technical compatibility, product status, or public claims where a shallow answer would be unsafe

Do not activate this skill for:

- ordinary web browsing or simple latest-info lookups
- one-source factual checks that do not require cross-verification
- routine questions that can be answered well without deep multi-source web research

## Runtime

Start by checking `GET https://www.shoggoth.vip/health`.

- `200 OK` -> `Runtime Online`
- unreachable or timeout (> 3s) -> `Runtime Fallback`

Fallback principle: backend failure must not block user answer; fallback silently.

In `Runtime Fallback`, keep the same research discipline but skip all backend API calls.

## Research Workflow

When this skill is triggered, do not answer immediately.

Run this workflow:

1. normalize the query into stable structured fields
2. restate the question in one sentence
3. decompose into multiple angles or subquestions
4. choose one primary research track and supporting tracks only when needed
5. discover sources through backend-assisted search when online, plus native web search as independent coverage
6. security-check all candidate URLs before fetching when online
7. research in multiple rounds and compare sources across angles
8. resolve conflicts or state them plainly
9. write the answer from a structured evidence map
10. if external sources were actually used, submit the structured record to `POST /v1/research-feedback`, including `claims`, `claim_evidence_edges`, and when derivable also `claim_slot_evidences`, `typed_conflicts`, `candidate_causal_edges`, `causal_gaps`

Keep the workflow principle short and stable:

- multi-round
- multi-angle
- conflict-aware

## Feedback Boundary

Default public flow:

- if external sources were fetched and used -> send `POST /v1/research-feedback`
- if no external sources were fetched -> skip backend record by default
- do not send raw query text, full answer text, or `offnet-analysis` in the default public flow
- when the evidence map is rich enough, prefer explicit semantic fields over leaving all semantic synthesis to the backend

Explicit high-sensitivity mode:

- only when the user explicitly requests a diagnostic path
- may use `POST /v1/offnet-analysis`
- may include raw query text or full answer text when the explicit diagnostic actually requires them

Explicit vote mode:

- `POST /v1/sources/vote` is not a default closing step
- only use it when the user explicitly wants to submit a trust/untrust vote

## Numeric Facts Requirement

The backend hard-rejects (400) any research-feedback payload where a numeric slot is present but `numeric_facts` is missing. Generate `numeric_facts` wherever the rule applies.

### Trigger

- A claim with a non-empty `number` field MUST include at least one entry in its `numeric_facts`.
- A `claim_evidence_edge` with `"number"` in `supported_slots` MUST include at least one entry in its `numeric_facts`.

### Field shape

Each `numeric_fact` entry:

- `numeric_fact_id` (required): unique id with `nf_` prefix, e.g. `nf_c1_1`
- `subject` (required): entity the number belongs to (align with the claim `subject`)
- `metric` (required): metric name, e.g. `social_security_payment_years`, `new_home_price_mom`
- `value_raw` (required): the raw number, e.g. `1`, `3`, `0.2%`, or a range `2-3`
- `unit` (required, non-empty): e.g. `years`, `%`, `CNY`, `units`, `percentage_points`
- `comparator` (optional, default `eq`): one of `eq`, `gt`, `gte`, `lt`, `lte`, `range`, `approx`
- optional: `time`, `location`, `scope`, `evidence_span`

### claim vs edge meaning

- `claim.numeric_facts`: the number asserted by the claim text.
- `edge.numeric_facts`: the number extracted from that edge's source snippet.

The backend compares them only when `subject` + `metric` (metric signature) and `unit` both match.

### Avoid false triggers

`number` is for measurable values only. Put document codes and policy names into `version_or_policy_name` (e.g. `BJJD-2026-400`) and bare dates into `time` — not `number` — so `numeric_facts` stays meaningful.

Claim example:

```json
{
  "claim_id": "c1",
  "number": "1",
  "numeric_facts": [
    {
      "numeric_fact_id": "nf_c1_1",
      "subject": "Beijing non-local households",
      "metric": "social_security_payment_years",
      "value_raw": "1",
      "unit": "years",
      "comparator": "eq"
    }
  ]
}
```

Edge example:

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

When an edge declares `"number"` in `supported_slots`, it MUST fill `edge.numeric_facts` even if the linked claim already has `numeric_facts`. Keep the edge `subject` + `metric` (metric signature) and `unit` aligned with the claim so the backend comparison succeeds.

## User-Facing Output Constraints

- never expose backend health checks, routing, retries, logs, payloads, or transport diagnostics
- only surface user-relevant research findings, source evidence, uncertainty, and source reputation signals
- do not narrate the internal workflow step by step in the final answer
- separate the machine-side structured feedback (what is submitted to the backend) from the human-facing answer (what the user reads); never dump the raw `sources` / `claims` / `claim_evidence_edges` payload into the answer
- never expose internal identifiers in the human-facing answer: no `src_*` / `claim_*` / `edge_*` / `node_*` citation ids or machine keys — reference sources only by readable name, domain, and type (e.g. official / media / derivative / secondhand)

## Final Answer Shape

Default section order:

1. `Question Restatement`
2. `Short Answer`
3. `Key Findings`
4. `Cross-Source Notes`
5. `Uncertainties or Limits`
6. `Sources`
7. `Explain Why`

For predictive or outlook questions, split `Verified Facts` and `Inference`.

## Minimal Example

Input:

- `/net-deep-research Is Bun production-ready for large Next.js deployments in 2026?`

Expected behavior:

- normalize the query
- compare official docs, releases, and strong independent references
- resolve version or deployment-scope conflicts
- answer with evidence and uncertainty
- if external sources were used, submit the default minimal structured feedback record

## References

Detailed implementation rules live here:

- `references/feedback-contract.md` — full `research-feedback` and `offnet-analysis` contract
- `references/source-scoring.md` — backend reputation layer and 6-dimension source scoring
- `references/research-playbook.md` — research rounds, query planning, routing, and stop rules
- `references/writing-rules.md` — output format, `Explain Why`, and writing constraints

Read the relevant reference file before using its corresponding subsystem.
