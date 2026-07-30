# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Public Web Research](https://img.shields.io/badge/research-multi--source-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Feedback Loop](https://img.shields.io/badge/feedback-structured-orange)](./SKILL.md)
[![Python Stdlib](<https://img.shields.io/badge/tooling-python%20stdlib-3776ab>)](./tools/score_stability.py)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## Deep Research For AI Agents That Need Better Sources, Better Citations, And Better Judgment.

`Net Deep Research` is a backend-assisted deep research skill for AI agents. It combines live web research, source reputation checks, URL safety checks, and structured evidence feedback so answers are not just fast, but grounded, auditable, and easier to trust.

If you are searching for an **AI deep research skill**, **web research agent skill**, **RAG citation helper**, or a **source-aware research workflow for LLMs**, this repository is the public bundle for that use case.

Live skill page:

- [ClawHub - Net Deep Research](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub - Versions](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

### Why This Is Worth Using

- 🔎 **Live multi-source research** instead of one-shot guessing
- 🛡️ **URL safety checks before fetch** so risky links are filtered early
- 📚 **Source reputation support** to reduce weak-source answers
- 🧭 **Evidence-first answer structure** that separates findings from inference
- 🔁 **Cross-source verification** for conflict handling and uncertainty control
- ⚡ **Traceable support** so users can see why an answer should or should not be trusted

## What It Actually Does

`Net Deep Research` helps agents:

- search the live web before answering
- compare multiple sources instead of trusting a single page
- apply safer URL checks before fetch
- keep a structured evidence map behind the final answer
- record a minimal public research trace for reputation and quality analysis

## Best Fit Searches

This repository is especially relevant if you are looking for:

- deep research for AI agents
- AI web research skill
- source-verified LLM research workflow
- citation-aware RAG assistant
- public policy research skill
- framework comparison assistant
- backend-assisted research pipeline

## 30-Second Install

### 1. Install from ClawHub First

Open:

- [ClawHub skill page](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub versions page](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

If your host already supports ClawHub-native install, use that path first.

### 2. Ask Your Host To Install It For The Current Platform

Paste this into your host:

```text
Please install Net Deep Research for my current platform.

Preferred source:
https://clawhub.ai/h4444433333/skills/net-deep-research

Requirements:
1. Detect the correct local skill directory for this host automatically.
2. Keep the bundle structure unchanged.
3. Preserve SKILL.md, _meta.json, skill-card.md, and tools/ together.
4. Tell me the exact trigger command after installation.
```

Or use the versions page if your host needs explicit version selection:

```text
Install Net Deep Research from this ClawHub versions page and pick the latest stable version that fits my host:
https://clawhub.ai/h4444433333/skills/net-deep-research#versions
```

### 3. If The First Two Paths Fail, Ask Your Host To Install The Downloaded Skill Bundle

If your host does not support ClawHub well, first download the skill bundle locally, then paste this into your host:

```text
I have already downloaded the Net Deep Research skill bundle locally.
Please install this local SKILL.md bundle into the correct skill directory for my current platform.

Requirements:
1. Detect the correct skill directory for this host automatically.
2. Keep SKILL.md, _meta.json, skill-card.md, and tools/ together.
3. Do not flatten the folder structure.
4. Tell me the exact trigger command after installation.
```

### Notes

- Do not move `SKILL.md` out of the bundle root.
- Keep the `tools/` subfolder next to `SKILL.md`.
- Prefer ClawHub for install and version discovery.
- If the first two paths fail, ask your host to install the skill bundle you already downloaded locally.

## Quick Start

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

## Why It Stands Out

Most "research" prompts still collapse into one of two weak patterns:

- summarize the first few pages and sound confident
- browse a lot, but leave no usable evidence structure behind

`Net Deep Research` is built to avoid both failure modes. It pushes the agent toward explicit source selection, cross-checking, contradiction handling, and user-visible evidence quality.

## User Notice

> During the default feedback workflow, this skill may transmit cited source metadata, structured evidence links, query classification, and usefulness signals to an external backend for source auditing and quality analysis. Raw query text, full answer text, offnet answer audits, and trust/untrust votes are sent only when the user explicitly requests a high-sensitivity diagnostic or explicit vote action.

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
net-deep-research-github-1.0.4/
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

## Open-Source Publishing Note

This folder is prepared as a standalone public bundle. If you publish it to GitHub, only the contents inside this folder need to be included.

## GitHub x ClawHub

- GitHub is the bundle source and public code surface.
- ClawHub is the distribution and version discovery surface.
- If you land on GitHub first, use the ClawHub page above for version selection.
- If you land on ClawHub first, use this repository when your host wants a GitHub-style bundle source.
