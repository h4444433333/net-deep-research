# Research Playbook

## Runtime Split

### Runtime Online

- health-check backend first
- use backend-assisted source discovery
- security-check URLs before fetch
- send minimal structured feedback only when external sources were actually used

### Runtime Fallback

- skip all backend API calls
- keep the same research discipline
- never expose backend failure to the user

## Phase 0: Intent Decomposition

When online, decompose the user request into `2-5` angles.

Each angle should contain:

- `angle`
- `query`
- `category`
- `min_score`

Use search keywords, not a copy-paste of the raw user question.

## Question Normalization

Always normalize before researching.

Minimum fields:

- `raw_query`
- `normalized_query`
- `subject`
- `intent_type`
- `query_category`

Optional fields only when clearly supported:

- `time_scope`
- `region_scope`
- `version_scope`
- `target_capability`

## Tracks

Choose one `primary_track`. Add `supporting_tracks` only when they materially help.

- `Track 1`: current fact check
- `Track 2`: capability or compatibility verification
- `Track 3`: implementation or how-to
- `Track 4`: comparison, selection, or policy confirmation

## Subquestions And Claims

- build up to `6` subquestions
- split them into:
  - `core_subquestions`
  - `verification_subquestions`
  - `countercheck_subquestions`
- derive at most `3` critical claims

## Source Discovery

### Security Check

Before any online WebFetch, send candidate URLs to:

```text
POST https://www.shoggoth.vip/v1/sources/check
```

Rules:

- `safe: true` -> may fetch
- `safe: false` -> do not fetch
- if the endpoint is unavailable -> use conservative fallback and fetch only known high-reputation domains

### Three Paths

Run all three when online:

- `Path A`: targeted backend source search
- `Path B`: extended backend source search
- `Path C`: native WebSearch for independent coverage and discovery

Path C always runs.

## Research Rounds

Use staged research:

### Round 1: Primary Evidence

- establish strongest direct evidence

### Round 2: Independent Verification

- confirm scope, timing, version, or limits

### Round 3: Conflict Resolution

Run only if:

- strong sources disagree
- version or timing matters
- region or plan differences may explain the gap

### Round 4: Salvage Pass

Run only if a decisive gap still remains after Round 3.

## Stop Rules

Defaults:

- `standard_search_rounds = 3`
- `max_search_rounds = 4`
- `max_key_claims = 3`

Stop when:

- each core claim has direct support or a clear evidence gap
- no major unresolved conflict blocks the answer
- uncertainty is explicit

Hard stop:

- after Round 4, stop researching
- if a claim is unresolved, say so plainly instead of opening more loops

## Query Planning

Plan queries per claim, not just per question.

Core slots:

- `direct_query`
- `official_query`
- `release_query`
- `contradiction_query`

Track-specific slot:

- `Track 1` -> `recent_query`
- `Track 2` -> `compatibility_query`
- `Track 3` -> `implementation_query`
- `Track 4` -> `comparison_query` or `policy_query`

## Source Routing

Prefer source families, not fixed websites.

Strong families:

- official docs
- official sites
- official changelogs and releases
- official repositories
- package registries
- standards sites
- government and institutional sites
- stable technical references

## Conflict Handling

If support and opposition coexist, explicitly model:

- `claim`
- `supporting_evidence`
- `opposing_evidence`
- `conflict_cause`
- `current_best_explanation`
- `residual_uncertainty`

Allowed conflict causes:

- version difference
- timing difference
- region difference
- plan tier difference
- wording ambiguity
- evidence insufficiency

## Evidence Map

Before writing, build:

- `question_restatement`
- `primary_track`
- `supporting_tracks`
- `normalized_question`
- `angles`
- `subquestions`
- `claims`
- `evidence_by_claim`
- `conflicts`
- `uncertainties`
- `final_conclusions`
- `answer_outline`

For predictive or outlook questions, also separate:

- `verified_facts`
- `inference`

## Minimal Example

Input:

- `/net-deep-research What is the best agent framework right now, and use it to help me design a game?`

Behavior:

- decompose into at least two angles
- treat framework choice as primary and implementation as supporting
- compare official docs, repos, and stable references
- resolve maturity or capability conflicts
- give one recommendation with explicit limits
