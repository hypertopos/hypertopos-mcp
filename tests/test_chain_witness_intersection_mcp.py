# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for chain_witness_intersection.

Validates: passthrough wiring to navigator, strict-JSON sanitisation
(NaN/inf → null in serialised body), tier registration (must appear in
_TOOL_TIERS as "base" so it surfaces only after sphere_overview).
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import chain_witness_intersection


@pytest.fixture
def fake_state():
    """Stub a navigator into _state.navigator with a controllable
    chain_witness_intersection return value.
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
    fake_state.chain_witness_intersection.return_value = {
        "chain_id": "CH-1",
        "chain_pattern": "chain_pattern",
        "member_pattern": "account_pattern",
        "n_members": 4,
        "n_members_explained": 4,
        "n_members_skipped": 0,
        "intersected_witness_dims": ["risk_score"],
        "union_witness_dims": ["diversity", "risk_score"],
        "mean_pairwise_witness_jaccard": 0.75,
        "coordinated": True,
        "interpretation": "4 of 4 members coordinated on [risk_score]",
        "per_member_top_dims": [
            {"primary_key": "A1", "top_dims": ["risk_score"]},
            {"primary_key": "A2", "top_dims": ["risk_score"]},
        ],
    }
    body = chain_witness_intersection(
        "CH-1",
        chain_pattern="chain_pattern",
        member_pattern="account_pattern",
        min_jaccard=0.6,
        top_k_witness=3,
    )
    fake_state.chain_witness_intersection.assert_called_once_with(
        "CH-1",
        chain_pattern="chain_pattern",
        member_pattern="account_pattern",
        min_jaccard=0.6,
        top_k_witness=3,
    )
    parsed = json.loads(body)
    assert parsed["chain_id"] == "CH-1"
    assert parsed["coordinated"] is True
    assert parsed["mean_pairwise_witness_jaccard"] == 0.75


def test_sanitises_non_finite_jaccard_to_null(fake_state):
    """Engineered NaN must serialise as JSON null, not "NaN" literal."""
    fake_state.chain_witness_intersection.return_value = {
        "chain_id": "CH-EMPTY",
        "chain_pattern": "chain_pattern",
        "member_pattern": "account_pattern",
        "n_members": 2,
        "n_members_explained": 2,
        "n_members_skipped": 0,
        "intersected_witness_dims": [],
        "union_witness_dims": [],
        "mean_pairwise_witness_jaccard": math.nan,
        "coordinated": False,
        "interpretation": "all empty",
        "per_member_top_dims": [],
    }
    body = chain_witness_intersection(
        "CH-EMPTY",
        chain_pattern="chain_pattern",
        member_pattern="account_pattern",
    )
    # Strict JSON parse must succeed (no Infinity/NaN literals)
    parsed = json.loads(body)
    assert parsed["mean_pairwise_witness_jaccard"] is None
    # Defence in depth: raw body has no Infinity/NaN tokens
    assert "NaN" not in body
    assert "Infinity" not in body


def test_tool_is_registered_in_base_tier():
    """Tier mapping check — must appear as 'base' so it surfaces only
    after sphere_overview (matches existing chain tools).
    """
    assert _TOOL_TIERS.get("chain_witness_intersection") == "base"


def test_returns_json_error_on_value_error(fake_state):
    """Navigator-raised ValueError must surface as JSON error envelope,
    not an unhandled Python exception bubbling to the MCP framework.
    """
    fake_state.chain_witness_intersection.side_effect = ValueError(
        "test_error_message_for_chain_witness"
    )
    body = chain_witness_intersection(
        "test_chain",
        chain_pattern="x",
        member_pattern="y",
    )
    result = json.loads(body)
    assert "error" in result
    assert result["error"] == "test_error_message_for_chain_witness"
    assert result["chain_id"] == "test_chain"
