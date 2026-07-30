# Net Deep Research

[![Skill Bundle](https://img.shields.io/badge/skill-backend--integrated-blue)](./SKILL.md)
[![Public Web Research](https://img.shields.io/badge/research-multi--source-0a7ea4)](./SKILL.md)
[![URL Safety Checks](<https://img.shields.io/badge/safety-url%20checks-22863a>)](./SKILL.md)
[![Feedback Loop](https://img.shields.io/badge/feedback-structured-orange)](./SKILL.md)
[![Python Stdlib](<https://img.shields.io/badge/tooling-python%20stdlib-3776ab>)](./tools/score_stability.py)
[![ClawHub](<https://img.shields.io/badge/ClawHub-Net%20Deep%20Research-7c3aed>)](https://clawhub.ai/h4444433333/skills/net-deep-research)
[![Versions](https://img.shields.io/badge/ClawHub-Versions-111827)](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## 给 AI 智能体的深度调研技能：更强信源、更强引用、更强判断力

`Net Deep Research` 是一个后端辅助的深度调研 skill，面向需要联网检索、谨慎选源、先组织证据再输出答案的 AI 智能体。它把实时网页调研、信源信誉判断、URL 安全检查和结构化证据反馈放进同一条链路里，让回答不只是更快，而是更稳、更可审计、更容易让人信服。

如果你在找这些东西，这个仓库就是对应的公开 bundle：

- AI 深度调研 skill
- 智能体联网研究工具
- RAG 引用增强助手
- 有信源判断能力的研究工作流
- 面向 LLM 的结构化证据检索能力

技能页面：

- [ClawHub - Net Deep Research](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub - 版本列表](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

### 为什么值得用

- 🔎 **实时多源调研**，不是一次性拍脑袋回答
- 🛡️ **URL 安全检查**，抓取前先做风险过滤
- 📚 **后端辅助信源信誉判断**，让选源更稳
- 🧭 **结构化回答流程**，强调证据优先
- 🔁 **交叉验证**，每次回答后都能交叉验证
- ⚡ 展示支撑证据，让回答可溯源，会提供支撑证据与原因让用户决定是否采纳回答

## 它到底解决什么问题

`Net Deep Research` 主要帮智能体做这几件事：

- 回答前先做实时联网检索
- 不迷信单一网页，而是多源对照
- 抓取前先做 URL 风险过滤
- 在最终答案背后保留结构化证据图
- 记录最小化公开研究痕迹，用于信誉与质量分析

## 最适合的搜索意图

这个仓库尤其适合这些需求：

- AI 深度调研
- 智能体联网搜索
- 带引用的 RAG 检索
- 政策 / 官方口径查询
- 框架 / 工具对比调研
- 有后端辅助的研究型 skill

## 30 秒安装

从 ClawHub 安装：

- [ClawHub 技能页](https://clawhub.ai/h4444433333/skills/net-deep-research)
- [ClawHub 版本页](https://clawhub.ai/h4444433333/skills/net-deep-research#versions)

## 快速开始

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
net-deep-research-github-1.0.4/
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
