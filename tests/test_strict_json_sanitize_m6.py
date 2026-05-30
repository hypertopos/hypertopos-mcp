# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""M6(b) strict-JSON sanitize regression — navigator-math float emitters that
previously serialised ±inf / NaN raw must now emit JSON null.

Each test stubs the navigator method to return a payload carrying a non-finite
float and asserts (1) the field round-trips to null, (2) the raw body contains
no ``Infinity`` / ``NaN`` literal tokens (RFC 8259 strict parsers reject them).

Covers the tools wrapped in the M6 sanitize sweep across detection.py,
observability.py, and navigation.py — the previously-unsanitized navigator
float emitters.
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.detection  # noqa: F401 — register tools
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import pytest
from hypertopos_mcp.server import _state
from hypertopos_mcp.tools.detection import (
    detect_neighbor_contamination,
    detect_segment_shift,
)
from hypertopos_mcp.tools.navigation import anomaly_summary
from hypertopos_mcp.tools.observability import check_alerts


@pytest.fixture
def fake_nav():
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    _state["sphere"] = MagicMock()
    yield nav
    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def _assert_strict_json(body: str) -> dict:
    """Raw body must contain no non-finite literals and parse strictly."""
    assert "Infinity" not in body
    assert "NaN" not in body
    return json.loads(body)


def test_detect_neighbor_contamination_sanitizes(fake_nav):
    fake_nav.detect_neighbor_contamination.return_value = [
        {"primary_key": "E1", "contamination_rate": math.inf,
         "anomalous_neighbor_count": 5},
    ]
    body = detect_neighbor_contamination("account_pattern")
    parsed = _assert_strict_json(body)
    assert parsed["results"][0]["contamination_rate"] is None


def test_detect_segment_shift_sanitizes(fake_nav):
    sphere = MagicMock()
    sphere.entity_line.return_value = "accounts"
    sphere.lines = {}
    fake_nav_state_sphere = MagicMock()
    fake_nav_state_sphere._sphere = sphere
    _state["sphere"] = fake_nav_state_sphere
    fake_nav.detect_segment_shift.return_value = [
        {"segment": "x", "anomaly_rate": math.nan, "shift_ratio": math.inf,
         "entity_count": 3},
    ]
    body = detect_segment_shift("account_pattern")
    parsed = _assert_strict_json(body)
    assert parsed["results"][0]["anomaly_rate"] is None
    assert parsed["results"][0]["shift_ratio"] is None


def test_anomaly_summary_sanitizes(fake_nav, monkeypatch):
    # binary_geometry_note_for_pattern reads the sphere; stub it to a no-op.
    monkeypatch.setattr(
        "hypertopos_mcp.tools.navigation.binary_geometry_note_for_pattern",
        lambda _pid: None,
    )
    fake_nav.anomaly_summary.return_value = {
        "anomaly_count": 10,
        "anomaly_rate": math.nan,
        "delta_norm_percentiles": {"p95": math.inf},
    }
    body = anomaly_summary("account_pattern")
    parsed = _assert_strict_json(body)
    assert parsed["anomaly_rate"] is None
    assert parsed["delta_norm_percentiles"]["p95"] is None


def test_check_alerts_sanitizes(fake_nav):
    fake_nav.check_alerts.return_value = {
        "alerts": [{"check": "anomaly_rate", "severity": math.inf,
                    "value": math.nan}],
    }
    body = check_alerts("account_pattern")
    parsed = _assert_strict_json(body)
    assert parsed["alerts"][0]["severity"] is None
    assert parsed["alerts"][0]["value"] is None
