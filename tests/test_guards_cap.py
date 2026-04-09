# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for adaptive_polygon_cap property-aware estimation."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_pattern(n_relations: int, pattern_type: str = "anchor", edge_max: list | None = None):
    p = MagicMock()
    p.relations = [MagicMock() for _ in range(n_relations)]
    p.pattern_type = pattern_type
    p.edge_max = edge_max
    return p


class TestAdaptivePolygonCap:
    def test_cap_with_28_props_is_lower_than_without(self):
        """customer_pattern (9 edges, 28 props) should have lower cap than 0 props."""
        from hypertopos_mcp.tools._guards import adaptive_polygon_cap

        pattern = _make_pattern(9, "anchor", edge_max=[1] * 9)
        cap_no_props = adaptive_polygon_cap(pattern, n_entity_props=0)
        cap_28_props = adaptive_polygon_cap(pattern, n_entity_props=28)
        assert cap_28_props < cap_no_props, (
            f"28-prop cap ({cap_28_props}) should be less than 0-prop cap ({cap_no_props})"
        )

    def test_cap_with_28_props_under_50k_budget(self):
        """51 polygons x 2500 chars = 127K > budget. Cap must be < 51 for 28-prop patterns."""
        from hypertopos_mcp.tools._guards import adaptive_polygon_cap

        pattern = _make_pattern(9, "anchor", edge_max=[1] * 9)
        cap = adaptive_polygon_cap(pattern, n_entity_props=28)
        assert cap <= 30, f"Cap {cap} too high for 28-property pattern"

    def test_cap_event_pattern_unaffected_by_entity_props(self):
        """Event patterns have enriched edges -- props add no extra overhead."""
        from hypertopos_mcp.tools._guards import adaptive_polygon_cap

        pattern = _make_pattern(10, "event", edge_max=None)
        cap_0 = adaptive_polygon_cap(pattern, n_entity_props=0)
        cap_14 = adaptive_polygon_cap(pattern, n_entity_props=14)
        assert cap_0 == cap_14, (
            f"Event cap should not change with n_entity_props: {cap_0} vs {cap_14}"
        )

    def test_backward_compat_default(self):
        """Calling without n_entity_props uses conservative default."""
        from hypertopos_mcp.tools._guards import adaptive_polygon_cap

        pattern = _make_pattern(9, "anchor", edge_max=[1] * 9)
        cap = adaptive_polygon_cap(pattern)
        assert cap < 51, f"Default cap {cap} unchanged"
