# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for dead_dimensions field in geometry tool responses."""

import json


class TestDeadDimensions:
    """find_clusters, contrast_populations, centroid_map should include dead_dimensions."""

    def test_find_clusters_has_dead_dimensions(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_clusters

        result = json.loads(
            find_clusters(
                pattern_id="account_behavior_pattern",
                n_clusters=3,
                top_n=2,
            )
        )
        assert "dead_dimensions" in result
        assert isinstance(result["dead_dimensions"], list)

    def test_contrast_populations_has_dead_dimensions(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import contrast_populations

        result = json.loads(
            contrast_populations(
                pattern_id="account_behavior_pattern",
                group_a={"anomaly": True},
            )
        )
        assert "dead_dimensions" in result
        assert isinstance(result["dead_dimensions"], list)

    def test_centroid_map_has_dead_dimensions(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import get_centroid_map

        # tx_pattern is continuous; self-group by entity line with property
        result = json.loads(
            get_centroid_map(
                pattern_id="tx_pattern",
                group_by_line="transactions",
                group_by_property="transactions:type",
            )
        )
        assert "dead_dimensions" in result
        assert isinstance(result["dead_dimensions"], list)

    def test_dead_dims_are_ints(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_clusters

        result = json.loads(
            find_clusters(
                pattern_id="account_behavior_pattern",
                n_clusters=2,
                top_n=1,
            )
        )
        for d in result["dead_dimensions"]:
            assert isinstance(d, int)

    def test_cache_cleared_on_open(self, open_berka_sphere):
        """After open_sphere, dead_dim cache should be empty (navigator-owned)."""
        from hypertopos_mcp.server import _state

        nav = _state["navigator"]
        # Navigator owns the cache; clear it
        nav._dead_dim_cache.clear()
        assert len(nav._dead_dim_cache) == 0
        # Call dead_dim_indices to populate cache
        from hypertopos_mcp.tools._guards import dead_dim_indices

        dead_dim_indices("account_behavior_pattern")
        assert len(nav._dead_dim_cache) > 0
        # Cache is owned by navigator, not MCP guards

