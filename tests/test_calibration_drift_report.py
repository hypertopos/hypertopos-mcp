# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for calibration_drift_report.

Coverage matrix:
- Unit: each drift band maps to the documented verdict.
- Discriminator: three engineered overall_drift_rms values (low/mid/high) MUST
  produce three distinct drift_verdict labels.
- JSON sanitisation: a ±inf / NaN drift value must surface as JSON null.
- Tier registration: must appear in _TOOL_TIERS as "base".
- error envelope: a ValueError from the underlying compare_calibrations returns
  a JSON error, not an unhandled exception.
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos.model.sphere import CalibrationDriftReport, DimensionDrift
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import calibration_drift_report


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


def _dim_drift(idx: int, mu_norm: float) -> DimensionDrift:
    return DimensionDrift(
        dim_index=idx,
        dim_kind="count",
        mu_from=1.0,
        mu_to=1.0 + mu_norm,
        mu_delta=mu_norm,
        mu_delta_normalized=mu_norm,
        sigma_from=1.0,
        sigma_to=1.0,
        sigma_delta=0.0,
        theta_from=2.0,
        theta_to=2.0,
        theta_delta=0.0,
    )


def _report(rms: float) -> CalibrationDriftReport:
    return CalibrationDriftReport(
        pattern_id="account_pattern",
        v_from=2,
        v_to=3,
        schema_hash="abc123",
        population_size_from=1000,
        population_size_to=1010,
        overall_drift_rms=rms,
        top_drifted=[_dim_drift(0, rms)],
        per_dimension=None,
    )


def test_tool_is_registered_in_base_tier():
    assert _TOOL_TIERS.get("calibration_drift_report") == "base"


def test_stable_verdict(fake_nav):
    fake_nav.compare_calibrations.return_value = _report(0.05)
    parsed = json.loads(calibration_drift_report("account_pattern"))
    assert parsed["drift_verdict"] == "stable"
    assert parsed["overall_drift_rms"] == 0.05
    assert parsed["calibration_a"] == 2
    assert parsed["calibration_b"] == 3


def test_moderate_verdict(fake_nav):
    fake_nav.compare_calibrations.return_value = _report(0.20)
    parsed = json.loads(calibration_drift_report("account_pattern"))
    assert parsed["drift_verdict"] == "moderate"


def test_significant_verdict(fake_nav):
    fake_nav.compare_calibrations.return_value = _report(0.55)
    parsed = json.loads(calibration_drift_report("account_pattern"))
    assert parsed["drift_verdict"] == "significant"
    # Significant drift must route the agent to decompose_drift.
    assert any(
        "decompose_drift" in s for s in parsed["recommended_next_steps"]
    )


def test_discriminator_three_distinct_verdicts(fake_nav):
    verdicts = set()
    for rms in (0.05, 0.20, 0.55):
        fake_nav.compare_calibrations.return_value = _report(rms)
        verdicts.add(
            json.loads(calibration_drift_report("account_pattern"))[
                "drift_verdict"
            ]
        )
    assert verdicts == {"stable", "moderate", "significant"}, (
        f"Drift verdict collapsed — got {sorted(verdicts)}"
    )


def test_sanitises_non_finite_rms(fake_nav):
    fake_nav.compare_calibrations.return_value = _report(math.inf)
    body = calibration_drift_report("account_pattern")
    parsed = json.loads(body)
    assert parsed["overall_drift_rms"] is None
    assert "NaN" not in body
    assert "Infinity" not in body


def test_returns_json_error_on_value_error(fake_nav):
    fake_nav.compare_calibrations.side_effect = ValueError(
        "v_from and v_to must differ (both=3)"
    )
    body = calibration_drift_report("account_pattern", calibration_a=3,
                                    calibration_b=3)
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["pattern_id"] == "account_pattern"


def test_returns_json_error_on_calibration_not_found(fake_nav):
    """A garbage-collected calibration epoch raises CalibrationNotFoundError
    (a GDSError sibling of GDSNavigationError, NOT a subclass) — it must be
    caught and returned as a JSON error envelope, not bubbled as a 500."""
    from hypertopos import CalibrationNotFoundError

    fake_nav.compare_calibrations.side_effect = CalibrationNotFoundError(
        "calibration version 1 for 'account_pattern' was garbage-collected"
    )
    body = calibration_drift_report("account_pattern", calibration_a=1,
                                    calibration_b=3)
    parsed = json.loads(body)
    assert "error" in parsed
    assert "garbage-collected" in parsed["error"]
    assert parsed["pattern_id"] == "account_pattern"
