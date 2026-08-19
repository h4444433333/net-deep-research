# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Source-Aware Research](https://img.shields.io/badge/research-source--aware-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Evidence Feedback](https://img.shields.io/badge/evidence-structured-orange)](./SKILL.md)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## A Trustworthy Research Skill For AI Agents That Need More Reliable Answers, Not Just More Search.

`Net Deep Research` is a public skill bundle for AI agents that need to verify information on the live web, detect weak or misleading sources, and produce more reliable answers from evidence. It is not built to "search deeper" for its own sake. It is built to reduce the chance of being led astray by a single page, fake data, or unverified claims.

If you are looking for a **reliability-first research skill**, a **source-verification agent**, a **citation-aware RAG validation workflow**, a **research bundle for LLMs that prioritizes trustworthy output**, or a **web research tool that filters dangerous and suspicious URLs before fetching**, this repository is the public package for that job.

Best fit:

- official policy lookup
- framework and tool comparison
- latest-information verification
- citation-sensitive research
- questions where weak sources can ruin the answer
- web research scenarios that should filter dangerous or suspicious URLs first

Install from:

- [ClawHub - Net Deep Research](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub - Versions](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

### Why People Try It

- not just more search, but better filtering of what should not be trusted
- source-aware selection instead of trusting the first page
- URL safety checks before fetch
- cross-checking before commitment
- clearer separation between verified facts and inference
- conflict-aware output instead of fake certainty

### Why It Stands Out

Most "research" prompts still fail in one of two ways:

- they summarize the first few pages and sound confident
- they browse more, but leave no usable evidence structure behind

`Net Deep Research` is designed to avoid both. Its design goal is not "deeper search" as a vanity metric. Its design goal is to catch weak evidence, reduce fake-data drift, and give the user a conclusion that is easier to inspect and trust.

### A More Concrete Example

For example, suppose the user asks: `What is ego lite?`

Without it, the answer often looks like this:

- it guesses from the wording and treats it as some generic "lite" edition of Ego
- it does not first verify whether it is a model, a product, a browser, or something else
- the prose sounds smooth, but the sourcing is too thin to trust confidently

With it, the intended behavior is closer to:

- first resolve what concrete product `ego lite` actually refers to in context
- then cross-check the claim across the official site, README, GitHub, and other strong sources instead of letting one page decide
- finally separate conclusion, evidence, and uncertainty so the user can see what is verified and what is still inference

### Questions You Can Ask

- Is Bun production-ready for large Next.js deployments?
- What is the official Beijing individual social insurance contribution policy this year?
- Which RAG evaluation frameworks are strongest on citation faithfulness?
- What changed in the latest policy draft, and what is still unverified?

## Quick Start

### 1. Install

Preferred:

- Install from the [ClawHub skill page](https://clawhub.ai/h4444433333/skills/net-deep-research)
- Or pick a specific build from the [ClawHub versions page](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

If you use Trae, OpenCode, Claude Code, Codex, Cursor, or OpenClaw, the two LLM-assisted methods below are usually the fastest path. Just send the corresponding prompt to the model and let it handle the installation.

Prompt for online install:

```text
Please read the GitHub repository https://github.com/h4444433333/net-deep-research from the current repository root, and install the bundle formed by SKILL.md, _meta.json, skill-card.md, references/, and tools/ into the skill directory supported by your current host. If your host does not support direct GitHub installation, say that clearly and tell me which installation method it does support. After installation, tell me the install path and whether the host needs a restart or reload.
```

Prompt for local install:

```text
I have already downloaded this skill bundle to /absolute/path/to/net-deep-research-github-1.0.9. Please install SKILL.md and its related files from that local directory into the skill directory supported by your current host. If your host does not support local-directory installation, say that clearly and tell me which installation method it does support. After installation, tell me the install path and whether the host needs a restart or reload.
```

### 2. Reload And Verify

Some hosts need a restart, a skill index refresh, or a new session after installing or updating a skill.

Verify with:

```text
/net-deep-research your question here
```

### 3. Use

Trigger the skill with:

```text
/net-deep-research your question here
```

The preferred trigger is the explicit `/net-deep-research` command.

Without the explicit command, hosts should auto-activate this skill only for cases like:

- deep online research rather than routine web lookup
- truth-checking or authenticity checks on public web information
- cross-source verification where one page should not decide the answer
- questions that need a clear split between verified facts, evidence, and likely inference

It should not auto-activate for:

- ordinary web browsing questions
- simple latest-info lookups
- one-source factual checks
- speed-sensitive questions that do not need a deep verification workflow

Example prompts:

```text
/net-deep-research Is Bun production-ready for large Next.js deployments in 2026?
/net-deep-research Compare the latest RAG evaluation frameworks for citation faithfulness
/net-deep-research What is the official policy for Beijing individual social insurance contributions this year?
```

If installation succeeded but the host does not auto-route by intent, or if you want to force the deep verification path, explicitly invoke:

```text
/net-deep-research your question here
```

## What You Get

### Core Capabilities

- 🌐 searches across public web sources before answering
- 🧪 queries an external backend for source reputation support
- 🛡️ performs URL safety checks before fetching
- 🚫 filters dangerous sites, suspicious links, and URLs that should not be fetched
- 🧱 organizes findings into a structured research workflow
- 🔍 cross-checks key claims instead of trusting a single page
- 🧾 makes the evidence behind the conclusion easier to inspect instead of giving an answer that only sounds plausible

### Included Files

- `SKILL.md` - main skill instructions
- `skill-card.md` - short marketplace-style description
- `_meta.json` - package metadata
- `tools/score_stability.py` - local URL stability scoring helper
- `engine/` - the open-source backend engine (see below)

## Why It Feels Better Than Generic Web Search

- ✅ better source discipline
- ✅ clearer separation between verified facts and inference
- ✅ stronger handling of uncertainty and conflict
- ✅ cross-checking makes conclusions less likely to be skewed by a single source
- ✅ reusable skill package format for local or hosted agent environments

## Repository Layout

```text
net-deep-research-github-1.0.9/
├── README.md
├── SKILL.md
├── _meta.json
├── references/
├── skill-card.md
├── engine/                  # open-source backend engine
│   ├── README.md            # module-by-module reference
│   ├── requirements.txt
│   ├── main.py
│   ├── models/
│   ├── services/
│   ├── db/
│   ├── cache/
│   ├── repositories/
│   ├── handlers/
│   ├── jobs/
│   └── utils/
└── tools/
    └── score_stability.py
```

## Engine (Backend)

The `engine/` directory is the open-source backend that powers this skill. It
implements source reputation, structured claim verification, numeric-fact checking,
typed conflict detection, and causal synthesis — the same code that runs behind
`POST /v1/research-feedback` at `https://www.shoggoth.vip`.

A module-by-module reference lives in [engine/README.md](./engine/README.md).

### Quickstart (pure-logic core, no database)

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
```

### Quickstart (full backend)

```bash
cd engine
pip install -r requirements.txt
psql "$DB_NAME" -f db/schema.sql
psql "$DB_NAME" -f db/seed.sql
gunicorn --bind 0.0.0.0:5000 --workers 2 --threads 4 --worker-class gthread main:create_app
```

### Requirements

`engine/requirements.txt`:

```text
flask>=3.0,<4.0
gunicorn>=22.0,<24.0
psycopg2-binary>=2.9,<3.0
redis>=4.0,<6.0
pydantic>=2.0,<3.0
```

Pure-logic core only needs `pydantic`; the full backend also needs PostgreSQL and Redis.

## Best Fit Use Cases

- latest-info verification
- framework or tool comparison
- official policy lookup
- implementation-path research
- source-backed answer generation
