# Changelog

All notable changes to `net-deep-research`. Version numbers follow semver;
every entry corresponds to a PyPI / ClawHub release of the same version.

## 1.1.2

- MCP adapter now ships with the wheel: `pip install "net-deep-research[mcp]"`
  provides the `net-deep-research-mcp` console entry point, so MCP clients can
  register `{"command": "net-deep-research-mcp"}` without a source checkout
- Skill release artifacts gain a dist outlet: `release.py skill-bundle` writes
  `dist/skill/net-deep-research-skill-<version>.zip` plus an extracted copy
- README install entries expanded beyond ClawHub: PyPI, MCP, and GitHub paths

## 1.1.1

- Silent degradation when the reputation backend is unreachable: the CLI /
  library now falls back without any user-visible output
  (safety scan → LLM rule-based cross-validation → inline guards;
  feedback → local save; passport/reputation → skipped)
- New `_llm_cross_validate_urls()` fallback screener (6 rules: typosquatting,
  title/domain mismatch, content farms, raw-IP/shortener hosts, known-bad
  categories, single-source claims)
- Backend health probe hardened: 10s timeout × 3 attempts (was 6s × 2),
  reducing false "offline" verdicts caused by transient TLS jitter
- Release tooling: `scripts/bump_version.py` (single-source version sync
  across 5 points), `scripts/release.py` (build/upload/clawhub pipeline),
  MCP adapter (`channels/mcp/server.py`), GitHub Actions CI/CD, unit tests

## 1.1.0

- English-first localization of the skill bundle and docs
- CLI independence: standalone `net_deep_research` package, published to
  PyPI (`pip install net-deep-research`), zero third-party dependencies
- Dual-form entry: console command `net-deep-research` plus programmatic
  `research()` API returning structured results (sources, answer, passport)
- Citation passport emission on external-source runs

## 1.0.9

- First release with backend integration (`https://www.shoggoth.vip`):
  backend-assisted search, URL safety checks via `/v1/sources/check`,
  source reputation scoring, structured research-feedback submission
