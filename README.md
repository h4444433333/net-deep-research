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
I have already downloaded this skill bundle to /absolute/path/to/net-deep-research-github-1.0.7. Please install SKILL.md and its related files from that local directory into the skill directory supported by your current host. If your host does not support local-directory installation, say that clearly and tell me which installation method it does support. After installation, tell me the install path and whether the host needs a restart or reload.
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
- ✅ cross-checking makes conclusions less likely to be skewed by a single source
- ✅ reusable skill package format for local or hosted agent environments

## Repository Layout

```text
net-deep-research-github-1.0.7/
├── README.md
├── SKILL.md
├── _meta.json
├── references/
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
