# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP tools read the session snapshot.

recalibrate refreshes it; get_session_stats reports staleness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def writable_sphere(writable_berka_sphere) -> str:
    return writable_berka_sphere


def _anchor_pattern(state) -> str:
    sphere = state["sphere"]._sphere
    return next(pid for pid, p in sphere.patterns.items() if p.pattern_type == "anchor")


def _set_population_size(path: str, pid: str, value: int) -> None:
    from hypertopos.storage.writer import write_sphere_json

    meta = Path(path) / "_gds_meta"
    data = json.loads((meta / "sphere.json").read_text())
    data["patterns"][pid]["population_size"] = value
    write_sphere_json(meta, data)


def test_mcp_recalibrate_refreshes_open_time_sphere(writable_sphere, monkeypatch):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import session as session_tools

    session_tools.open_sphere(writable_sphere)
    pid = _anchor_pattern(_state)
    _set_population_size(writable_sphere, pid, 1)
    session_tools.open_sphere(writable_sphere)
    assert _state["sphere"]._sphere.patterns[pid].population_size == 1

    result = json.loads(session_tools.recalibrate(pid))

    n = result["records_recalibrated"]
    assert n != 1
    assert _state["sphere"]._sphere.patterns[pid].population_size == n
    assert _state["session"].sphere().patterns[pid].population_size == n

    # Without the refresh call the open-time sphere keeps the old value.
    monkeypatch.setattr(session_tools, "_refresh_state", lambda: None)
    _set_population_size(writable_sphere, pid, 1)
    session_tools.open_sphere(writable_sphere)
    session_tools.recalibrate(pid)
    assert _state["sphere"]._sphere.patterns[pid].population_size == 1


def test_get_session_stats_reports_stale_snapshot(writable_sphere):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import session as session_tools

    session_tools.open_sphere(writable_sphere)
    snapshot = json.loads(session_tools.get_session_stats())["snapshot"]
    assert snapshot["stale"] is False
    assert snapshot["hint"] is None

    pid = _anchor_pattern(_state)
    _set_population_size(writable_sphere, pid, 7)
    snapshot = json.loads(session_tools.get_session_stats())["snapshot"]
    assert snapshot["stale"] is True
    assert snapshot["metadata_changed"] is True
    assert "open_sphere" in snapshot["hint"]

    session_tools.open_sphere(writable_sphere)
    assert json.loads(session_tools.get_session_stats())["snapshot"]["stale"] is False
