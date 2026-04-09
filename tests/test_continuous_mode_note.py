# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for continuous_mode_note in sphere_overview."""

from __future__ import annotations

import json

import numpy as np


class TestContinuousModeNote:
    """sphere_overview should include continuous_mode_note for patterns with edge_max."""

    def test_continuous_pattern_has_note(self, open_berka_sphere):
        """tx_pattern has edge_max -> must include continuous_mode_note."""
        from hypertopos_mcp.tools.observability import sphere_overview

        result = json.loads(sphere_overview(pattern_id="tx_pattern"))
        assert len(result) == 1
        entry = result[0]
        assert "continuous_mode_note" in entry, (
            "Missing continuous_mode_note for pattern with edge_max"
        )
        assert "group_by_property" in entry["continuous_mode_note"]
        assert "centroid_map" in entry["continuous_mode_note"]
        assert "contrast_populations" in entry["continuous_mode_note"]

    def test_discrete_pattern_has_no_note(self, open_berka_sphere):
        """Temporarily remove edge_max to verify discrete pattern has no note."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.observability import sphere_overview

        sphere = _state["sphere"]._sphere
        pattern = sphere.patterns["account_behavior_pattern"]
        original_edge_max = pattern.edge_max

        try:
            # Remove edge_max to simulate a discrete pattern
            pattern.edge_max = None
            result = json.loads(sphere_overview(pattern_id="account_behavior_pattern"))
            assert len(result) == 1
            entry = result[0]
            assert "continuous_mode_note" not in entry, (
                "Discrete pattern should not have continuous_mode_note"
            )
        finally:
            pattern.edge_max = original_edge_max

    def test_note_content_mentions_alternatives(self, open_berka_sphere):
        """The note must mention both unavailable operations and the alternative."""
        from hypertopos_mcp.tools.observability import sphere_overview

        result = json.loads(sphere_overview(pattern_id="tx_pattern"))
        note = result[0]["continuous_mode_note"]
        assert "centroid_map(group_by_line)" in note
        assert "contrast_populations(edge spec)" in note
        assert "group_by_property" in note

    def test_all_patterns_overview_includes_note_where_applicable(self, open_berka_sphere):
        """When fetching all patterns, only those with edge_max get the note."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.observability import sphere_overview

        result = json.loads(sphere_overview())
        sphere = _state["sphere"]._sphere

        for entry in result:
            pid = entry["pattern_id"]
            pattern = sphere.patterns.get(pid)
            if pattern is not None and pattern.edge_max is not None:
                assert "continuous_mode_note" in entry, (
                    f"Pattern {pid} has edge_max but missing continuous_mode_note"
                )
            else:
                assert "continuous_mode_note" not in entry, (
                    f"Pattern {pid} has no edge_max but got continuous_mode_note"
                )

    def test_note_with_mocked_anchor_edge_max(self, open_berka_sphere):
        """Verify note appears for an anchor pattern when edge_max is injected."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.observability import sphere_overview

        sphere = _state["sphere"]._sphere
        pattern = sphere.patterns["account_behavior_pattern"]
        original_edge_max = pattern.edge_max

        try:
            # Inject edge_max to ensure continuous-mode anchor
            pattern.edge_max = np.array([1.0, 1.0], dtype=np.float32)
            result = json.loads(sphere_overview(pattern_id="account_behavior_pattern"))
            entry = result[0]
            assert "continuous_mode_note" in entry, (
                "Anchor pattern with injected edge_max should have continuous_mode_note"
            )
        finally:
            pattern.edge_max = original_edge_max

