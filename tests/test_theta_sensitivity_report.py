# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for theta_sensitivity_report.

Coverage matrix:
- Unit: each band/cliff structure maps to the documented recalibration_safety.
- Discriminator: three engineered structures (band+no-cliff / band+cliff /
  no-band) MUST produce three distinct recalibration_safety labels.
- JSON sanitisation: a ±inf / NaN cliff ratio must serialise as JSON null.
- Tier registration: must appear in _TOOL_TIERS as "base".
- error envelope: a ValueError (pre-diagnostic epoch / no epochs) returns a
  JSON error.
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos.model.sphere import ThetaSensitivityReport
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import theta_sensitivity_report


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


def _sweep(percentiles=(90, 95, 99)):
    return {
        f"p{p}": {
            "theta_mean": 2.0,
            "theta_std": 0.0,
            "anomaly_count_mean": 10.0,
            "anomaly_count_std": 0.0,
            "anomaly_rate": 0.05,
        }
        for p in percentiles
    }


def _report(*, band_length, cliffs):
    band = (
        {"from": "p90", "to": "p99", "length": band_length}
        if band_length > 0
        else {"from": None, "to": None, "length": 0}
    )
    return ThetaSensitivityReport(
        pattern_id="account_pattern",
        calibration_epoch=3,
        population_size=1000,
        theta_sensitivity=_sweep(),
        stable_band=band,
        cliffs=cliffs,
        n_cliffs=len(cliffs),
        stable_band_length=band_length,
    )


def test_tool_is_registered_in_base_tier():
    assert _TOOL_TIERS.get("theta_sensitivity_report") == "base"


def test_safe_verdict_band_no_cliff(fake_nav):
    fake_nav.theta_sensitivity.return_value = _report(band_length=10, cliffs=[])
    parsed = json.loads(theta_sensitivity_report("account_pattern"))
    assert parsed["recalibration_safety"] == "safe"
    assert parsed["n_cliffs"] == 0


def test_caution_verdict_band_with_cliff(fake_nav):
    fake_nav.theta_sensitivity.return_value = _report(
        band_length=5,
        cliffs=[{"from": "p97", "to": "p98", "ratio": 1.8}],
    )
    parsed = json.loads(theta_sensitivity_report("account_pattern"))
    assert parsed["recalibration_safety"] == "caution"


def test_unsafe_verdict_no_band(fake_nav):
    fake_nav.theta_sensitivity.return_value = _report(
        band_length=0,
        cliffs=[{"from": "p90", "to": "p91", "ratio": 1.6}],
    )
    parsed = json.loads(theta_sensitivity_report("account_pattern"))
    assert parsed["recalibration_safety"] == "unsafe"


def test_discriminator_three_distinct_verdicts(fake_nav):
    cases = [
        ("safe", _report(band_length=10, cliffs=[])),
        ("caution", _report(band_length=5,
                            cliffs=[{"from": "p97", "to": "p98", "ratio": 1.8}])),
        ("unsafe", _report(band_length=0,
                          cliffs=[{"from": "p90", "to": "p91", "ratio": 1.6}])),
    ]
    verdicts = set()
    for _, rep in cases:
        fake_nav.theta_sensitivity.return_value = rep
        verdicts.add(
            json.loads(theta_sensitivity_report("account_pattern"))[
                "recalibration_safety"
            ]
        )
    assert verdicts == {"safe", "caution", "unsafe"}, (
        f"Recalibration safety verdict collapsed — got {sorted(verdicts)}"
    )


def test_sanitises_non_finite_cliff_ratio(fake_nav):
    fake_nav.theta_sensitivity.return_value = _report(
        band_length=5,
        cliffs=[{"from": "p97", "to": "p98", "ratio": math.inf}],
    )
    body = theta_sensitivity_report("account_pattern")
    parsed = json.loads(body)
    assert parsed["cliffs"][0]["ratio"] is None
    assert "NaN" not in body
    assert "Infinity" not in body


def test_returns_json_error_on_value_error(fake_nav):
    fake_nav.theta_sensitivity.side_effect = ValueError(
        "calibration epoch v=3 was built before the diagnostic was wired in"
    )
    body = theta_sensitivity_report("account_pattern")
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["pattern_id"] == "account_pattern"


def test_returns_json_error_on_calibration_not_found(fake_nav):
    """A garbage-collected calibration epoch raises CalibrationNotFoundError
    (a GDSError sibling of GDSNavigationError, NOT a subclass) — it must be
    caught and returned as a JSON error envelope, not bubbled as a 500."""
    from hypertopos import CalibrationNotFoundError

    fake_nav.theta_sensitivity.side_effect = CalibrationNotFoundError(
        "calibration version 4 for 'account_pattern' was garbage-collected"
    )
    body = theta_sensitivity_report("account_pattern", version=4)
    parsed = json.loads(body)
    assert "error" in parsed
    assert "garbage-collected" in parsed["error"]
    assert parsed["pattern_id"] == "account_pattern"
