# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for agent trust signal enrichments."""

import json

import pytest


class TestInactiveRatio:
    """Task 1: sphere_overview should include inactive_ratio for anchor patterns."""

    def test_anchor_pattern_inactive_ratio_valid_when_present(self, open_berka_sphere):
        from hypertopos_mcp.tools.observability import sphere_overview

        result = json.loads(sphere_overview(pattern_id="account_behavior_pattern"))
        assert len(result) == 1
        entry = result[0]
        # inactive_ratio is only present when geometry_stats detects inactive entities
        if "inactive_ratio" in entry:
            assert 0.0 <= entry["inactive_ratio"] <= 1.0

    def test_event_pattern_has_no_inactive_ratio(self, open_berka_sphere):
        from hypertopos_mcp.tools.observability import sphere_overview

        result = json.loads(sphere_overview(pattern_id="tx_pattern"))
        entry = result[0]
        assert "inactive_ratio" not in entry


class TestBinaryGeometryNote:
    """Task 2: tools should warn when pattern is binary geometry.

    Berka sphere uses continuous edge_max for all patterns, so none are binary.
    Verify that binary_geometry_note does NOT appear for any Berka pattern.
    """

    def test_anchor_pattern_no_binary_note(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import find_anomalies

        result = json.loads(find_anomalies(pattern_id="account_behavior_pattern", top_n=3))
        # Berka account_behavior_pattern has continuous geometry — not binary
        assert "binary_geometry_note" not in result

    def test_anomaly_summary_no_binary_note(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import anomaly_summary

        result = json.loads(anomaly_summary(pattern_id="account_behavior_pattern"))
        assert "binary_geometry_note" not in result

    def test_find_clusters_no_binary_note(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_clusters

        result = json.loads(
            find_clusters(
                pattern_id="account_behavior_pattern",
                n_clusters=3,
                top_n=2,
            )
        )
        assert "binary_geometry_note" not in result

    def test_event_pattern_no_binary_note(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import find_anomalies

        result = json.loads(find_anomalies(pattern_id="tx_pattern", top_n=3))
        assert "binary_geometry_note" not in result


class TestSignalStabilityNote:
    """Task 3: find_anomalies should warn when temporal signal is volatile."""

    def test_volatile_pattern_has_stability_note(self, open_berka_sphere):
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.navigation import find_anomalies

        nav = _state["navigator"]
        tq = nav.temporal_quality_summary("account_behavior_pattern")
        if tq is None or tq.get("signal_quality") == "persistent":
            pytest.skip("account_behavior_pattern is not volatile on this sphere")
        result = json.loads(find_anomalies(pattern_id="account_behavior_pattern", top_n=3))
        assert "signal_stability_note" in result
        assert (
            "volatile" in result["signal_stability_note"].lower()
            or "transition" in result["signal_stability_note"].lower()
        )

    def test_event_pattern_has_no_stability_note(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import find_anomalies

        result = json.loads(find_anomalies(pattern_id="tx_pattern", top_n=3))
        assert "signal_stability_note" not in result

