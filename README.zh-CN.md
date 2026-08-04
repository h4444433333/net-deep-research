# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Source-Aware Research](https://img.shields.io/badge/research-source--aware-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Evidence Feedback](https://img.shields.io/badge/evidence-structured-orange)](./SKILL.md)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## 给 AI 智能体的深度调研技能：不只会搜，而是会拿证据说话

`Net Deep Research` 是一个公开的深度调研 skill bundle，面向那些必须先联网检索、比较多源、过滤风险链接、再按证据组织答案的 AI 智能体。它不是普通“会搜索的提示词”，而是一条更强调信源、交叉核验和可解释性的研究链路。

如果你在找这些东西，这个仓库就是对应的公开包：

- AI 深度调研 skill
- 智能体联网研究工具
- 带 citation 意识的 RAG 研究工作流
- 有信源判断能力的 LLM 调研能力

最适合：

- 官方政策查询
- 框架 / 工具对比
- 最新信息核验
- 需要引文和证据链的研究问题
- 容易被弱信源带偏的问题

安装入口：

- [ClawHub - Net Deep Research](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub - 版本列表](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

### 为什么别人会试它

- 实时多源调研，不是一轮搜索后强行总结
- 先看信源，再写答案
- 抓取前先做 URL 风险过滤
- 已验证事实和推断分得更清楚
- 遇到冲突信息不会硬编成一个结论

### 为什么它和普通联网搜索不一样

很多所谓 research prompt 最后还是两种弱模式：

- 只看前几条结果，然后自信总结
- 看了很多网页，但没有留下可用证据结构

`Net Deep Research` 的目标就是避开这两种问题。它更强调多轮调研、多角度拆解、交叉核验和证据优先。

### 示例问题

- Bun 现在适不适合大规模 Next.js 生产部署？
- 北京今年个人社保缴纳政策的官方口径是什么？
- 哪些 RAG 评测框架更适合做 citation faithfulness？
- 一个政策或技术结论现在到底是“已验证事实”还是“高概率推断”？

## 快速开始

### 1. 安装

首选：

- 从 [ClawHub 技能页](https://clawhub.ai/h4444433333/skills/net-deep-research) 安装
- 或从 [ClawHub 版本页](https://clawhub.ai/h4444433333/skills/net-deep-research#versions) 选择指定版本

LLM 安装：

- 让你的 LLM 宿主直接在线读取这个 GitHub 仓库，并从 `net-deep-research-github-1.0.7/` 安装 skill bundle
- 或先把本仓库下载到本地，再让你的 LLM 宿主从 `net-deep-research-github-1.0.7/` 安装本地 bundle

在线安装提示词：

```text
请直接在线读取 GitHub 仓库 https://github.com/h4444433333/net-deep-research ，找到目录 net-deep-research-github-1.0.7/ ，并把这个 skill bundle 安装到你当前宿主支持的 skill 目录中。安装完成后告诉我安装位置，并验证 /net-deep-research 是否可触发。
```

本地安装提示词：

```text
我已经把 net-deep-research-github-1.0.7/ 下载到本地。请从这个本地目录安装 skill bundle 到你当前宿主支持的 skill 目录中。安装完成后告诉我安装位置，并验证 /net-deep-research 是否可触发。
```

### 2. 使用

使用下面方式触发技能：

```text
/net-deep-research 你的问题
```

这个公开包只在显式 `/net-deep-research` 指令下激活。

示例：

```text
/net-deep-research Bun 在 2026 年是否适合大规模 Next.js 生产部署？
/net-deep-research 对比最新的 RAG 评测框架，看看谁更适合做 citation faithfulness 评估
/net-deep-research 北京今年个人社保缴纳政策的官方口径是什么？
```

如果安装完成后宿主不会自动按意图触发，就显式写：

```text
/net-deep-research 你的问题
```

## 你能得到什么

### 核心能力

- 🌐 在回答前先检索公开网络信息
- 🧪 调用外部后端辅助做信源信誉判断
- 🛡️ 在抓取前执行 URL 安全检查
- 🧱 将调研过程组织成结构化工作流
- 📝 在实际使用外部信源后发送最小化结构化研究记录
- 🧭 仅在用户单独要求时执行显式高敏诊断或显式投票
- 🔄 当后端不可用时自动回退到基础研究模式

### 包含的文件

- `SKILL.md` - 主技能说明
- `skill-card.md` - 简版技能介绍卡
- `_meta.json` - 包元信息
- `tools/score_stability.py` - 本地 URL 稳定性评分工具

## 为什么它比普通联网搜索更稳

- ✅ 信源筛选更克制
- ✅ 已验证事实和推断内容分得更清楚
- ✅ 对冲突信息和不确定性的处理更扎实
- ✅ 后端部分降级时，整体行为仍然稳定
- ✅ 能直接作为本地或托管智能体环境中的复用技能包

## 为什么它更像“研究工具”而不是“会搜索的提示词”

很多所谓“research prompt”最后还是两种问题：

- 只看前几条结果，然后强行总结
- 看了很多页面，但没有留下可用的证据结构

`Net Deep Research` 的目标就是避开这两种弱模式。它更强调显式选源、交叉核验、矛盾处理，以及对用户可见的证据质量。

## 运行模式

当后端可用时，本包优先走后端集成研究模式。

当后端不可用时：

- 整个运行流程仍然可用
- 调研会继续在 fallback 模式下完成
- 不会在用户可见输出中暴露后端状态

## 附带工具

`tools/score_stability.py` 是一个轻量级 Python 工具，只依赖 Python 标准库，用于给 URL 的结构稳定性打分。

示例：

```bash
python3 tools/score_stability.py https://github.com/example/repo
python3 tools/score_stability.py --json https://docs.python.org/3/
```

## 目录结构

```text
net-deep-research-github-1.0.7/
├── README.md
├── README.zh-CN.md
├── SKILL.md
├── _meta.json
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
