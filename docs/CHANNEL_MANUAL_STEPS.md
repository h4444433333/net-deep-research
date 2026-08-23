# 渠道手动发布步骤（Coze / WorkBuddy / MCP 目录站）

配套 `docs/RELEASE.md`（自动通道）。本文档覆盖**没有官方 CLI、必须手工操作**
的渠道。所有步骤的前置条件相同：版本号已通过 `scripts/bump_version.py`
同步、`scripts/release.py build` 已通过。

---

## 1. Coze（扣子）手动接入

> 无公开发布 CLI，全程在开发者后台手工操作。

### 准备（本仓库内，可自动化部分）

1. 需要一个公网可达的 HTTPS 端点（MCP HTTP 形态或 OpenAPI 插件端点），
   可部署到现有阿里云 ECS（`47.252.33.143`）。
2. 生成接入产物（接入时在 `channels/coze/` 下补）：
   - `http_server.py`：`channels/mcp/server.py` 的 Streamable HTTP 形态
     （FastMCP 原生支持 `mcp.run(transport="streamable-http")`）
   - `plugin_schema.json`：OpenAPI 3.0 schema（若走插件方式）

### 后台操作（手工）

**方式 A：MCP 网关（推荐）**

1. 登录 Coze 开发者后台（国际版 `coze.com` / 国内版 `coze.cn`，两套独立）
2. 进入目标 Bot/空间 → 插件/能力管理 → 选择 "MCP" 接入方式
3. 填入 MCP Server 的 HTTP 端点 URL（指向我们部署的 `http_server.py`）
4. 平台会自动拉取工具列表（`deep_research`、`check_source`）——确认识别成功
5. 在 Bot 编排中启用这两个工具，保存并发布

**方式 B：自定义插件（OpenAPI）**

1. 后台 → 插件 → 创建插件 → 上传 `plugin_schema.json`
2. 配置鉴权（如无鉴权需确认平台政策）→ 调试每个 API → 发布
3. 在 Bot 中添加该插件

### 验证

- 在后台调试窗口输入测试问题，确认 `deep_research` 返回 JSON 结果
- 检查后端 `www.shoggoth.vip` 日志是否收到对应请求

### 版本更新

Coze 侧指向的是我们的端点/仓库，**无需重新发布**：升级后端或仓库代码即可。
仅当工具签名（输入输出结构）变化时，需回后台重新同步工具列表。

---

## 2. WorkBuddy 手动接入

> 平台接入规范尚未确认，以下为通用流程框架，落地前需先完成"调研"步骤。

### 第一步：调研（前置，手工）

1. 找到 WorkBuddy 开发者文档（入口待用户提供）
2. 确认其能力接入形态属于哪一类：
   - [ ] MCP 客户端 → 走方式 A（同 Coze，复用 `channels/mcp/server.py`）
   - [ ] Skill/插件目录 → 走方式 B（复用 `SKILL.md` + `references/` + `_meta.json`）
   - [ ] 私有 API → 需在 `channels/workbuddy/` 补适配产物
3. 确认是否需要账号审核/企业认证

### 第二步：接入（按调研结论）

**方式 A（MCP）**：
1. 开发者后台 → 添加 MCP Server → 填 stdio 命令或 HTTP 端点
2. 确认工具列表识别成功 → 启用 → 测试

**方式 B（Skill 包）**：
1. 打包：`SKILL.md` + `references/` + `_meta.json` + `skill-card.md`
   （与 ClawHub 产物一致，无需额外生成）
2. 后台上传 → 填写卡片信息（从 `skill-card.md` 复制）→ 提交审核

### 第三步：验证与记录

- 跑一次端到端测试，把平台特有的坑回填到 `channels/workbuddy/README.md`

---

## 3. MCP 目录站推广（半自动化提交）

> MCP 协议没有统一注册表；曝光靠各目录站。这些目录站的收录是**半自动化**的：
> 提交动作（注册、填表、粘配置）手工做一次，之后目录站持续跟随我们的
> GitHub 仓库——代码升级自动生效，无需重复提交。所以这是一次性人工成本，
> 不是每版一次的流程。
>
> **具体入口以提交时各站现行流程为准**（目录站改版频繁）。

### 提交前通用准备

1. 仓库已推 GitHub（目录站几乎都要求公开仓库链接）
2. `channels/mcp/README.md` 中的客户端配置 JSON 就是"安装配置"素材
3. 一句话描述 + 工具清单（`deep_research`：多源深度研究；
   `check_source`：URL 安全筛查）

### 目标站点定位与典型流程

| 目录站 | 定位 | 典型提交方式 |
|---|---|---|
| **Smithery**（smithery.ai） | 目前最活跃的 MCP 目录，支持一键安装命令生成，有评分和评论系统，类似 npmjs.com 的体验 | 网页端用 GitHub 仓库导入，自动生成安装配置；支持远程/本地两种形态 |
| **Glama**（glama.ai/mcp/servers） | 侧重 MCP Server 的质量评估和基准测试，会自动化验证你的 Server 是否真正可用，更像“认证目录” | 自动爬取 GitHub，也可网页手动提交仓库链接 |
| **mcp.so** | 中文社区友好，收录速度快，对国内开发者曝光度更高 | 网页表单提交 / GitHub PR 到其收录仓库 |
| **PulseMCP**（pulsemcp.com） | 偏新闻/聚合属性，适合发版本公告 | 网页提交 |

### 提交顺序建议（按投入产出比）

1. **Smithery**：流量最大，一键安装命令可直接引用到 README，优先提交
2. **mcp.so**：收录快、中文曝光，低成本第二站；注意我们的后端部署在海外区，
   面向国内用户的可用性叙事值得写清楚（后端不通会无感降级）
3. **Glama**：会被自动验证可用性，提交前确保仓库内 `channels/mcp/README.md`
   的安装步骤在干净环境可复现（stdio 形态零外部依赖，预期可通过）
4. **PulseMCP**：每次发布新版本（如 1.1.2）时顺手发一条公告

### 每个站点的提交动作（通用模板）

1. 注册/登录 → Submit / Add Server
2. 填：名称 `net-deep-research`、GitHub 仓库 URL、一句话描述
3. 粘贴安装配置（从 `channels/mcp/README.md` 复制 mcpServers JSON）
4. 选工具类别：research / web / security
5. 提交 → 等待收录（多数 1-3 天）

### 版本更新（半自动化的体现）

目录站一般只存“仓库链接 + 配置”，代码升级**不需要重新提交**；
仅当新增工具或改安装方式时更新条目。Glama 会周期性重新跑可用性验证，
保持仓库内安装说明可复现即可持续保住“已认证”状态。

---

## 共同纪律

- 所有手工步骤完成后，在本文档对应小节打钩并记录日期
- 凭据（各平台账号密码、token）不进仓库、不进 `.env.notes` 明文
- 每次手工发布对应一条 `git commit`（记录"发什么版本、去了哪个渠道"）
