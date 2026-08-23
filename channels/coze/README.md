# Coze 渠道（扣子）

状态：**待接入**（需要开发者后台权限后落地）。

## 接入调研结论

Coze 支持两类接入方式，推荐顺序如下：

1. **MCP 网关方式（推荐）**：Coze 平台已支持将 MCP Server 注册为插件能力。
   复用本仓库 `channels/mcp/server.py`，需部署为 HTTP（Streamable HTTP）
   形态供 Coze 回调。落地时在本目录补 `http_server.py`（FastMCP 原生支持
   `mcp.run(transport="streamable-http")`）。
2. **插件（Plugin）方式**：在 Coze 开发者后台创建插件，定义 OpenAPI
   schema 指向自建网关接口。需要公网可达的 HTTPS 端点。

## 落地前置条件

- [ ] Coze 开发者后台账号（用户提供）
- [ ] 公网可达的 MCP HTTP 端点（可部署到现有阿里云 ECS）
- [ ] 本目录产物：`http_server.py` + `plugin_schema.json`（OpenAPI）

## 版本同步

接入后版本号跟随单一事实源（`scripts/bump_version.py` 统一管理），
本渠道不维护独立版本。
