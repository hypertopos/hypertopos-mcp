# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for find_diverse_explanations.

Validates: passthrough wiring to navigator, strict-JSON sanitisation
(NaN/inf → null in serialised body), error envelope on ValueError, tier
registration (must appear in _TOOL_TIERS as "base" so it surfaces only
after sphere_overview).
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import find_diverse_explanations


@pytest.fixture
def fake_state():
    """Stub a navigator into _state.navigator with a controllable
    find_diverse_explanations return value.
    """
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    _state["sphere"] = MagicMock()
    yield nav
    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def test_passthrough_wires_args_and_returns_json(fake_state):
    fake_state.find_diverse_explanations.return_value = {
        "primary_key": "E1",
        "pattern_id": "account_pattern",
        "delta_norm": 4.2,
        "theta_norm": 2.5,
        "n_hypotheses_requested": 3,
        "n_hypotheses_returned": 2,
        "hypotheses": [
            {
                "hypothesis_id": 0,
                "dim_labels": ["risk_score"],
                "joint_contribution_pct": 0.55,
                "narrative": "risk_score dominates",
            },
            {
                "hypothesis_id": 1,
                "dim_labels": ["volume"],
                "joint_contribution_pct": 0.15,
                "narrative": "volume secondary",
            },
        ],
        "diversity_score": 1.0,
        "degraded_reason": None,
    }
    body = find_diverse_explanations(
        "E1",
        pattern_id="account_pattern",
        n_hypotheses=3,
        min_contribution_pct=0.10,
        validate=False,
    )
    fake_state.find_diverse_explanations.assert_called_once_with(
        "E1",
        pattern_id="account_pattern",
        n_hypotheses=3,
        min_contribution_pct=0.10,
        validate=False,
    )
    parsed = json.loads(body)
    assert parsed["primary_key"] == "E1"
    assert parsed["pattern_id"] == "account_pattern"
    assert parsed["n_hypotheses_returned"] == 2
    assert len(parsed["hypotheses"]) == 2
    assert parsed["diversity_score"] == 1.0


def test_sanitises_non_finite_diversity_score_to_null(fake_state):
    """Engineered NaN must serialise as JSON null, not "NaN" literal."""
    fake_state.find_diverse_explanations.return_value = {
        "primary_key": "E2",
        "pattern_id": "account_pattern",
        "delta_norm": 3.0,
        "theta_norm": 2.5,
        "n_hypotheses_requested": 3,
        "n_hypotheses_returned": 1,
        "hypotheses": [
            {
                "hypothesis_id": 0,
                "dim_labels": ["risk_score"],
                "joint_contribution_pct": 0.92,
                "narrative": "single-dim driven",
            },
        ],
        "diversity_score": math.nan,
        "degraded_reason": "insufficient_diverse_mass",
    }
    body = find_diverse_explanations(
        "E2",
        pattern_id="account_pattern",
    )
    # Strict JSON parse must succeed (no Infinity/NaN literals)
    parsed = json.loads(body)
    assert parsed["diversity_score"] is None
    assert parsed["degraded_reason"] == "insufficient_diverse_mass"
    # Defence in depth: raw body has no Infinity/NaN tokens
    assert "NaN" not in body
    assert "Infinity" not in body


def test_tool_is_registered_in_base_tier():
    """Tier mapping check — must appear as 'base' so it surfaces only
    after sphere_overview (matches existing analysis tools).
    """
    assert _TOOL_TIERS.get("find_diverse_explanations") == "base"


def test_returns_json_error_on_value_error(fake_state):
    """Navigator-raised ValueError must surface as JSON error envelope,
    not an unhandled Python exception bubbling to the MCP framework.
    """
    fake_state.find_diverse_explanations.side_effect = ValueError("bad input")
    body = find_diverse_explanations(
        "missing_entity",
        pattern_id="account_pattern",
    )
    result = json.loads(body)
    assert result["error"] == "bad input"
    assert result["primary_key"] == "missing_entity"
