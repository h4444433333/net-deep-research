# MCP 渠道（Model Context Protocol）

适配层：`server.py`，薄包装核心库，零代码复制。

## 工具清单

| 工具 | 说明 |
|---|---|
| `deep_research(question, report=False)` | 完整深度研究，返回 JSON（answer/sources/passport/...） |
| `check_source(url)` | 单 URL 安全筛查，后端不通时静默走本地内联守卫 |

## 安装与运行

```bash
pip install "net-deep-research[mcp]"
python channels/mcp/server.py          # stdio 传输
```

## 客户端接入示例

```json
{
  "mcpServers": {
    "net-deep-research": {
      "command": "python",
      "args": ["/path/to/channels/mcp/server.py"]
    }
  }
}
```

## 配置

遵循核心包规则：当前目录 `.env`（参考包内 `.env.example`），至少需要
`LLM_API_KEY`；后端默认 `https://www.shoggoth.vip`，不可达时全链无感降级。
