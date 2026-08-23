"""net-deep-research 最简示例（编程接口形态）

安装：  pip install net-deep-research
配置：  当前目录放一个 .env（参考包内 .env.example），至少需要 LLM_API_KEY
运行：  python example_usage.py

命令行形态则是：  net-deep-research "你的问题" [--report]
"""
from net_deep_research import research, __version__

print(f"net-deep-research version: {__version__}\n")

# 一次完整研究：多轮搜索 + 安全扫描 + 后端信誉 + LLM 综合
# report=True 会额外生成 Markdown 研究报告（写入当前目录）
result = research("Bun 在 2026 年是否适合生产环境部署大型 Next.js 项目？", report=False)

# result 结构：{session_id, normalization, sources, answer, feedback, passport, report_path}
print("\n" + "=" * 60)
print("研究结论（节选前 600 字）")
print("=" * 60)
print(result["answer"][:600])

print("\n" + "=" * 60)
print(f"采用信源 {len(result['sources'])} 个：")
print("=" * 60)
for src in result["sources"][:5]:
    rep = src.get("reputation")
    rep_txt = f" | 信誉分 {rep}" if isinstance(rep, (int, float)) else ""
    print(f"- {src.get('title') or '(untitled)'}{rep_txt}\n  {src['url']}")

passport = result.get("passport") or {}
print(f"\n引用护照: {passport.get('passport_uuid', '（后端未签发）')}")
