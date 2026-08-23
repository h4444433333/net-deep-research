"""负信号管道守卫：反例轮强制 + feedback 提示词负证据硬规则。

覆盖三件事：
1. 反例轮默认开启，_plan_adversarial_round 正确解析/截断/容错；
2. feedback 提示词里语义字段不再是 optional，负证据规则（discard 边 / oppose 边 /
   typed_conflicts 必填）以硬规则形式存在。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = ROOT / "net_deep_research"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from net_deep_research import cli


def test_adversarial_round_enabled_by_default():
    assert cli.ADVERSARIAL_ROUND is True


def test_plan_adversarial_round_parses_and_caps(monkeypatch):
    monkeypatch.setattr(cli, "_llm_json", lambda messages: {
        "counter_queries": ["refutation query 1", "  ", "refutation query 2", "extra"],
    })
    sources = {"https://a.example/x": {"url": "https://a.example/x", "title": "A"}}
    result = cli._plan_adversarial_round("some question", sources)
    assert result == ["refutation query 1", "refutation query 2"]


def test_plan_adversarial_round_tolerates_llm_failure(monkeypatch):
    def _boom(messages):
        raise RuntimeError("llm down")

    monkeypatch.setattr(cli, "_llm_json", _boom)
    sources = {"https://a.example/x": {"url": "https://a.example/x", "title": "A"}}
    assert cli._plan_adversarial_round("some question", sources) == []


def test_feedback_prompt_requires_negative_evidence_rules():
    prompt = cli._feedback_user_prompt("q", "evidence")
    # 语义字段不再 optional
    assert "REQUIRED whenever sources disagree" in prompt
    assert '"typed_conflicts": [ ... optional ... ]' not in prompt
    # 未采纳来源必须给出 discard_reason
    assert "[negative-evidence rule]" in prompt
    assert "discard_reason" in prompt
    # 冲突声明必须产出 oppose 边
    assert "[oppose rule]" in prompt
    assert "stance=oppose" in prompt
    # typed_conflicts 结构化必填
    assert "[typed_conflicts rule]" in prompt
    assert "conflicting_values" in prompt
