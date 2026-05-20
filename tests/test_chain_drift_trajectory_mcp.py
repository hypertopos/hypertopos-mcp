# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for chain_drift_trajectory.

Validates: passthrough wiring to navigator, strict-JSON sanitisation
(NaN/inf -> null in serialised body), tier registration (must appear in
_TOOL_TIERS as "base" so it surfaces only after sphere_overview).
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import chain_drift_trajectory


@pytest.fixture
def fake_state():
    """Stub a navigator into _state.navigator with a controllable
    chain_drift_trajectory return value.
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
    fake_state.chain_drift_trajectory.return_value = {
        "chain_id": "CH-1",
        "chain_pattern": "chain_pattern",
        "member_pattern": "account_pattern",
        "n_members": 3,
        "n_members_with_history": 3,
        "n_members_skipped": 0,
        "n_members_short_history": 0,
        "n_windows": 4,
        "per_position_trajectory": [
            {
                "position": 0,
                "member_key": "A1",
                "delta_norms_over_time": [0.5, 1.5, 2.5, 3.5],
                "slope": 1.0,
                "regime": "deteriorating",
            },
        ],
        "chain_level_regime": "deteriorating",
        "chain_drift_score": 1.0,
    }
    body = chain_drift_trajectory(
        "CH-1",
        chain_pattern="chain_pattern",
        member_pattern="account_pattern",
        n_windows=4,
    )
    fake_state.chain_drift_trajectory.assert_called_once_with(
        "CH-1",
        chain_pattern="chain_pattern",
        member_pattern="account_pattern",
        n_windows=4,
    )
    parsed = json.loads(body)
    assert parsed["chain_id"] == "CH-1"
    assert parsed["chain_level_regime"] == "deteriorating"
    assert parsed["chain_drift_score"] == 1.0
    assert parsed["per_position_trajectory"][0]["regime"] == "deteriorating"


def test_sanitises_non_finite_values_to_null(fake_state):
    """Engineered NaN slope and ±inf score must serialise as JSON null,
    not "NaN" / "Infinity" literals.
    """
    fake_state.chain_drift_trajectory.return_value = {
        "chain_id": "CH-NAN",
        "chain_pattern": "chain_pattern",
        "member_pattern": "account_pattern",
        "n_members": 1,
        "n_members_with_history": 1,
        "n_members_skipped": 0,
        "n_members_short_history": 0,
        "n_windows": 4,
        "per_position_trajectory": [
            {
                "position": 0,
                "member_key": "A1",
                "delta_norms_over_time": [0.5, math.nan, 2.5, 3.5],
                "slope": math.nan,
                "regime": "flat",
            },
        ],
        "chain_level_regime": "flat",
        "chain_drift_score": math.inf,
    }
    body = chain_drift_trajectory(
        "CH-NAN",
        chain_pattern="chain_pattern",
        member_pattern="account_pattern",
    )
    # Strict JSON parse must succeed (no Infinity/NaN literals)
    parsed = json.loads(body)
    assert parsed["per_position_trajectory"][0]["slope"] is None
    assert None in parsed["per_position_trajectory"][0]["delta_norms_over_time"]
    assert parsed["chain_drift_score"] is None
    # Defence in depth: raw body has no Infinity/NaN tokens
    assert "NaN" not in body
    assert "Infinity" not in body


def test_tool_is_registered_in_base_tier():
    """Tier mapping check — must appear as 'base' so it surfaces only
    after sphere_overview (matches existing chain tools).
    """
    assert _TOOL_TIERS.get("chain_drift_trajectory") == "base"


def test_returns_json_error_on_value_error(fake_state):
    """Navigator-raised ValueError must surface as JSON error envelope,
    not an unhandled Python exception bubbling to the MCP framework.
    """
    fake_state.chain_drift_trajectory.side_effect = ValueError(
        "test_error_message_for_chain_drift"
    )
    body = chain_drift_trajectory(
        "test_chain",
        chain_pattern="x",
        member_pattern="y",
    )
    result = json.loads(body)
    assert "error" in result
    assert result["error"] == "test_error_message_for_chain_drift"
    assert result["chain_id"] == "test_chain"
