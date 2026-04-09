# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Test that get_polygon includes temporal metadata for anchor patterns."""

from __future__ import annotations

import json


class TestPolygonTemporalHint:
    """get_polygon should surface last_deformation_timestamp."""

    def test_anchor_polygon_has_temporal_fields(self, open_berka_sphere):
        """Anchor pattern polygon should include temporal_hint."""
        from hypertopos_mcp.tools.geometry import get_polygon
        from hypertopos_mcp.tools.navigation import goto

        goto("1", "accounts")
        result = json.loads(get_polygon("account_behavior_pattern"))
        assert "error" not in result, result
        assert "temporal_hint" in result, "get_polygon missing temporal_hint for anchor pattern"
        hint = result["temporal_hint"]
        assert "last_deformation_timestamp" in hint
        assert "num_slices" in hint
        assert isinstance(hint["num_slices"], int)
        assert hint["num_slices"] >= 0

    def test_event_polygon_has_no_temporal_hint(self, open_berka_sphere):
        """Event pattern polygons are immutable -- no temporal_hint."""
        from hypertopos_mcp.tools.geometry import get_polygon
        from hypertopos_mcp.tools.navigation import goto

        goto("TX-0000001", "transactions")
        result = json.loads(get_polygon("tx_pattern"))
        assert "error" not in result, result
        assert "temporal_hint" not in result, "Event pattern should not have temporal_hint"

