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
