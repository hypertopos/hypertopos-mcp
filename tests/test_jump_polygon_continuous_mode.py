# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Test that jump_polygon continuous-mode error returns structured JSON, not raises."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from hypertopos.model.objects import Point


_CONTINUOUS_MODE_ERROR = (
    "Cannot jump to 'accounts': edge uses continuous mode "
    "(edge_max pattern) which stores edge counts, not entity keys. "
    "Use get_centroid_map(group_by_property=...) or aggregate() instead."
)


@pytest.fixture
def continuous_mode_nav(open_berka_sphere):
    """Replace navigator with a mock that raises ValueError for continuous-mode jump."""
    from hypertopos_mcp.server import _state

    pos = Point(
        primary_key="TX-0000000",
        line_id="transactions",
        version=1,
        status="active",
        properties={},
        created_at=datetime(1995, 1, 1, tzinfo=timezone.utc),
        changed_at=datetime(1995, 1, 1, tzinfo=timezone.utc),
    )

    nav = MagicMock()
    nav.position = pos
    mock_polygon = MagicMock()
    mock_polygon.count_alive_edges_to.return_value = 5
    nav.current_polygon.return_value = mock_polygon
    nav.π2_jump_polygon.side_effect = ValueError(_CONTINUOUS_MODE_ERROR)
    nav.suggest_grouping_properties.return_value = ["type", "operation", "bank"]

    original_nav = _state["navigator"]
    _state["navigator"] = nav
    yield nav
    _state["navigator"] = original_nav


class TestJumpPolygonContinuousModeError:
    """jump_polygon on continuous-mode edge must return JSON error, not raise."""

    def test_returns_json_not_raises(self, continuous_mode_nav):
        from hypertopos_mcp.tools.navigation import jump_polygon

        result_str = jump_polygon("accounts")
        result = json.loads(result_str)
        assert "error" in result

    def test_continuous_mode_flag(self, continuous_mode_nav):
        from hypertopos_mcp.tools.navigation import jump_polygon

        result = json.loads(jump_polygon("accounts"))
        assert result.get("continuous_mode") is True

    def test_has_pattern_and_target_line(self, continuous_mode_nav):
        from hypertopos_mcp.tools.navigation import jump_polygon

        result = json.loads(jump_polygon("accounts"))
        assert result.get("pattern_id") == "tx_pattern"
        assert result.get("target_line_id") == "accounts"

    def test_hint_mentions_continuous_mode_and_alternatives(self, continuous_mode_nav):
        from hypertopos_mcp.tools.navigation import jump_polygon

        result = json.loads(jump_polygon("accounts"))
        hint = result.get("hint", "")
        assert "continuous" in hint.lower()
        assert "get_centroid_map" in hint or "aggregate" in hint

    def test_suggested_call_references_entity_line_and_property(self, continuous_mode_nav):
        from hypertopos_mcp.tools.navigation import jump_polygon

        result = json.loads(jump_polygon("accounts"))
        assert "suggested_call" in result, (
            f"Missing suggested_call. Got keys: {list(result.keys())}"
        )
        sc = result["suggested_call"]
        assert "group_by_property" in sc
        assert "transactions" in sc

    def test_available_properties_prefixed_with_entity_line(self, continuous_mode_nav):
        from hypertopos_mcp.tools.navigation import jump_polygon

        result = json.loads(jump_polygon("accounts"))
        assert "available_properties" in result
        props = result["available_properties"]
        assert len(props) > 0
        assert all("transactions:" in p for p in props), (
            f"Expected transactions:* properties, got: {props}"
        )
