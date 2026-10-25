# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for the per-session 3-phase tool visibility model."""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import patch

import anyio
import pytest

# Import tool modules to register them with the MCPServer instance
# (same as main.py does at startup).
import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401
from hypertopos_mcp.server import (
    _TOOL_TIERS,
    _register_manual_tools,
    _register_phase2_tools,
    _state,
    _unregister_phase2_tools,
    mcp,
    session_scope,
    visible_tools,
)

_ALWAYS = {"open_sphere", "close_sphere", "get_session_stats"}
_GATEWAY = {"detect_pattern", "sphere_overview"}
# Edge tier — available alongside gateway in Phase 2.
# Mirrors the "edge" entries in server._TOOL_TIERS.
_EDGE = {
    "find_geometric_path",
    "edge_stats",
    "entity_flow",
    "contagion_score",
    "contagion_score_batch",
    "degree_velocity",
    "investigation_coverage",
    "propagate_influence",
    "cluster_bridges",
    "anomalous_edges",
    "find_witness_cohort",
    "find_novel_entities",
    "find_graph_geometry_tension",
    "simulate_edge_removal",
    "simulate_dimension_change",
    "simulate_counterparty_removal",
    "select_minimal_joint_edge_removal",
}
_ALL_CAPS = {"has_temporal": True, "multi_pattern": True, "has_trajectory_index": True}


@pytest.fixture(autouse=True)
def _phase_isolation():
    """Each test runs in its own client session, so phases never leak between tests."""
    with session_scope("test:dynamic-tools"):
        yield
        _unregister_phase2_tools()


class TestToolTierMap:
    def test_all_tools_have_tier(self):
        """Every registered tool must appear in _TOOL_TIERS."""
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        missing = registered - set(_TOOL_TIERS)
        assert missing == set(), f"Tools without tier: {missing}"

    def test_tool_registry_is_fixed(self):
        """Phases change visibility only; every tool stays registered."""
        before = {t.name for t in mcp._tool_manager.list_tools()}
        _unregister_phase2_tools()
        assert {t.name for t in mcp._tool_manager.list_tools()} == before == set(_TOOL_TIERS)

    def test_always_tier_has_exactly_3(self):
        always = {n for n, t in _TOOL_TIERS.items() if t == "always"}
        assert always == _ALWAYS

    def test_gateway_tier_has_exactly_2(self):
        gateway = {n for n, t in _TOOL_TIERS.items() if t == "gateway"}
        assert gateway == _GATEWAY


class TestDetectCapabilitiesMultiPattern:
    """_detect_capabilities must count all cross-pattern key types, not just direct/sibling."""

    def _make_state(self, pattern_map: dict[str, str]):
        """Build minimal _state mock where navigator returns *pattern_map* for any line."""
        from unittest.mock import MagicMock

        sphere = MagicMock()
        patterns = {
            "p_anchor": MagicMock(pattern_type="anchor"),
        }
        sphere.patterns = patterns
        sphere.entity_line.return_value = "line_accounts"

        nav = MagicMock()
        nav._discover_pattern_map.return_value = pattern_map

        hyper = MagicMock()
        hyper._sphere = sphere

        return {"sphere": hyper, "path": "fake/path", "navigator": nav}

    @pytest.mark.parametrize(
        ("pattern_map", "expected"),
        [
            ({"p1": "direct", "p2": "event_edge"}, True),
            ({"p1": "direct", "p2": "composite"}, True),
            ({"p1": "direct", "p2": "chain"}, True),
            ({"p1": "direct"}, False),
        ],
    )
    def test_cross_pattern_kinds(self, pattern_map, expected):
        _state.update(self._make_state(pattern_map))
        with (
            patch("pathlib.Path.exists", return_value=False),
            patch("pathlib.Path.glob", return_value=iter([])),
        ):
            from hypertopos_mcp.server import _detect_capabilities

            caps = _detect_capabilities()
        assert caps["multi_pattern"] is expected


class TestPhases:
    def test_phase1_only_always_tools(self):
        _unregister_phase2_tools()
        assert visible_tools() == _ALWAYS

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_phase2_gateway_and_edge(self, _caps):
        _register_phase2_tools()
        assert visible_tools() == _ALWAYS | _GATEWAY | _EDGE
        assert _state.get("manual_mode") is False

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_phase3_full_toolset(self, _caps):
        _register_phase2_tools()
        _register_manual_tools()
        assert visible_tools() == set(_TOOL_TIERS)

    @pytest.mark.parametrize(
        ("missing_cap", "tier"),
        [
            ("has_temporal", "temporal"),
            ("multi_pattern", "multi_pattern"),
            ("has_trajectory_index", "trajectory_index"),
        ],
    )
    def test_phase3_hides_tiers_the_sphere_cannot_serve(self, missing_cap, tier):
        caps = {**_ALL_CAPS, missing_cap: False}
        with patch("hypertopos_mcp.server._detect_capabilities", return_value=caps):
            _register_phase2_tools()
            _register_manual_tools()
        hidden = {n for n, t in _TOOL_TIERS.items() if t == tier}
        assert hidden.isdisjoint(visible_tools())

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_manual_mode_idempotent(self, _caps):
        _register_phase2_tools()
        _register_manual_tools()
        first = visible_tools()
        _register_manual_tools()
        assert visible_tools() == first

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_capabilities_resource_reads_the_session(self, _caps):
        import json

        from hypertopos_mcp.server import sphere_capabilities_resource

        assert "error" in json.loads(sphere_capabilities_resource())
        _register_phase2_tools()
        assert json.loads(sphere_capabilities_resource()) == _ALL_CAPS

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_phases_are_per_session(self, _caps):
        _register_phase2_tools()
        _register_manual_tools()
        with session_scope("test:other-session"):
            assert visible_tools() == _ALWAYS
        assert visible_tools() == set(_TOOL_TIERS)


def _middleware(session_id: str, method: str, params: dict | None, result: dict) -> dict:
    from hypertopos_mcp.server import _serialize_tool_calls

    ctx = SimpleNamespace(
        session=SimpleNamespace(
            _connection=SimpleNamespace(
                session_id=session_id, state={}, exit_stack=AsyncExitStack()
            )
        ),
        request=None,
        method=method,
        params=params,
    )

    async def call_next(_ctx: object) -> dict:
        return result

    async def main() -> dict:
        return await _serialize_tool_calls(ctx, call_next)

    return anyio.run(main)


class TestMiddlewareVisibility:
    def test_tools_list_is_filtered_per_session(self):
        listing = {"tools": [{"name": n} for n in _TOOL_TIERS]}
        out = _middleware("phase1-client", "tools/list", None, listing)
        assert {t["name"] for t in out["tools"]} == _ALWAYS

    def test_hidden_tool_call_is_refused(self):
        with pytest.raises(Exception, match="sphere_overview|open_sphere"):
            _middleware("phase1-client-2", "tools/call", {"name": "find_anomalies"}, {})

    def test_visible_tool_call_passes(self):
        out = _middleware("phase1-client-3", "tools/call", {"name": "open_sphere"}, {"ok": 1})
        assert out == {"ok": 1}
