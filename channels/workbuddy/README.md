# WorkBuddy 渠道

状态：**待接入**（需要平台规范与开发者权限后落地）。

## 接入思路

WorkBuddy 作为 Agent 宿主/工作台类产品，接入路径预期为以下之一：

1. **MCP 方式（首选）**：与 Coze 相同，复用 `channels/mcp/server.py`，
   按 WorkBuddy 的 MCP 接入规范配置（stdio 或 HTTP 视平台要求）。
2. **Skill/插件包方式**：若平台支持类 ClawHub 的 skill 目录，
   直接复用仓库根的 `SKILL.md` + `references/` + `_meta.json` 产物。

## 落地前置条件

- [ ] WorkBuddy 开发者文档与接入规范确认（用户提供后台权限后调研）
- [ ] 本目录产物：按平台规范生成清单文件（`manifest.json` 等）

## 版本同步

接入后版本号跟随单一事实源（`scripts/bump_version.py` 统一管理），
本渠道不维护独立版本。
