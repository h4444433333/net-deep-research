---
name: net-deep-research
description: Perform deep multi-source internet research for complex web truth-finding tasks. Prefer explicit /net-deep-research invocation. Without the command, activate only for deep online verification, cross-source fact checking, authenticity checks, or complex web research where ordinary browsing is insufficient. Do not use for routine web lookups or simple current-info queries.
---

# Net Deep Research

Bundle version: `1.0.8`

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
10. if external sources were actually used, send the default minimal structured feedback record

Keep the workflow principle short and stable:

- multi-round
- multi-angle
- conflict-aware

## Feedback Boundary

Default public flow:

- if external sources were fetched and used -> send `POST /v1/research-feedback`
- if no external sources were fetched -> skip backend record by default
- do not send raw query text, full answer text, or `offnet-analysis` in the default public flow

Explicit high-sensitivity mode:

- only when the user explicitly requests a diagnostic path
- may use `POST /v1/offnet-analysis`
- may include raw query text or full answer text when the explicit diagnostic actually requires them

Explicit vote mode:

- `POST /v1/sources/vote` is not a default closing step
- only use it when the user explicitly wants to submit a trust/untrust vote

## User-Facing Output Constraints

- never expose backend health checks, routing, retries, logs, payloads, or transport diagnostics
- only surface user-relevant research findings, source evidence, uncertainty, and source reputation signals
- do not narrate the internal workflow step by step in the final answer

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
