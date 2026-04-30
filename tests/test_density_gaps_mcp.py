"""MCP wrapper tests for find_density_gaps."""
from __future__ import annotations

import json

import pytest


def test_returns_valid_json_with_gaps_block(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_density_gaps

    out = find_density_gaps(
        pattern_id="account_behavior_pattern", top_n=5,
    )
    parsed = json.loads(out)
    assert "gaps" in parsed
    assert "excluded_dims" in parsed
    assert "n_pairs_tested" in parsed
    assert "n_entities" in parsed


def test_validation_invalid_alpha(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_density_gaps

    with pytest.raises(Exception, match="alpha"):
        find_density_gaps(
            pattern_id="account_behavior_pattern", alpha=2.0,
        )


def test_dim_pairs_passthrough_unknown_name(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_density_gaps

    with pytest.raises(Exception, match="unknown dim names"):
        find_density_gaps(
            pattern_id="account_behavior_pattern",
            dim_pairs=[["nope1", "nope2"]],
        )


def test_returns_finite_q_values(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_density_gaps

    out = find_density_gaps(
        pattern_id="account_behavior_pattern", top_n=5,
    )
    parsed = json.loads(out)
    for gap in parsed["gaps"]:
        assert 0.0 <= gap["q_value"] <= 1.0
