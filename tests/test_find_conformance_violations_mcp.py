# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for find_conformance_violations.

Validates: passthrough wiring to navigator, tier registration (must
appear in _TOOL_TIERS as "base"), JSON error envelope on ValueError.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import find_conformance_violations


@pytest.fixture
def fake_state():
    """Stub a navigator into _state.navigator with a controllable
    find_conformance_violations return value.
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
    fake_state.find_conformance_violations.return_value = {
        "pattern_id": "account_pattern",
        "n_violations": 2,
        "violations": [
            {"primary_key": "E1", "rule_id": "high_volume_no_kyc", "severity": "high"},
            {"primary_key": "E2", "rule_id": "high_volume_no_kyc", "severity": "high"},
        ],
        "rules_evaluated": ["high_volume_no_kyc"],
        "manifest": {
            "rule_set_hash": "abc123",
            "evaluated_at": "2026-05-18T10:00:00Z",
            "n_rules": 1,
        },
        "warnings": [],
        "follow_up": [],
    }
    body = find_conformance_violations(
        "account_pattern",
        rule_id="high_volume_no_kyc",
        severity_min="medium",
        top_n=50,
    )
    fake_state.find_conformance_violations.assert_called_once_with(
        "account_pattern",
        rule_id="high_volume_no_kyc",
        severity_min="medium",
        top_n=50,
    )
    parsed = json.loads(body)
    assert parsed["pattern_id"] == "account_pattern"
    assert parsed["n_violations"] == 2
    assert len(parsed["violations"]) == 2
    assert parsed["violations"][0]["primary_key"] == "E1"
    assert parsed["manifest"]["rule_set_hash"] == "abc123"


def test_tool_is_registered_in_base_tier():
    """Tier mapping check — must appear as 'base' so it surfaces only
    after sphere_overview (matches existing analysis tools).
    """
    assert _TOOL_TIERS.get("find_conformance_violations") == "base"


def test_returns_json_error_on_value_error(fake_state):
    """Navigator-raised ValueError must surface as JSON error envelope,
    not an unhandled Python exception bubbling to the MCP framework.
    """
    fake_state.find_conformance_violations.side_effect = ValueError(
        "unknown pattern_id"
    )
    body = find_conformance_violations("missing_pattern")
    result = json.loads(body)
    assert result["error"] == "unknown pattern_id"
    assert result["pattern_id"] == "missing_pattern"
