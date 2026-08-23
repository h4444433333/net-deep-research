# AGENTS.md — 发布纪律与渠道台账

本文件约束任何 Agent 在本仓库的发布行为。

## 硬性纪律：上传前必须询问

**每次向任何渠道上传任何版本之前，必须先向用户明确询问并获得同意**，
说明将上传的渠道、版本号与更新说明要点。未经用户同意，禁止执行：

- `twine upload`（PyPI / TestPyPI）
- `npx clawhub publish`（ClawHub）
- 虾评 `POST /api/skills` / `POST /api/upload`
- `git push`（含 tag）
- 任何其他平台投递动作

本地 commit、构建（`build` / `skill-bundle`）、检查类操作不受此限制。

## 渠道版本台账（各渠道独立，不强制统一）

各平台有自己的版本号规则（如 ClawHub 按自己的序列递增、虾评可显式指定），
**不要求各渠道版本号一致**。以下为当前状态，每次发布后更新本表：

| 渠道 | 当前线上版本 | 说明 |
|---|---|---|
| PyPI | 1.1.5 | 版本号不可复用；`https://pypi.org/project/net-deep-research/` |
| GitHub | main 对齐 1.1.5；最新 Release tag v1.1.4 | tag + Release 手动创建 |
| ClawHub | 平台序列（下一版为 1.0.14） | 用户手动上传；版本号由平台序列决定 |
| 虾评 XiaPing | 1.1.5 | skill_id `3f8e9263-…`；上传时显式传 `version` |
| MCP | 随 PyPI 包 | 无独立版本号（stdio 形态，`net-deep-research-mcp`） |
| Smithery（smithery.ai） | 待提交表单 | MCP 目录站；HTTPS 端点已就绪：`https://www.shoggoth.vip/mcp` |

包内五处版本点（pyproject / `__init__` / `_meta.json` / SKILL.md / skill-card.md）
仍保持一致，代表"包版本"；渠道版本是各平台自己的事。

## 已知待办

- 下一个包版本发布时，内容与当前本地源码对齐（各渠道同步更新）。
- 虾评安全标记 `warning_checked` 为平台对"非白名单域名 + 数据发送"的硬规则，
  非本技能缺陷；不为其专门修改内容，靠评测转正淡化。

## 发布工具

发布脚本**不在本公开仓库内**（避免向用户暴露发布工具链），
位于私有目录 `../release-scripts/`（`bump_version.py` + `release.py`），
从仓库根目录以 `.release_tooling/bin/python ../release-scripts/release.py <子命令>` 调用。
