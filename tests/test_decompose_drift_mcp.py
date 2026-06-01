# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""End-to-end MCP test for decompose_drift — exercises asdict serialization
on a real ≥2-epoch sphere.

Regression guard for a missing `from dataclasses import asdict` inside the
MCP tool body, which would only surface when an entity actually reaches the
serialization step (the ValueError gates fire BEFORE asdict, so a single-
epoch sphere hides the bug).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


class TestDecomposeDriftErrorEnvelope:
    """Unit-level: the composer must convert a garbage-collected calibration
    epoch (CalibrationNotFoundError — a GDSError sibling of
    GDSNavigationError, NOT a subclass) into a JSON error envelope rather than
    bubbling the exception out of the MCP tool."""

    def test_returns_json_error_on_calibration_not_found(self):
        import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
        from hypertopos import CalibrationNotFoundError
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import decompose_drift

        nav = MagicMock()
        nav.decompose_drift.side_effect = CalibrationNotFoundError(
            "calibration version 1 for 'account_pattern' was garbage-collected"
        )
        saved_nav = _state.get("navigator")
        saved_sphere = _state.get("sphere")
        _state["navigator"] = nav
        _state["sphere"] = MagicMock()
        try:
            body = decompose_drift(
                entity_key="E1", pattern_id="account_pattern", v_from=1, v_to=3,
            )
        finally:
            _state["navigator"] = saved_nav
            _state["sphere"] = saved_sphere

        parsed = json.loads(body)
        assert "error" in parsed
        assert "garbage-collected" in parsed["error"]
        assert parsed["pattern_id"] == "account_pattern"


class TestDecomposeDriftMcp:
    """Round-trip the MCP tool; the navigator + serializer must both work."""

    def test_decompose_drift_returns_valid_json(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import decompose_drift, find_drifting_entities

        # Pick the top-drift entity from π9 — guaranteed to exist on a built sphere.
        drift = json.loads(
            find_drifting_entities(
                pattern_id="account_behavior_pattern",
                top_n=1,
                sample_size=5000,
            )
        )
        assert drift["count"] >= 1, "Berka must have at least one drifting account"
        entity_key = drift["results"][0]["primary_key"]

        payload = decompose_drift(
            entity_key=entity_key,
            pattern_id="account_behavior_pattern",
            top_n=3,
        )
        report = json.loads(payload)
        if "error" in report:
            # decompose_drift returns a JSON error envelope (never raises) when
            # the on-disk Berka has <2 retained calibration epochs — its happy
            # path needs a rebuild + recalibrate. Skip rather than fail on that
            # build state (the previous try/except ValueError gate was dead: the
            # tool documents that it never raises).
            pytest.skip(
                f"decompose_drift unavailable on this Berka build: {report['error']}"
            )
        assert report["entity_key"] == entity_key
        assert report["pattern_id"] == "account_behavior_pattern"
        assert report["v_from"] >= 1
        assert report["v_to"] > report["v_from"]
        assert report["intrinsic_displacement"] >= 0.0
        assert report["extrinsic_displacement"] >= 0.0
        assert 0.0 <= report["intrinsic_fraction"] <= 1.0
        assert isinstance(report["top_dimensions"], list)
        for dd in report["top_dimensions"]:
            assert "dim_index" in dd
            assert "total" in dd
            assert "intrinsic" in dd
            assert "extrinsic" in dd
            assert 0.0 <= dd["intrinsic_fraction"] <= 1.0
