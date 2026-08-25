# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Source-Aware Research](https://img.shields.io/badge/research-source--aware-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Evidence Feedback](https://img.shields.io/badge/evidence-structured-orange)](./SKILL.md)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

**English** | [简体中文](./README.zh-CN.md)

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

Quick links:

- [A. Skill install](#quickstart-skill): install into Trae, Claude Code, Cursor, Codex, OpenCode, or OpenClaw
- [B. pip install](#quickstart-pip): install the CLI and Python library
- [C. Run from source](#quickstart-source): check requirements first, then start quickly in a local Python environment
- [D. MCP server](#quickstart-mcp): connect it to Claude Desktop, Qoder, or other MCP clients

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

Pick the path that matches how you want to use it:

| Path | Best for |
|---|---|
| **A. Skill install** | Agent hosts (Trae, OpenCode, Claude Code, Codex, Cursor, OpenClaw) |
| **B. pip install** | Terminal users and Python programs |
| **C. Run from source** | Local development or the fastest Python start from source |
| **D. MCP server** | MCP-capable clients (Claude Desktop, Qoder, ...) |

### 1. Install

<a id="quickstart-skill"></a>

#### Path A: Skill install (agent hosts)

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
I have already downloaded this skill bundle to /absolute/path/to/net-deep-research. Please install SKILL.md and its related files from that local directory into the skill directory supported by your current host. If your host does not support local-directory installation, say that clearly and tell me which installation method it does support. After installation, tell me the install path and whether the host needs a restart or reload.
```

<a id="quickstart-pip"></a>

#### Path B: pip install (CLI / library)

```bash
pip install net-deep-research          # Python >= 3.10, zero third-party dependencies
```

Configure it with a local `.env` file (recommended) or with exported environment
variables. See `.env.example` in this repo. `LLM_API_KEY` is required, and for
non-default OpenAI-compatible providers you should also set `LLM_BASE_URL` and
`LLM_MODEL` explicitly:

```bash
cp .env.example .env                   # keep this file local, then fill in your real values
```

Recommended local `.env`:

```env
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

How config is loaded for `net-deep-research "your question"`:

- keep `.env` on your local machine; do not commit it
- for normal use, put `.env` in the directory where you run the command
- existing shell environment variables win over `.env`
- the CLI auto-loads the first `.env` it finds in this order:
  1. package directory `net_deep_research/.env`
  2. current working directory `.env`

So yes: if you copied `.env.example` to a local `.env` and filled in the
provider values, the command will use it normally.

Two usage forms:

```bash
# Terminal
net-deep-research "your question" [--report]
```

```python
# Python program
from net_deep_research import research
result = research("your question", report=False)
print(result["answer"])
```

`--report` (Python API: `report=True`) additionally writes a full research report
file `report-<session_id>.pdf` to the current directory (falls back to `.md` when
Chrome/Chromium is not available). The report bundles the conclusion, per-claim
grades (A/B/C/U), the claim-evidence chain, typed conflicts, causal candidates,
and the citation passport for backend verification. Without the flag only the
answer text is produced.

<a id="quickstart-source"></a>

#### Path C: Run from source (this repo)

Requirements first:

- Python >= 3.10
- Git
- A valid `LLM_API_KEY` in `.env`
- A local virtual environment is recommended

If you want the fastest Python local start, run:

```bash
git clone https://github.com/h4444433333/net-deep-research.git
cd net-deep-research
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
cp .env.example .env                   # keep it local; fill in LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
python -m net_deep_research.cli "your question" [--report]
```

If you prefer to skip the editable install, you can still run the thin wrapper directly:

```bash
cp .env.example .env                   # keep it local; fill in LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
python3 research_cli.py "your question" [--report]
```

`research_cli.py` is a thin entry point that forwards to
`net_deep_research/cli.py:main`; `pip install -e .` just gives you the standard
package entry point inside your local Python environment.
<a id="quickstart-mcp"></a>

#### Path D: MCP server

```bash
pip install "net-deep-research[mcp]"
```

Register in your MCP client (Claude Desktop, Qoder, ...) using the console
entry point installed by pip:

```json
{
  "mcpServers": {
    "net-deep-research": {
      "command": "net-deep-research-mcp",
      "args": []
    }
  }
}
```

Exposes two tools: `deep_research(question)` (full multi-source research) and
`check_source(url)` (URL safety screening).

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

## Why It Feels Better Than Generic Web Search

- ✅ better source discipline
- ✅ clearer separation between verified facts and inference
- ✅ stronger handling of uncertainty and conflict
- ✅ cross-checking makes conclusions less likely to be skewed by a single source
- ✅ reusable skill package format for local or hosted agent environments

## Repository Layout

This repo serves two audiences at once — agent-host users (skill form) and
Python users (pip form). The files below are grouped by who needs them.

```text
net-deep-research/
├── # ── Skill form (Path A: agent hosts / ClawHub / Smithery) ──
├── SKILL.md                 # skill instructions for agent hosts
├── skill-card.md            # skill summary card
├── _meta.json               # skill package metadata
├── references/              # feedback contract & research playbook
├── tools/                   # skill helper scripts (score_stability.py, md_to_pdf.py)
├──
├── # ── Python form (Path B/C: pip install / run from source) ──
├── pyproject.toml           # package definition & entry points
├── net_deep_research/       # installable package (CLI + library + MCP adapter)
├── research_cli.py          # thin CLI wrapper for running from source
├── example_usage.py         # library usage example
├──
├── # ── Shared ──
├── README.md / README.zh-CN.md
├── CHANGELOG.md
├── LICENSE
└── tests/                   # test suite
```

If you only use one form, you can ignore the other group's files — they do
not interfere with installation.

The hosted reputation backend runs at `https://www.shoggoth.vip`; this
repository ships the client side — the skill, the CLI, and the MCP adapter.

## Best Fit Use Cases

- latest-info verification
- framework or tool comparison
- official policy lookup
- implementation-path research
- source-backed answer generation
