# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Test that centroid_map continuous-mode error includes suggested_call."""

from __future__ import annotations

import json


class TestCentroidMapContinuousModeHint:
    """centroid_map continuous-mode errors must include suggested_call for agent recovery."""

    def test_continuous_mode_error_includes_suggested_call(self, open_berka_sphere):
        """When centroid_map fails on continuous-mode, response should include suggested_call."""
        from hypertopos_mcp.tools.analysis import get_centroid_map

        # tx_pattern has edge_max -> continuous mode; group_by_line="accounts"
        # triggers the "all edges use continuous mode" ValueError
        result = json.loads(get_centroid_map("tx_pattern", "accounts"))
        assert "error" in result
        assert "suggested_call" in result, (
            f"Missing suggested_call in continuous-mode error response. "
            f"Got keys: {list(result.keys())}"
        )
        sc = result["suggested_call"]
        assert "group_by_property" in sc, f"suggested_call should mention group_by_property: {sc}"

    def test_continuous_mode_suggested_call_references_entity_line(self, open_berka_sphere):
        """suggested_call must reference the entity line for self-grouping."""
        from hypertopos_mcp.tools.analysis import get_centroid_map

        result = json.loads(get_centroid_map("tx_pattern", "accounts"))
        assert "error" in result
        sc = result["suggested_call"]
        # The entity line for tx_pattern is "transactions"
        assert "transactions" in sc, (
            f"suggested_call should reference entity line 'transactions': {sc}"
        )

    def test_continuous_mode_error_lists_available_properties(self, open_berka_sphere):
        """Error response should include available string properties for the entity line."""
        from hypertopos_mcp.tools.analysis import get_centroid_map

        result = json.loads(get_centroid_map("tx_pattern", "accounts"))
        assert "error" in result
        assert "available_properties" in result, (
            f"Missing available_properties in response. Got keys: {list(result.keys())}"
        )
        props = result["available_properties"]
        assert isinstance(props, list)
        assert len(props) > 0, "available_properties should not be empty"
        # All properties should be prefixed with the entity line
        assert all("transactions:" in p for p in props), (
            f"Expected transactions:* properties, got: {props}"
        )
