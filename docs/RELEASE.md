# 发布手册（Release Runbook）

单一事实源 = 本仓库。所有渠道产物由同一版本号派生，版本号由
`scripts/bump_version.py` 统一同步（5 处：`pyproject.toml`、
`net_deep_research/__init__.py`、`_meta.json`、`SKILL.md`、`skill-card.md`）。

## 标准发版流程（本地手工通道，当前使用）

```bash
# 0. 确保工作区干净、测试通过
pytest tests/ -q

# 1. 更新 CHANGELOG.md（在顶部新增版本小节）

# 2. 升版本号（自动同步 5 处 + 读回校验 + 拒绝 PyPI 已存在版本）
python scripts/bump_version.py 1.1.2

# 3. 提交版本变更（纪律：精确 add，永不 push）
git add CHANGELOG.md pyproject.toml net_deep_research/__init__.py _meta.json SKILL.md skill-card.md
git commit -m "chore: 版本升级 -> 1.1.2"

# 4. 构建 + 质检 + 全新 venv 回装验证
python scripts/release.py build

# 5. 上传 TestPyPI 并回装验证
TESTPYPI_TOKEN=pypi-xxx python scripts/release.py upload --repo testpypi
pip install --index-url https://test.pypi.org/simple/ net-deep-research==1.1.2  # 新 venv 验证

# 6. 上传正式源
PYPI_TOKEN=pypi-xxx python scripts/release.py upload --repo pypi

# 7. 发布 ClawHub
python scripts/release.py clawhub
```

## GitHub Actions 通道（备用，需先 push 仓库）

push 后 Actions 生效：

- `ci.yml`：push/PR → 版本一致性检查 + 单测（3.10/3.12/3.13）+ 构建质检 + 安装冒烟
- `publish.yml`：**打 `v*.*.*` tag 触发** → 测试 → 构建 → TestPyPI → 回装验证 →
  PyPI → GitHub Release（Release notes 自动取自 CHANGELOG 对应小节）

前置：仓库 Settings → Secrets 配置 `TESTPYPI_TOKEN`、`PYPI_TOKEN`。

```bash
git tag v1.1.2 && git push origin v1.1.2   # 仅在你决定启用 CI 通道时
```

## 渠道清单

| 渠道 | 产物 | 发布方式 |
|---|---|---|
| PyPI | wheel + sdist | `release.py upload --repo pypi`（或 tag 触发 Actions） |
| TestPyPI | 同上 | `release.py upload --repo testpypi`（验证通道，会定期清库） |
| ClawHub | SKILL.md + references/ + _meta.json | `release.py clawhub`（`npx clawhub publish`） |
| GitHub | 源码 + Release 附件 | Actions 自动（或手工 `gh release create`） |
| MCP | `channels/mcp/server.py` | 随仓库分发，`pip install net-deep-research[mcp]` |
| Coze / WorkBuddy | 见 `channels/*/README.md` | 待接入 |

## 凭据边界

- token 一律走环境变量（`PYPI_TOKEN` / `TESTPYPI_TOKEN`），**不落盘、不进 .env.notes**
- 本地手工通道执行完即释放环境变量，不写入 shell history 持久配置

## 回滚

- PyPI 版本号不可复用：发错版本只能 `twine` 在网页端 **yank**（隐藏，不删除），
  然后发下一个版本号。所以发版前必须走完 TestPyPI 验证。
- ClawHub：在后台回滚到上一个已发布版本。
