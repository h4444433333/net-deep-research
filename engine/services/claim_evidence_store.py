"""
Claim-level evidence edge persistence.
"""

from __future__ import annotations

import json


def persist_claim_evidence_edges(cur, session_id: str, edges: list[dict]) -> int:
    cur.execute("DELETE FROM claim_evidence_edge WHERE session_id = %s", (session_id,))
    if not edges:
        return 0

    for edge in edges:
        cur.execute(
            """
            INSERT INTO claim_evidence_edge (
                session_id,
                claim_id,
                claim_text,
                source_id,
                llm_source_id,
                article_id,
                source_domain,
                stance,
                evidence_snippet,
                support_score,
                source_tier,
                trace_depth,
                used_in_final,
                exact_match_signal,
                exact_match_score,
                slot_coverage_score,
                slot_hits,
                snippet_span_type,
                verifiable_carrier_signal,
                independent_consensus_signal,
                edge_confidence
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                session_id,
                edge["claim_id"],
                edge["claim_text"],
                edge.get("source_pk"),
                edge.get("source_id"),
                edge.get("article_id"),
                edge.get("source_domain"),
                edge["stance"],
                edge.get("evidence_snippet"),
                edge["support_score"],
                edge["source_tier"],
                edge["trace_depth"],
                edge["used_in_final"],
                bool(edge.get("exact_match_signal", False)),
                float(edge.get("exact_match_score", 0.0) or 0.0),
                float(edge.get("slot_coverage_score", 0.0) or 0.0),
                json.dumps(edge.get("slot_hits") or {}, ensure_ascii=False),
                edge.get("snippet_span_type"),
                bool(edge.get("verifiable_carrier_signal", False)),
                bool(edge.get("independent_consensus_signal", False)),
                edge.get("edge_confidence", 0.0),
            ),
        )
    return len(edges)
