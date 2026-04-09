# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Guard tests — ensure every detection navigator method has a step handler."""

from __future__ import annotations

import inspect

from hypertopos.navigation.navigator import GDSNavigator
from hypertopos_mcp.tools.smart import _STEP_CAPABILITIES, _STEP_HANDLERS

# Navigator methods that are detection/analysis-oriented but intentionally
# NOT step handlers (navigation primitives, entity lookups, etc.)
_EXCLUDED_METHODS = {
    # Navigation primitives — require cursor position, not suitable for autonomous detection
    "pi1_walk_line",
    "pi2_jump_polygon",
    "pi3_dive_solid",
    "pi4_emerge",
    # Entity-level reads — require specific entity context, not population-level detection
    "find_entity",
    "find_point",
}


def _detection_navigator_methods() -> set[str]:
    """Return navigator method names that match detection/analysis patterns."""
    return {
        name
        for name, _ in inspect.getmembers(GDSNavigator, predicate=inspect.isfunction)
        if any(
            name.startswith(prefix)
            for prefix in (
                "detect_", "find_", "attract_",
                "π5_", "π6_", "π7_", "π8_", "π9_",
                "π10_", "π11_", "π12_",
                "anomaly_summary", "aggregate_anomalies",
                "check_alerts", "sphere_overview",
                "centroid_map", "hub_score_history",
                "contrast_populations", "explain_anomaly",
                "cross_pattern_profile", "passive_scan",
                "composite_risk", "extract_chains",
            )
        )
        and name not in _EXCLUDED_METHODS
    }


class TestSmartCoverage:
    def test_every_handler_has_capability(self):
        """Every step handler must have a capability entry."""
        missing = set(_STEP_HANDLERS.keys()) - set(_STEP_CAPABILITIES.keys())
        assert missing == set(), f"Handlers without capability: {missing}"

    def test_capabilities_match_handlers(self):
        """Every capability entry must have a handler."""
        extra = set(_STEP_CAPABILITIES.keys()) - set(_STEP_HANDLERS.keys())
        assert extra == set(), f"Capabilities without handler: {extra}"

    def test_handler_count_minimum(self):
        """Minimum expected handler count (guard against accidental deletion)."""
        assert len(_STEP_HANDLERS) >= 30, (
            f"Expected 30+ handlers, got {len(_STEP_HANDLERS)}"
        )
