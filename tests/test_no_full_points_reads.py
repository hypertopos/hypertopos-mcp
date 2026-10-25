# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Schema, row counts and pattern discovery never read whole points tables."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def no_full_points_reads(open_berka_sphere, monkeypatch):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    if _state.get("session") is None:
        open_sphere(open_berka_sphere)
    reader = _state["session"]._reader
    expected = {
        lid: reader.count_points_rows(lid, line.versions[-1])
        for lid, line in _state["sphere"]._sphere.lines.items()
    }

    def refuse(*_args, **_kwargs):
        raise AssertionError("read_points must not be called here")

    monkeypatch.setattr(reader, "read_points", refuse)
    return expected


def test_line_schema_and_sphere_info_counts_without_reading_points(no_full_points_reads) -> None:
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import get_line_schema

    line_id = next(lid for lid in no_full_points_reads if not lid.startswith("_d_"))
    out = json.loads(get_line_schema(line_id))
    assert out["total_rows"] == no_full_points_reads[line_id]
    assert out["columns"]

    from hypertopos_mcp.tools.session import _get_line_row_count

    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    assert (
        _get_line_row_count(reader, line_id, sphere.lines[line_id].versions[-1])
        == (no_full_points_reads[line_id])
    )
    assert _get_line_row_count(reader, "no-such-line", 1) is None


def test_pattern_discovery_reads_only_schemas(no_full_points_reads) -> None:
    from hypertopos_mcp.server import _state

    nav = _state["navigator"]
    lines = [lid for lid in _state["sphere"]._sphere.lines if not lid.startswith("_d_")]
    found = [nav._discover_pattern_map(lid) for lid in lines]
    assert any(found)
