# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for the diverse_explanations agent-correctness composer.

Distinct from test_find_diverse_explanations_mcp.py (which covers the raw tool).
This composer forces counterfactual validation ON and synthesises a
robustness_verdict from the per-hypothesis ``neutralizes_anomaly`` results.

Coverage matrix:
- Unit: each n_validated count maps to the documented verdict.
- Discriminator: four engineered hypothesis sets MUST produce four distinct
  robustness_verdict labels.
- validate=True is forced: the underlying navigator call always receives
  validate=True regardless of caller input.
- JSON sanitisation: ±inf / NaN from delta_norm / theta_norm → JSON null.
- Tier registration: must appear in _TOOL_TIERS as "base".
- error envelope: a ValueError from the navigator returns a JSON error.
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import diverse_explanations


@pytest.fixture
def fake_nav():
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    # _require_navigator → _require_sphere demands a non-None sphere; the tool
    # itself does not read the sphere object, so a sentinel suffices.
    _state["sphere"] = MagicMock()
    yield nav
    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def _hyp(hid, labels, neutralizes):
    return {
        "hypothesis_id": hid,
        "dim_labels": labels,
        "joint_contribution_pct": 40.0,
        "narrative": f"driven by {', '.join(labels)}",
        "validation": {
            "delta_norm_after_override": 1.0 if neutralizes else 9.0,
            "neutralizes_anomaly": neutralizes,
        },
    }


def _raw(hypotheses, *, delta_norm=8.0, theta_norm=3.0,
         diversity_score=1.0, degraded_reason=None):
    return {
        "primary_key": "E1",
        "pattern_id": "account_pattern",
        "delta_norm": delta_norm,
        "theta_norm": theta_norm,
        "n_hypotheses_requested": 3,
        "n_hypotheses_returned": len(hypotheses),
        "hypotheses": hypotheses,
        "diversity_score": diversity_score,
        "degraded_reason": degraded_reason,
    }


def test_tool_is_registered_in_base_tier():
    assert _TOOL_TIERS.get("diverse_explanations") == "base"


def test_forces_validate_true(fake_nav):
    fake_nav.find_diverse_explanations.return_value = _raw(
        [_hyp(1, ["a"], True)]
    )
    diverse_explanations("E1", "account_pattern", k=2)
    _, kwargs = fake_nav.find_diverse_explanations.call_args
    assert kwargs["validate"] is True
    assert kwargs["n_hypotheses"] == 2


def test_multi_cause_robust(fake_nav):
    fake_nav.find_diverse_explanations.return_value = _raw(
        [_hyp(1, ["a"], True), _hyp(2, ["b"], True)]
    )
    parsed = json.loads(diverse_explanations("E1", "account_pattern"))
    assert parsed["robustness_verdict"] == "multi_cause_robust"
    assert parsed["n_validated"] == 2


def test_single_cause(fake_nav):
    fake_nav.find_diverse_explanations.return_value = _raw(
        [_hyp(1, ["a"], True), _hyp(2, ["b"], False)]
    )
    parsed = json.loads(diverse_explanations("E1", "account_pattern"))
    assert parsed["robustness_verdict"] == "single_cause"
    assert parsed["n_validated"] == 1


def test_fragile(fake_nav):
    fake_nav.find_diverse_explanations.return_value = _raw(
        [_hyp(1, ["a"], False), _hyp(2, ["b"], False)]
    )
    parsed = json.loads(diverse_explanations("E1", "account_pattern"))
    assert parsed["robustness_verdict"] == "fragile"
    assert parsed["n_validated"] == 0


def test_insufficient_signal(fake_nav):
    fake_nav.find_diverse_explanations.return_value = _raw(
        [], degraded_reason="insufficient_diverse_mass", diversity_score=None
    )
    parsed = json.loads(diverse_explanations("E1", "account_pattern"))
    assert parsed["robustness_verdict"] == "insufficient_signal"
    assert parsed["n_hypotheses_returned"] == 0


def test_discriminator_four_distinct_verdicts(fake_nav):
    cases = {
        "multi_cause_robust": [_hyp(1, ["a"], True), _hyp(2, ["b"], True)],
        "single_cause": [_hyp(1, ["a"], True), _hyp(2, ["b"], False)],
        "fragile": [_hyp(1, ["a"], False), _hyp(2, ["b"], False)],
        "insufficient_signal": [],
    }
    verdicts = set()
    for hyps in cases.values():
        fake_nav.find_diverse_explanations.return_value = _raw(hyps)
        verdicts.add(
            json.loads(diverse_explanations("E1", "account_pattern"))[
                "robustness_verdict"
            ]
        )
    assert verdicts == set(cases.keys()), (
        f"Robustness verdict collapsed — got {sorted(verdicts)}"
    )


def test_sanitises_non_finite_magnitudes(fake_nav):
    fake_nav.find_diverse_explanations.return_value = _raw(
        [_hyp(1, ["a"], True)], delta_norm=math.inf, theta_norm=math.nan
    )
    body = diverse_explanations("E1", "account_pattern")
    parsed = json.loads(body)
    assert parsed["delta_norm"] is None
    assert parsed["theta_norm"] is None
    assert "NaN" not in body
    assert "Infinity" not in body


def test_returns_json_error_on_value_error(fake_nav):
    fake_nav.find_diverse_explanations.side_effect = ValueError(
        "entity 'ghost' not found"
    )
    body = diverse_explanations("ghost", "account_pattern")
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["primary_key"] == "ghost"
