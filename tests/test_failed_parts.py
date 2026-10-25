# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""A response part that fails to load is named in ``failed_parts``, not dropped silently."""

from __future__ import annotations

import json

import pytest


def test_recorded_failures_are_attached_once_per_part() -> None:
    from hypertopos_mcp._format import record_failure
    from hypertopos_mcp.server import timed

    @timed
    def tool() -> str:
        record_failure("properties of line accounts", OSError("disk gone"))
        record_failure("properties of line accounts", OSError("disk gone"))
        return json.dumps({"ok": True})

    data = json.loads(tool())
    assert data["failed_parts"] == [
        {"part": "properties of line accounts", "error": "OSError: disk gone"}
    ]


def test_a_tool_that_reloads_the_format_module_still_returns() -> None:
    # open_sphere(force_reload=True) reloads hypertopos_mcp._format mid-call;
    # the timed wrapper must still close the collection it opened.
    import importlib

    import hypertopos_mcp._format as fmt
    from hypertopos_mcp.server import timed

    @timed
    def tool() -> str:
        importlib.reload(fmt)
        fmt.record_failure("after reload", OSError("x"))
        return json.dumps({"ok": True})

    data = json.loads(tool())
    assert data["ok"] is True
    assert data["failed_parts"] == [{"part": "after reload", "error": "OSError: x"}]


def test_no_failures_means_no_key() -> None:
    from hypertopos_mcp.server import timed

    @timed
    def tool() -> str:
        return json.dumps({"ok": True})

    assert "failed_parts" not in json.loads(tool())


def test_failures_do_not_leak_into_the_next_call() -> None:
    from hypertopos_mcp._format import record_failure
    from hypertopos_mcp.server import timed

    @timed
    def failing() -> str:
        record_failure("temporal_hint", ValueError("bad"))
        return json.dumps({})

    @timed
    def clean() -> str:
        return json.dumps({})

    failing()
    assert "failed_parts" not in json.loads(clean())


def test_record_failure_outside_a_tool_call_is_a_no_op() -> None:
    from hypertopos_mcp._format import record_failure

    record_failure("temporal_hint", ValueError("bad"))


def test_get_polygon_names_failed_properties_and_temporal_hint(
    open_berka_sphere: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hypertopos.navigation.navigator import GDSNavigator
    from hypertopos.storage.reader import GDSReader

    from hypertopos_mcp.tools.geometry import get_polygon
    from hypertopos_mcp.tools.navigation import goto

    goto("1", "accounts")

    def broken_batch(*_args: object, **_kwargs: object) -> None:
        raise OSError("points unreadable")

    def broken_hint(*_args: object, **_kwargs: object) -> None:
        raise ValueError("temporal unreadable")

    monkeypatch.setattr(GDSReader, "read_points_batch", broken_batch)
    monkeypatch.setattr(GDSNavigator, "temporal_hint", broken_hint)
    result = json.loads(get_polygon("account_behavior_pattern"))

    assert "error" not in result, result
    parts = {f["part"]: f["error"] for f in result["failed_parts"]}
    assert parts["temporal_hint"] == "ValueError: temporal unreadable"
    assert any(
        p.startswith("properties of line ") and e == "OSError: points unreadable"
        for p, e in parts.items()
    ), parts


def test_dead_dimensions_failure_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    from hypertopos_mcp.server import _state, timed
    from hypertopos_mcp.tools._guards import dead_dim_indices

    class BrokenNavigator:
        def dead_dim_indices(self, _pattern_id: str) -> list[int]:
            raise KeyError("no such pattern")

    monkeypatch.setitem(_state, "navigator", BrokenNavigator())

    @timed
    def tool() -> str:
        return json.dumps({"dead_dimensions": dead_dim_indices("p")})

    data = json.loads(tool())
    assert data["dead_dimensions"] == []
    assert data["failed_parts"] == [
        {"part": "dead_dimensions", "error": "KeyError: 'no such pattern'"}
    ]
