# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Per-session tool visibility: the server's profile, filtered by the open sphere's capabilities."""

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
from hypertopos_mcp import server
from hypertopos_mcp.server import (
    _TOOL_TIERS,
    _apply_sphere_capabilities,
    _reset_session_tools,
    _state,
    mcp,
    select_profile,
    selected_profile,
    session_scope,
    visible_tools,
)

_ALL_CAPS = {"has_temporal": True, "multi_pattern": True, "has_trajectory_index": True}


@pytest.fixture(autouse=True)
def _session_isolation():
    """Each test runs in its own client session on the full profile."""
    saved = server._selected
    select_profile("full")
    with session_scope("test:dynamic-tools"):
        yield
        _reset_session_tools()
    server._selected = saved


class TestToolTierMap:
    def test_all_tools_have_tier(self):
        """Every registered tool must appear in _TOOL_TIERS."""
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        missing = registered - set(_TOOL_TIERS)
        assert missing == set(), f"Tools without tier: {missing}"

    def test_tiers_are_capabilities_only(self):
        """Tiers say what a sphere must hold, not when a tool unlocks."""
        assert set(_TOOL_TIERS.values()) == {
            "base",
            "temporal",
            "multi_pattern",
            "trajectory_index",
        }


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


class TestSessionVisibility:
    def test_whole_profile_before_open_sphere(self):
        _reset_session_tools()
        assert visible_tools() == set(selected_profile())

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_open_sphere_with_every_capability_hides_nothing(self, _caps):
        _apply_sphere_capabilities()
        assert visible_tools() == set(_TOOL_TIERS)

    @pytest.mark.parametrize(
        ("missing_cap", "tier"),
        [
            ("has_temporal", "temporal"),
            ("multi_pattern", "multi_pattern"),
            ("has_trajectory_index", "trajectory_index"),
        ],
    )
    def test_open_sphere_hides_tiers_the_sphere_cannot_serve(self, missing_cap, tier):
        caps = {**_ALL_CAPS, missing_cap: False}
        with patch("hypertopos_mcp.server._detect_capabilities", return_value=caps):
            _apply_sphere_capabilities()
        hidden = {n for n, t in _TOOL_TIERS.items() if t == tier}
        assert hidden.isdisjoint(visible_tools())

    @patch("hypertopos_mcp.server._detect_capabilities", return_value=_ALL_CAPS)
    def test_capabilities_resource_reads_the_session(self, _caps):
        import json

        from hypertopos_mcp.server import sphere_capabilities_resource

        assert "error" in json.loads(sphere_capabilities_resource())
        _apply_sphere_capabilities()
        assert json.loads(sphere_capabilities_resource()) == _ALL_CAPS

    def test_capabilities_are_per_session(self):
        caps = {**_ALL_CAPS, "has_temporal": False}
        with patch("hypertopos_mcp.server._detect_capabilities", return_value=caps):
            _apply_sphere_capabilities()
        temporal = {n for n, t in _TOOL_TIERS.items() if t == "temporal"}
        with session_scope("test:other-session"):
            assert temporal <= visible_tools()
        assert temporal.isdisjoint(visible_tools())


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
    def test_tools_list_is_the_profile_in_its_order(self):
        select_profile("investigator")
        listing = {"tools": [{"name": n} for n in reversed(list(_TOOL_TIERS))]}
        out = _middleware("profile-client", "tools/list", None, listing)
        assert [t["name"] for t in out["tools"]] == list(selected_profile())

    def test_call_outside_the_profile_names_the_profile(self):
        select_profile("investigator")
        with pytest.raises(Exception, match="not in this server's tool profile.*--profile full"):
            _middleware("profile-client-2", "tools/call", {"name": "cluster_bridges"}, {})

    def test_profile_tool_call_passes(self):
        select_profile("investigator")
        out = _middleware("profile-client-3", "tools/call", {"name": "find_anomalies"}, {"ok": 1})
        assert out == {"ok": 1}

    def test_unknown_tool_name_is_left_to_the_sdk(self):
        # A typo is not a profile question: the server does not point to --profile full.
        select_profile("investigator")
        out = _middleware("profile-client-4", "tools/call", {"name": "no_such_tool"}, {"sdk": 1})
        assert out == {"sdk": 1}
