# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for dynamic MCP tool registration lifecycle (3-phase model)."""

from __future__ import annotations

from unittest.mock import patch

# Import tool modules to register them with the FastMCP instance
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
    _restore_tool,
    _state,
    _tool_stash,
    _unregister_phase2_tools,
    mcp,
)

_ALWAYS = {"open_sphere", "close_sphere", "get_session_stats"}
_GATEWAY = {"detect_pattern", "sphere_overview"}
# Edge tier — always available alongside gateway in Phase 2 (added in 0.2.0).
# Mirrors the "edge" entries in server._TOOL_TIERS.
_EDGE = {
    "find_geometric_path", "discover_chains", "edge_stats", "entity_flow",
    "contagion_score", "contagion_score_batch", "degree_velocity",
    "investigation_coverage", "propagate_influence", "cluster_bridges",
    "anomalous_edges", "find_witness_cohort",
}


def _visible_tool_names() -> set[str]:
    return {t.name for t in mcp._tool_manager.list_tools()}


def _restore_all() -> None:
    """Restore all stashed tools (test cleanup)."""
    for name in list(_tool_stash.keys()):
        _restore_tool(name)


class TestToolTierMap:
    def test_all_tools_have_tier(self):
        """Every registered + stashed tool must appear in _TOOL_TIERS."""
        _restore_all()
        registered = _visible_tool_names()
        tiered = set(_TOOL_TIERS.keys())
        missing = registered - tiered
        assert missing == set(), f"Tools without tier: {missing}"
        _unregister_phase2_tools()  # restore phase1 state

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

    @patch("hypertopos_mcp.server._state", new_callable=dict)
    def test_event_edge_counts_as_cross_pattern(self, mock_state):
        mock_state.update(self._make_state({"p1": "direct", "p2": "event_edge"}))
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.glob", return_value=iter([])):
            from hypertopos_mcp.server import _detect_capabilities
            caps = _detect_capabilities()
        assert caps["multi_pattern"] is True

    @patch("hypertopos_mcp.server._state", new_callable=dict)
    def test_composite_counts_as_cross_pattern(self, mock_state):
        mock_state.update(self._make_state({"p1": "direct", "p2": "composite"}))
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.glob", return_value=iter([])):
            from hypertopos_mcp.server import _detect_capabilities
            caps = _detect_capabilities()
        assert caps["multi_pattern"] is True

    @patch("hypertopos_mcp.server._state", new_callable=dict)
    def test_chain_counts_as_cross_pattern(self, mock_state):
        mock_state.update(self._make_state({"p1": "direct", "p2": "chain"}))
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.glob", return_value=iter([])):
            from hypertopos_mcp.server import _detect_capabilities
            caps = _detect_capabilities()
        assert caps["multi_pattern"] is True

    @patch("hypertopos_mcp.server._state", new_callable=dict)
    def test_single_direct_not_multi(self, mock_state):
        mock_state.update(self._make_state({"p1": "direct"}))
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.glob", return_value=iter([])):
            from hypertopos_mcp.server import _detect_capabilities
            caps = _detect_capabilities()
        assert caps["multi_pattern"] is False


class TestPhase1Mode:
    """Phase 1: before open_sphere — only always tools."""

    def setup_method(self):
        _restore_all()

    def test_only_always_tools_visible(self):
        _unregister_phase2_tools()
        visible = _visible_tool_names()
        assert visible == _ALWAYS

    def test_phase2_tools_stashed(self):
        _unregister_phase2_tools()
        assert len(_tool_stash) > 0
        for name in _tool_stash:
            assert _TOOL_TIERS.get(name) != "always"

    def teardown_method(self):
        _restore_all()


class TestPhase2Gateway:
    """Phase 2: after open_sphere — only always + gateway tools."""

    def setup_method(self):
        _restore_all()
        _unregister_phase2_tools()

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_gateway_only_after_open(self, mock_caps):
        mock_caps.return_value = {
            "has_temporal": True,
            "multi_pattern": True,
            "has_trajectory_index": True,
        }
        _register_phase2_tools()
        visible = _visible_tool_names()
        assert visible == _ALWAYS | _GATEWAY | _EDGE

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_manual_mode_false_after_open(self, mock_caps):
        mock_caps.return_value = {"has_temporal": False, "multi_pattern": False, "has_trajectory_index": False}
        _register_phase2_tools()
        assert _state.get("manual_mode") is False

    def teardown_method(self):
        _restore_all()


class TestPhase3ManualMode:
    """Phase 3: after sphere_overview — full toolset unlocked."""

    def setup_method(self):
        _restore_all()
        _unregister_phase2_tools()

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_full_capabilities_after_manual(self, mock_caps):
        mock_caps.return_value = {
            "has_temporal": True,
            "multi_pattern": True,
            "has_trajectory_index": True,
        }
        _register_phase2_tools()
        _register_manual_tools()
        visible = _visible_tool_names()
        assert visible == set(_TOOL_TIERS.keys())

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_no_temporal_hides_temporal_tools(self, mock_caps):
        mock_caps.return_value = {
            "has_temporal": False,
            "multi_pattern": True,
            "has_trajectory_index": False,
        }
        _register_phase2_tools()
        _register_manual_tools()
        visible = _visible_tool_names()
        temporal = {n for n, t in _TOOL_TIERS.items() if t == "temporal"}
        assert temporal.isdisjoint(visible)

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_no_multi_pattern_hides_multi_tools(self, mock_caps):
        mock_caps.return_value = {
            "has_temporal": True,
            "multi_pattern": False,
            "has_trajectory_index": False,
        }
        _register_phase2_tools()
        _register_manual_tools()
        visible = _visible_tool_names()
        mp = {n for n, t in _TOOL_TIERS.items() if t == "multi_pattern"}
        assert mp.isdisjoint(visible)

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_no_trajectory_hides_trajectory_tools(self, mock_caps):
        mock_caps.return_value = {
            "has_temporal": True,
            "multi_pattern": True,
            "has_trajectory_index": False,
        }
        _register_phase2_tools()
        _register_manual_tools()
        visible = _visible_tool_names()
        traj = {n for n, t in _TOOL_TIERS.items() if t == "trajectory_index"}
        assert traj.isdisjoint(visible)

    @patch("hypertopos_mcp.server._detect_capabilities")
    def test_manual_mode_idempotent(self, mock_caps):
        mock_caps.return_value = {
            "has_temporal": True,
            "multi_pattern": True,
            "has_trajectory_index": True,
        }
        _register_phase2_tools()
        _register_manual_tools()
        visible_first = _visible_tool_names()
        _register_manual_tools()  # second call — no-op
        visible_second = _visible_tool_names()
        assert visible_first == visible_second

    def teardown_method(self):
        _restore_all()
        _state["manual_mode"] = False
