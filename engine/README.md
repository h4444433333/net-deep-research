# Net Deep Research — Engine

Reference backend for the [`net-deep-research`](https://github.com/h4444433333/net-deep-research) skill.

This directory contains the full engine that powers the skill: source reputation,
structured claim verification, numeric-fact checking, typed conflict detection, and
causal synthesis. It is the code that sits behind the `POST /v1/research-feedback`
endpoint described in `SKILL.md`.

The engine is deliberately split into two layers so you can reuse the algorithmic
core without pulling in the full hosting stack.

| Layer | What it is | External dependencies |
| ----- | ---------- | --------------------- |
| **Pure-logic core** | The portable algorithms (data models, query normalization, numeric verification, quality scoring). | `pydantic` only |
| **Reference backend** | The complete Flask web service with persistence, caching, and background jobs. | `pydantic`, `flask`, `gunicorn`, `psycopg2-binary`, `redis` + a running PostgreSQL and Redis |

---

## Repository layout

```text
engine/
├── main.py                 # Flask entrypoint, Aliyun FC handler, route dispatch
├── requirements.txt        # full backend dependencies
├── models/                 # pydantic request/response contracts (pure logic)
├── services/               # business logic (pure-logic core + infra services)
├── db/                     # PostgreSQL access, schema, seed data, scaling policy
├── cache/                  # Redis clients (cache + state)
├── repositories/           # data-access repositories
├── handlers/               # HTTP route handlers
├── jobs/                   # background job scheduler
└── utils/                  # logging, tracing, health probes
```

---

## Dependency tiers

### Tier 1 — pure-logic core (no PostgreSQL, no Redis)

These modules import only the Python standard library and `pydantic`. You can copy
them into any project and use them directly.

- `models/source.py`
- `services/query_normalizer.py`
- `services/numeric_verification.py`
- `services/quality_scorer.py`

### Tier 2 — reference backend (needs PostgreSQL + Redis + Flask)

Everything else. These modules read configuration from environment variables and
talk to PostgreSQL/Redis, so they are the "hosted" part of the open-core split.

---

## Module reference

### `models/`

- **`source.py`** — the single pydantic contract for the whole feedback API. Defines
  `SourceResponse`, `VoteRequest`, `FeedbackSource`, `ClaimItem`, `NumericFactItem`,
  `ClaimEvidenceEdgeItem`, `ClaimSlotEvidenceItem`, `TypedConflictItem`,
  `CandidateCausalEdgeItem`, `CausalGapItem`, `QueryNormalizationInput`, and the
  top-level `FeedbackRequest`. Enforces strict input validation: unknown fields are
  rejected, whitespace is stripped, and a `numeric_facts` payload is mandatory whenever
  `claim.number` is set or an evidence edge declares `"number"` in `supported_slots`.

### `services/`

Pure-logic core:

- **`query_normalizer.py`** — reduces a free-text research question into stable
  structured fields (`subject`, `target_capability`, `time_scope`, `region_scope`,
  `version_scope`, `intent_type`, `query_category`, `topic_tags`) using regex and
  `unicodedata`. This is what lets the backend match a session even when the raw
  `query` text is absent.
- **`numeric_verification.py`** — numeric-fact normalization and comparison. Parses
  `value_raw`, normalizes units (`亿`/`万`/`%`/`元`), derives
  `value_norm`/`range_min`/`range_max`/`metric_signature`/`unit_family`/`comparator`,
  then decides comparability, evaluates each evidence edge, and aggregates the whole
  edge set into a consensus verdict (`hard_conflict`, `independent_consensus`,
  `source_divergence`, `same_root_duplicate`, `insufficient_numeric_evidence`).
- **`quality_scorer.py`** — language-agnostic article-quality scorer: character
  n-grams + feature hashing + multinomial Naive Bayes, persisted in a local SQLite
  file (no external DB). Cold-starts from `train_seed(high_texts, low_texts)` and is
  updated incrementally with `update_high`/`update_low`.

Infra-dependent services:

- **`reputation.py`** — Bayesian source-reputation aggregation. Combines trust/untrust
  votes with implicit signals (contradictions, `verifiable_carrier`, `exact_match`,
  `independent_consensus`) into `alpha`/`beta`/`reputation_score`/`confidence`, and
  flushes Redis-pending votes into PostgreSQL.
- **`topic_reputation.py`** — per-`(source_id, topic_tag)` Bayesian specialization
  scores, so a source can be trusted on one topic but not another.
- **`content_type_reputation.py`** — per-`content_type` reliability scores.
- **`semantic_storage.py`** — the core persistence layer. Plans and persists a
  `FeedbackRequest` into `canonical_source`, `claim`, `provenance_cluster`,
  `typed_conflict`, `claim_slot_evidence`, `candidate_causal_edge`, `causal_gap` in a
  single transaction (with best-effort fallback on failure).
- **`claim_evidence_store.py`** — inserts `claim → evidence` edges.
- **`tag_taxonomy.py`** — static tag definitions (100+ canonical tags), alias
  resolution, and dynamic tag merge/prune.
- **`source_signal_rollup.py`** — rolls daily signals into
  daily → monthly → quarterly → yearly aggregates.
- **`feedback_write_queue.py`** — Redis Streams producer for async feedback writes
  (Lua-based atomic dedup + `XADD`).
- **`feedback_write_worker.py`** — Redis Streams consumer (`XACK`, retry, dead-letter
  queue).
- **`request_guard.py`** — request rate-limiting rules.
- **`runtime_metrics.py`** — Redis-backed runtime metrics.
- **`test_access.py`** — access control for the test runtime (token + allowed-client
  gating via environment variables).

Windowed retention jobs (each deletes old rows for one table):

- **`article_retention.py`**, **`claim_evidence_retention.py`**,
  **`legacy_daily_stats_retention.py`**, **`llm_preference_retention.py`**,
  **`reputation_changelog_retention.py`**, **`semantic_process_retention.py`**,
  **`vote_retention.py`**.

### `db/`

- **`connection.py`** — psycopg2 `ThreadedConnectionPool` with read/write split and
  logical role routing (`primary` / `process` / `content` / `analytics`), connection
  probing, and stale-connection discard.
- **`scaling_policy.py`** — declarative, data-only policies for table placement,
  cross-database constraints, lifecycle/retention, and cutover stages. No I/O.
- **`schema.sql`** — full PostgreSQL DDL (sources, votes, semantic tables, rollups,
  simhash buckets, taxonomy tables).
- **`seed.sql`** — 30 high-frequency seed sources for a cold-start reputation baseline.

### `cache/`

- **`redis_client.py`** — two Redis clients: a **cache** Redis (source cache with TTL)
  and a **state** Redis (pending votes, rate-limit counters, runtime metrics).

### `repositories/`

- **`source_repository.py`** — source query and `ensure_source`.
- **`article_repository.py`** — `article_sources` and simhash-bucket dedup.
- **`feedback_repository.py`** — source-reputation snapshots and LLM preference
  storage.

### `handlers/`

- **`check.py`** — `POST /v1/sources/check`: URL safety scan (SSL + Safe Browsing +
  XSS).
- **`sources.py`** — source query/search.
- **`vote.py`** — `POST /v1/sources/vote`: writes vote to Redis first, flushed to
  PostgreSQL asynchronously.
- **`feedback.py`** — `POST /v1/research-feedback`: the main semantic feedback
  ingestion endpoint.
- **`offnet.py`** — `POST /v1/offnet-analysis`: offline support-structure / risk
  analysis.

### `jobs/`

- **`scheduler.py`** — background job scheduler (vote flush, reputation recalc,
  security rescan, dead-link cleanup, tag governance, signal rollup, retention
  cleanups). Only starts when `ENABLE_BACKGROUND_JOBS=1`.

### `utils/`

- **`logger.py`** — Beijing-time (UTC+8) millisecond logging to stdout and files.
- **`request_trace.py`** — `ContextVar`-based request tracing plus sanitized
  `user.log` nodes (the `semantic_preview` anchor used for request lock-in).
- **`health_probe.py`** — health-probe counting and periodic summary.

### `main.py`

- `create_app()` — Flask/Docker entrypoint (used by the `gunicorn` command).
- `handler(event, context)` — Aliyun Function Compute entrypoint.
- `_route()` — path → handler dispatch.

---

## Quickstart

### 1. Pure-logic core (no database)

The algorithmic core needs only `pydantic`. The modules use `engine/` as the import
root (e.g. `from services.query_normalizer import ...`), so put `engine/` on
`sys.path`.

```bash
cd engine
python -m venv .venv && source .venv/bin/activate
pip install "pydantic>=2.0,<3.0"
```

```python
import sys
sys.path.insert(0, ".")  # engine/ is the import root

from services.query_normalizer import normalize_query
from services.numeric_verification import normalize_numeric_fact, compare_numeric_facts
from services.quality_scorer import QualityScorer
from models.source import FeedbackRequest

# 1) Query normalization
norm = normalize_query("2026年北京公办本科录取率是多少？")
print(norm["subject"], norm["region_scope"], norm["intent_type"])

# 2) Numeric verification
a = normalize_numeric_fact({"subject": "录取率", "metric": "录取率",
                            "value_raw": "60%", "unit": "%"})
b = normalize_numeric_fact({"subject": "录取率", "metric": "录取率",
                            "value_raw": "61%", "unit": "%"})
print(compare_numeric_facts(a, b))

# 3) Quality scoring (cold start, SQLite-backed, no external DB)
scorer = QualityScorer()
scorer.train_seed(high_texts=["well written official docs ..."],
                  low_texts=["clickbait spam ..."])
print(scorer.is_ready(), scorer.score("a short test sentence"))
```

### 2. Full backend (PostgreSQL + Redis)

1. Install dependencies:

   ```bash
   cd engine
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Start PostgreSQL and Redis (any host; the engine reads connection settings from
   environment variables — see `db/connection.py` and `cache/redis_client.py`).

3. Create the schema and seed the baseline:

   ```bash
   psql "$DB_NAME" -f db/schema.sql
   psql "$DB_NAME" -f db/seed.sql
   ```

4. Run the API:

   ```bash
   gunicorn --bind 0.0.0.0:5000 --workers 2 --threads 4 \
     --worker-class gthread main:create_app
   ```

5. Verify:

   ```bash
   curl http://localhost:5000/health
   ```

The production image uses the same command (see `Dockerfile` in the API bundle), so
the hosted service at `https://www.shoggoth.vip` is byte-for-byte the same code path.

---

## Requirements

`engine/requirements.txt` (full backend):

```text
flask>=3.0,<4.0
gunicorn>=22.0,<24.0
psycopg2-binary>=2.9,<3.0
redis>=4.0,<6.0
pydantic>=2.0,<3.0
```

| Scope | Minimum install |
| ----- | --------------- |
| Pure-logic core only | `pip install "pydantic>=2.0,<3.0"` |
| Full backend | `pip install -r requirements.txt` + PostgreSQL + Redis |

Environment variables are read from the process environment (never hard-coded).
Key ones: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` (default
`db_reputation`), `REDIS_CACHE_*`, `REDIS_STATE_*`, `NET_INFO_RUNTIME_ENV`,
`NET_INFO_LOG_DIR`, `ENABLE_BACKGROUND_JOBS`.
