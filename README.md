# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Source-Aware Research](https://img.shields.io/badge/research-source--aware-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Evidence Feedback](https://img.shields.io/badge/evidence-structured-orange)](./SKILL.md)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## Deep Research Skill For AI Agents That Need Evidence, Not Just Browsing.

`Net Deep Research` is a public skill bundle for AI agents that must search the live web, compare multiple sources, filter risky URLs, and answer from a structured evidence trail. It is built for research questions where being grounded matters more than being fast.

If you are looking for an **AI deep research skill**, a **web research agent**, a **citation-aware RAG workflow**, or a **source-verified research bundle for LLMs**, this repository is the public package for that job.

Best fit:

- official policy lookup
- framework and tool comparison
- latest-information verification
- citation-sensitive research
- questions where weak sources can ruin the answer

Install from:

- [ClawHub - Net Deep Research](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub - Versions](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

### Why People Try It

- live multi-source research instead of one-shot guessing
- source-aware selection instead of trusting the first page
- URL safety checks before fetch
- clearer separation between verified facts and inference
- conflict-aware output instead of fake certainty

### Why It Stands Out

Most "research" prompts still fail in one of two ways:

- they summarize the first few pages and sound confident
- they browse more, but leave no usable evidence structure behind

`Net Deep Research` is designed to avoid both. It pushes the agent toward multi-round, multi-angle, conflict-aware, evidence-first research.

### Example Questions

- Is Bun production-ready for large Next.js deployments?
- What is the official Beijing individual social insurance contribution policy this year?
- Which RAG evaluation frameworks are strongest on citation faithfulness?
- What changed in the latest policy draft, and what is still unverified?

## Quick Start

### 1. Install

Preferred:

- Install from [ClawHub skill page](https://clawhub.ai/h4444433333/skills/net-deep-research)
- Or pick a specific build from [ClawHub versions page](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

LLM-assisted install:

- Ask your LLM host to find this GitHub repository and install the skill bundle from `net-deep-research-github-1.0.7/`
- Or download this repository locally and ask your LLM host to install the local bundle from `net-deep-research-github-1.0.7/`

Prompt for online install:

```text
Please read the GitHub repository https://github.com/h4444433333/net-deep-research online, find the directory net-deep-research-github-1.0.7/, and install this skill bundle into the skill directory supported by your current host. After installation, tell me the install path and verify that /net-deep-research can be triggered.
```

Prompt for local install:

```text
I have already downloaded net-deep-research-github-1.0.7/ locally. Please install this local skill bundle into the skill directory supported by your current host. After installation, tell me the install path and verify that /net-deep-research can be triggered.
```

### 2. Use

Trigger the skill with:

```text
/net-deep-research your question here
```

This public package activates only on the explicit `/net-deep-research` command.

Example prompts:

```text
/net-deep-research Is Bun production-ready for large Next.js deployments in 2026?
/net-deep-research Compare the latest RAG evaluation frameworks for citation faithfulness
/net-deep-research What is the official policy for Beijing individual social insurance contributions this year?
```

If installation succeeded but the host does not auto-route by intent, explicitly invoke:

```text
/net-deep-research your question here
```

## What You Get

### Core Capabilities

- 🌐 searches across public web sources before answering
- 🧪 queries an external backend for source reputation support
- 🛡️ performs URL safety checks before fetching
- 🧱 organizes findings into a structured research workflow
- 📝 sends a minimal structured research record after external-source runs
- 🧭 allows explicit high-sensitivity diagnostics or explicit user votes only when separately requested
- 🔄 falls back to base research mode when backend services are down

### Included Files

- `SKILL.md` - main skill instructions
- `skill-card.md` - short marketplace-style description
- `_meta.json` - package metadata
- `tools/score_stability.py` - local URL stability scoring helper

## Why It Feels Better Than Generic Web Search

- ✅ better source discipline
- ✅ clearer separation between verified facts and inference
- ✅ stronger handling of uncertainty and conflict
- ✅ cleaner behavior when backend infrastructure partially degrades
- ✅ reusable skill package format for local or hosted agent environments

## Runtime Model

This package prefers backend-integrated research when the backend is reachable.

When it is not:

- the run stays usable
- research continues in fallback mode
- backend status is kept out of user-facing output

## Included Helper Tool

`tools/score_stability.py` is a lightweight Python utility that scores a URL's structural stability using only the Python standard library.

Example:

```bash
python3 tools/score_stability.py https://github.com/example/repo
python3 tools/score_stability.py --json https://docs.python.org/3/
```

## Repository Layout

```text
net-deep-research-github-1.0.7/
├── README.md
├── SKILL.md
├── _meta.json
├── skill-card.md
└── tools/
    └── score_stability.py
```

## Best Fit Use Cases

- latest-info verification
- framework or tool comparison
- official policy lookup
- implementation-path research
- source-backed answer generation
