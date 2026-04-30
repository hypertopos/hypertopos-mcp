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

import pytest


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

        try:
            payload = decompose_drift(
                entity_key=entity_key,
                pattern_id="account_behavior_pattern",
                top_n=3,
            )
        except ValueError as exc:
            # If the on-disk Berka has only one epoch, the ValueError is the
            # expected gate. Skip rather than fail — this test depends on the
            # sphere being rebuilt to format 2.4 with at least one recalibrate.
            if "at least 2 epochs" in str(exc):
                pytest.skip(
                    "Berka has only 1 retained calibration epoch on disk — "
                    "rebuild + recalibrate to expose decompose_drift's happy path"
                )
            raise

        report = json.loads(payload)
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
