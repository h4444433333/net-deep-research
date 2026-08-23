"""Minimal net-deep-research example (programmatic interface)

Install:  pip install net-deep-research
Config:   place a .env in the current directory (see the bundled .env.example);
          at minimum LLM_API_KEY is required
Run:      python example_usage.py

The command-line form is:  net-deep-research "your question" [--report]
"""
from net_deep_research import research, __version__

print(f"net-deep-research version: {__version__}\n")

# One full research run: multi-round search + URL safety scan
# + backend reputation + LLM synthesis.
# report=True additionally writes a Markdown research report
# into the current directory.
result = research("Is Bun production-ready for large Next.js deployments in 2026?", report=False)

# result structure: {session_id, normalization, sources, answer, feedback, passport, report_path}
print("\n" + "=" * 60)
print("Conclusion (first 600 chars)")
print("=" * 60)
print(result["answer"][:600])

print("\n" + "=" * 60)
print(f"Adopted sources: {len(result['sources'])}")
print("=" * 60)
for src in result["sources"][:5]:
    rep = src.get("reputation")
    rep_txt = f" | reputation {rep}" if isinstance(rep, (int, float)) else ""
    print(f"- {src.get('title') or '(untitled)'}{rep_txt}\n  {src['url']}")

passport = result.get("passport") or {}
print(f"\nCitation passport: {passport.get('passport_uuid', '(not issued by backend)')}")
