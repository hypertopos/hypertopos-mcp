"""MCP wrapper tests for find_motif_by_hops."""
from __future__ import annotations

import json

import pytest


def test_returns_valid_json_with_motifs_block(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    # Berka tx_pattern has no edge_table — should fail gracefully.
    # We exercise the MCP serializer + validation paths against a real
    # navigator without paying full enumeration cost.
    out = find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[{"amount_min": 1.0}],
        max_results=3,
    )
    parsed = json.loads(out)
    assert "motifs" in parsed
    assert "n_results" in parsed
    assert parsed["pattern_id"] == "tx_pattern"


def test_validation_anchor_pattern(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="event pattern"):
        find_motif_by_hops(
            pattern_id="account_behavior_pattern",
            hops=[{"amount_min": 1.0}],
        )


def test_validation_empty_hops(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="hops"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[],
        )


def test_validation_too_many_hops(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="hop count"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[{}] * 7,
        )
