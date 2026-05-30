# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for server-side tool call counting."""

from __future__ import annotations

import json
import sys

import pytest

sys.path.insert(0, "packages/hypertopos-mcp/src")


@pytest.fixture(autouse=True)
def _reset():
    """Reset call stats before each test."""
    from hypertopos_mcp.server import _reset_call_stats

    _reset_call_stats()
    yield
    _reset_call_stats()


def test_timed_increments_counter():
    from hypertopos_mcp.server import _call_stats, timed

    @timed
    def dummy():
        return json.dumps({"ok": True})

    dummy()
    assert _call_stats["call_count"] == 1


def test_timed_accumulates_multiple_calls():
    from hypertopos_mcp.server import _call_stats, timed

    @timed
    def dummy():
        return json.dumps({"ok": True})

    for _ in range(5):
        dummy()
    assert _call_stats["call_count"] == 5


def test_timed_tracks_per_tool():
    from hypertopos_mcp.server import _call_stats, timed

    @timed
    def tool_a():
        return json.dumps({"a": 1})

    @timed
    def tool_b():
        return json.dumps({"b": 2})

    tool_a()
    tool_a()
    tool_b()
    assert _call_stats["per_tool"]["tool_a"]["count"] == 2
    assert _call_stats["per_tool"]["tool_b"]["count"] == 1


def test_reset_clears_stats():
    from hypertopos_mcp.server import _call_stats, _reset_call_stats, timed

    @timed
    def dummy():
        return json.dumps({"ok": True})

    dummy()
    dummy()
    assert _call_stats["call_count"] == 2
    _reset_call_stats()
    assert _call_stats["call_count"] == 0
    assert _call_stats["per_tool"] == {}
    assert _call_stats["session_start"] is not None


def test_timed_still_injects_elapsed_ms():
    from hypertopos_mcp.server import timed

    @timed
    def dummy():
        return json.dumps({"ok": True})

    result = json.loads(dummy())
    assert "elapsed_ms" in result
    assert isinstance(result["elapsed_ms"], float)


def test_timed_handles_non_json_response():
    from hypertopos_mcp.server import _call_stats, timed

    @timed
    def dummy():
        return "not json"

    result = dummy()
    assert result == "not json"
    assert _call_stats["call_count"] == 1


def test_session_stats_omits_cache_block_without_session():
    """No open session → no points_handle_cache block."""
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import _build_session_stats

    prev = _state.get("session")
    _state["session"] = None
    try:
        stats = _build_session_stats()
        assert "points_handle_cache" not in stats
    finally:
        _state["session"] = prev


def test_session_stats_surfaces_points_handle_cache(monkeypatch):
    """With an open session, _build_session_stats forwards the reader's
    points-handle cache hit/miss counters verbatim."""
    from types import SimpleNamespace

    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import _build_session_stats

    fake_reader = SimpleNamespace(
        points_cache_stats=lambda: {
            "points_handle_cache_hits": 7,
            "points_handle_cache_misses": 2,
        }
    )
    fake_session = SimpleNamespace(_reader=fake_reader)

    prev = _state.get("session")
    _state["session"] = fake_session
    try:
        stats = _build_session_stats()
        assert stats["points_handle_cache"] == {
            "points_handle_cache_hits": 7,
            "points_handle_cache_misses": 2,
        }
    finally:
        _state["session"] = prev
