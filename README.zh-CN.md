# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Source-Aware Research](https://img.shields.io/badge/research-source--aware-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Evidence Feedback](https://img.shields.io/badge/evidence-structured-orange)](./SKILL.md)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## 给 AI 智能体的可信研究技能：不是多搜一点，而是更不容易被假数据带偏

`Net Deep Research` 是一个公开的可信研究 skill bundle，面向那些需要先联网核验信息、识别弱信源或假数据、再按证据给出更可靠结论的 AI 智能体。它不是为了“多搜一点”，而是为了减少被单一网页、伪权威表述或未经核验的数据带偏的概率。

如果你在找这些东西，这个仓库就是对应的公开包：

- 可靠信息核验 skill
- 假数据 / 弱信源鉴别型智能体工具
- 带 citation 意识的 RAG 核验工作流
- 更强调可信输出的 LLM 研究能力
- 带危险网站过滤与异常链接拦截的联网研究工具

最适合：

- 官方政策查询
- 框架 / 工具对比
- 最新信息核验
- 需要引文和证据链的研究问题
- 容易被弱信源带偏的问题
- 需要先过滤危险网站或可疑链接的联网研究场景

安装入口：

- [ClawHub - Net Deep Research](https://clawhub.ai/h4444433333/skills/net-deep-research)（Agent 宿主的 skill bundle；[版本列表](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)）
- PyPI：`pip install net-deep-research`（命令行 + 库；装 MCP 服务加 `[mcp]`）
- [GitHub - h4444433333/net-deep-research](https://github.com/h4444433333/net-deep-research)（源码直跑）
- MCP 客户端：`pip install "net-deep-research[mcp]"` 后注册 `net-deep-research-mcp`（见[路径 D](#路径-dmcp-服务)）

### 为什么别人会试它

- 不是只会搜，而是会先判断哪些信息不该信
- 先看信源，再写答案
- 抓取前先做 URL 风险过滤
- 交叉验证后再下结论
- 已验证事实和推断分得更清楚
- 遇到冲突信息不会硬编成一个结论

### 为什么它和普通联网搜索不一样

很多所谓 research prompt 最后还是两种弱模式：

- 只看前几条结果，然后自信总结
- 看了很多网页，但没有留下可用证据结构

`Net Deep Research` 的目标就是避开这两种问题。它的设计重点不是“搜索更深”，而是尽量识别假数据、过滤弱信源，并把结论和证据边界一起交给用户。

### 一个更直观的例子

比如用户问：`ego lite 是什么？`

不用它时，常见回答会像：

- 先按字面拆词，把它解释成某个叫 Ego 的“轻量版”
- 没先确认它到底是模型、产品、浏览器，还是别的东西
- 整段话看起来顺，但缺少清楚的来源支撑

用它时，更接近：

- 先确认 `ego lite` 在当前语境里到底指向哪个具体产品
- 再用官网、README、GitHub 等来源交叉核验，而不是让单一页面直接决定答案
- 最后把结论、证据和不确定性分开写清楚，让用户看得出哪里是已证实、哪里只是推断

### 你可以直接这样问

- Bun 现在适不适合大规模 Next.js 生产部署？
- 北京今年个人社保缴纳政策的官方口径是什么？
- 哪些 RAG 评测框架更适合做 citation faithfulness？
- 一个政策或技术结论现在到底是“已验证事实”还是“高概率推断”？

## 快速开始

按你的使用方式选一条路径：

| 路径 | 适用人群 |
|---|---|
| **A. Skill 安装** | Agent 宿主（Trae、OpenCode、Claude Code、Codex、Cursor、OpenClaw） |
| **B. pip 安装** | 终端用户与 Python 程序 |
| **C. 源码直跑** | 在本仓库上开发、改造 |
| **D. MCP 服务** | 支持 MCP 的客户端（Claude Desktop、Qoder 等） |

### 1. 安装

#### 路径 A：Skill 安装（Agent 宿主）

首选：

- 从 [ClawHub 技能页](https://clawhub.ai/h4444433333/skills/net-deep-research) 安装
- 或从 [ClawHub 版本页](https://clawhub.ai/h4444433333/skills/net-deep-research#versions) 选择指定版本

如果你使用的是 Trae、OpenCode、Claude Code、Codex、Cursor 或 OpenClaw，建议优先使用下面两种 LLM 辅助安装方式。你只需要把对应 prompt 发给模型，让它帮你完成安装即可。

在线安装提示词：

```text
请直接在线读取 GitHub 仓库 https://github.com/h4444433333/net-deep-research 的当前仓库根目录，并把其中的 SKILL.md、_meta.json、skill-card.md、references/ 和 tools/ 作为一个 skill bundle 安装到你当前宿主支持的 skill 目录中。如果你的宿主不支持直接从 GitHub 安装，请明确告诉我不支持，并给出你支持的安装方式。安装完成后告诉我安装位置，以及是否需要重启或刷新宿主。
```

本地安装提示词：

```text
我已经把这个 skill bundle 下载到本地目录 /absolute/path/to/net-deep-research。请从这个本地目录安装 SKILL.md 及相关文件到你当前宿主支持的 skill 目录中。如果你的宿主不支持从本地目录安装，请明确告诉我不支持，并给出你支持的安装方式。安装完成后告诉我安装位置，以及是否需要重启或刷新宿主。
```

#### 路径 B：pip 安装（命令行 / 库）

```bash
pip install net-deep-research          # Python >= 3.10，零第三方依赖
```

如果包尚未在 pypi.org 正式站生效，可从 TestPyPI 安装同一构建：

```bash
pip install -i https://test.pypi.org/simple/ net-deep-research
```

通过工作目录下的 `.env` 文件或环境变量配置（模板见仓库内 `.env.example`），
唯一必填项是 `LLM_API_KEY`：

```bash
cp .env.example .env                   # 然后填入 LLM_API_KEY
```

两种使用形态：

```bash
# 命令行
net-deep-research "你的问题" [--report]
```

```python
# Python 程序
from net_deep_research import research
result = research("你的问题", report=False)
print(result["answer"])
```

#### 路径 C：源码直跑（本仓库）

```bash
git clone https://github.com/h4444433333/net-deep-research.git
cd net-deep-research
cp .env.example .env                   # 然后填入 LLM_API_KEY
python3 research_cli.py "你的问题" [--report]
```

`research_cli.py` 是转发到 `net_deep_research/cli.py:main` 的薄壳入口，
无需任何打包步骤。

#### 路径 D：MCP 服务

```bash
pip install "net-deep-research[mcp]"
```

在 MCP 客户端（Claude Desktop、Qoder 等）中注册 pip 安装后自带的
console 入口：

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

如果是源码目录直跑而非 pip 安装，把 `command` 换成 `python`、
`args` 指向适配脚本：`["/absolute/path/to/net-deep-research/channels/mcp/server.py"]`。

暴露两个工具：`deep_research(question)`（完整多源研究）与
`check_source(url)`（URL 安全筛查）。详见 `channels/mcp/README.md`。

### 2. 刷新并验证

某些宿主在安装或更新 skill 后，需要重启、刷新技能索引，或重新打开会话。

验证方式：

```text
/net-deep-research 你的问题
```

### 3. 使用

使用下面方式触发技能：

```text
/net-deep-research 你的问题
```

推荐显式使用 `/net-deep-research` 指令触发。

如果不显式写这个指令，只有在下面这类场景里，宿主才适合隐式触发这个 skill：

- 需要深度联网搜索，而不是普通网页查询
- 需要在网上鉴别真伪、排除误导性说法或弱信源
- 需要多来源交叉核验，避免单一页面直接决定结论
- 需要把已验证事实、公开证据和高概率推断拆开

不适合隐式触发的情况：

- 普通联网问答
- 简单最新信息查询
- 单一来源就能回答清楚的问题
- 对速度更敏感、没必要进入深度核验链路的问题

示例：

```text
/net-deep-research Bun 在 2026 年是否适合大规模 Next.js 生产部署？
/net-deep-research 对比最新的 RAG 评测框架，看看谁更适合做 citation faithfulness 评估
/net-deep-research 北京今年个人社保缴纳政策的官方口径是什么？
```

如果安装完成后宿主不会自动按意图触发，或者你就是想强制走这条深度核验链路，就显式写：

```text
/net-deep-research 你的问题
```

## 你能得到什么

### 核心能力

- 🌐 在回答前先检索公开网络信息
- 🧪 调用外部后端辅助做信源信誉判断
- 🛡️ 在抓取前执行 URL 安全检查
- 🚫 过滤危险网站、异常链接和不该被抓取的可疑地址
- 🧱 将调研过程组织成结构化工作流
- 🔍 对关键结论做交叉核验，而不是只信单一页面
- 🧾 把支撑结论的证据尽量列清楚，而不是只给一个像样但不可核验的答案

### 包含的文件

- `SKILL.md` - 主技能说明
- `skill-card.md` - 简版技能介绍卡
- `_meta.json` - 包元信息
- `tools/score_stability.py` - 本地 URL 稳定性评分工具

## 为什么它比普通联网搜索更稳

- ✅ 信源筛选更克制
- ✅ 已验证事实和推断内容分得更清楚
- ✅ 对冲突信息和不确定性的处理更扎实
- ✅ 交叉验证让结论更不容易被单一信源带偏
- ✅ 能直接作为本地或托管智能体环境中的复用技能包

## 为什么它更像“研究工具”而不是“会搜索的提示词”

很多所谓“research prompt”最后还是两种问题：

- 只看前几条结果，然后强行总结
- 看了很多页面，但没有留下可用的证据结构

`Net Deep Research` 的目标就是避开这两种弱模式。它更强调显式选源、交叉核验、矛盾处理，以及对用户可见的证据质量。

## 目录结构

```text
net-deep-research-github-1.1.0/
├── README.md
├── README.zh-CN.md
├── SKILL.md
├── _meta.json
├── references/
├── skill-card.md
└── tools/
    └── score_stability.py
```

## 适用场景

- 最新信息核验
- 框架或工具对比
- 官方政策查询
- 实现路径调研
- 需要信源支撑的回答生成
