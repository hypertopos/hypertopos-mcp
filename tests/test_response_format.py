# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Response and schema format applied to every MCP tool."""

from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.10000000149011612, 0.1),  # float32 artifact
        (-1.7000000476837158, -1.7),
        (3227472424.5, 3227472424.5),  # large amounts keep every integer digit and cents
        (6610663.1, 6610663.1),
        (3.054207159649215e-84, 3.054207e-84),  # tiny p-values keep their magnitude
        (0.0, 0.0),
    ],
)
def test_floats_keep_their_meaningful_digits(value: float, expected: float) -> None:
    from hypertopos_mcp.server import _record_timing

    out = json.loads(_record_timing("t", 1.0, json.dumps({"x": value})))
    assert out["x"] == expected


def test_integers_and_booleans_are_untouched() -> None:
    from hypertopos_mcp.server import _record_timing

    out = json.loads(_record_timing("t", 1.0, json.dumps({"n": 1056320, "b": True, "k": "A1"})))
    assert out == {"n": 1056320, "b": True, "k": "A1", "elapsed_ms": 1.0}


def test_non_ascii_text_is_sent_as_is() -> None:
    from hypertopos_mcp.server import _record_timing

    out = _record_timing("t", 1.0, json.dumps({"hint": "theta — boundary"}))
    assert "—" in out and "\\u2014" not in out


def test_nested_lists_are_rounded() -> None:
    from hypertopos_mcp.server import _record_timing

    out = json.loads(_record_timing("t", 1.0, json.dumps({"delta": [[0.30000001192092896]]})))
    assert out["delta"] == [[0.3]]


def test_tool_schemas_carry_no_title_keywords() -> None:
    import hypertopos_mcp.main  # noqa: F401
    from hypertopos_mcp.server import mcp

    def titles(node: object) -> int:
        if isinstance(node, dict):
            own = 1 if isinstance(node.get("title"), str) else 0
            return own + sum(titles(v) for v in node.values())
        if isinstance(node, list):
            return sum(titles(v) for v in node)
        return 0

    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    assert sum(titles(t.parameters) for t in tools.values()) == 0
    assert all(t.parameters.get("properties") is not None for t in tools.values())


def test_tabulate_properties_moves_item_properties_into_one_key_first_table() -> None:
    from hypertopos_mcp._format import tabulate_properties

    items = [
        {"primary_key": "A", "score": 1, "properties": {"name": "x", "city": "P"}},
        {"primary_key": "B", "score": 2},
        {"primary_key": "C", "score": 3, "properties": {"city": "Q", "zip": 10}},
    ]

    table = tabulate_properties(items, "primary_key")

    assert table == {
        "columns": ["primary_key", "name", "city", "zip"],
        "rows": [["A", "x", "P", None], ["C", None, "Q", 10]],
    }
    assert all("properties" not in item for item in items)


def test_sphere_overview_hoists_warning_advice_and_drops_alert_pattern_id(
    open_berka_sphere: str,
) -> None:
    from hypertopos_mcp.tools.session import open_sphere

    open_sphere(open_berka_sphere)
    from hypertopos_mcp.tools.observability import sphere_overview

    result = json.loads(sphere_overview())
    warnings = [w for p in result["patterns"] for w in p.get("dim_quality_warnings", [])]
    alerts = [a for p in result["patterns"] for a in p.get("profiling_alerts", [])]
    assert warnings and alerts, "fixture sphere should raise warnings and alerts"
    advice = result["advice_by_warning_type"]
    assert all("advice" not in w and w["type"] in advice for w in warnings)
    assert all("pattern_id" not in a for a in alerts)


def test_dedupe_text_replaces_repeated_values_with_refs() -> None:
    from hypertopos_mcp._format import dedupe_text

    items = [
        {"k": 1, "interpretation": "same"},
        {"k": 2, "interpretation": "unique"},
        {"k": 3, "interpretation": "same"},
        {"k": 4},
    ]

    texts = dedupe_text(items, "interpretation")

    assert texts == {"t1": "same"}
    assert items == [
        {"k": 1, "interpretation_ref": "t1"},
        {"k": 2, "interpretation": "unique"},
        {"k": 3, "interpretation_ref": "t1"},
        {"k": 4},
    ]


def test_slim_polygons_drops_type_and_parent_pattern_id() -> None:
    from hypertopos_mcp._format import slim_polygons

    polys = [
        {"type": "Polygon", "primary_key": "A", "pattern_id": "p"},
        {"type": "Polygon", "primary_key": "B", "pattern_id": "q"},
    ]

    slim_polygons(polys, "p")

    assert polys == [{"primary_key": "A"}, {"primary_key": "B", "pattern_id": "q"}]


def test_tabulate_edge_properties_moves_endpoint_properties_into_one_table_per_line() -> None:
    from hypertopos_mcp._format import tabulate_edge_properties

    def edge(line: str, key: str, **props: object) -> dict[str, object]:
        return {
            "line_id": line,
            "point_key": key,
            "status": "alive",
            "direction": "in",
            "is_jumpable": True,
            **props,
        }

    polys = [
        {"primary_key": "T1", "edges": [edge("accounts", "A1", region="N"), edge("ops", "VKLAD")]},
        {"primary_key": "T2", "edges": [edge("accounts", "A1", region="N")]},
        {"primary_key": "T3", "edges": [edge("accounts", "A2", region="S", zip=1)]},
    ]

    related = tabulate_edge_properties(polys)

    assert related == {
        "accounts": {
            "columns": ["point_key", "region", "zip"],
            "rows": [["A1", "N", None], ["A2", "S", 1]],
        }
    }
    assert polys[0]["edges"][0] == {
        "line_id": "accounts",
        "point_key": "A1",
        "status": "alive",
        "direction": "in",
        "is_jumpable": True,
    }


def test_tabulate_properties_returns_none_without_properties() -> None:
    from hypertopos_mcp._format import tabulate_properties

    assert tabulate_properties([{"primary_key": "A"}], "primary_key") is None


def test_hoist_repeated_text_keeps_one_copy_per_distinct_value() -> None:
    from hypertopos_mcp._format import hoist_repeated_text

    items = [
        {"type": "a", "advice": "do x"},
        {"type": "b", "advice": "do y"},
        {"type": "a", "advice": "do x"},
        {"type": "c"},
    ]

    hoisted = hoist_repeated_text(items, "type", "advice")

    assert hoisted == {"a": "do x", "b": "do y"}
    assert items == [{"type": "a"}, {"type": "b"}, {"type": "a"}, {"type": "c"}]


def test_hoist_repeated_text_keeps_text_inline_when_a_key_has_two_values() -> None:
    from hypertopos_mcp._format import hoist_repeated_text

    items = [{"type": "a", "advice": "do x"}, {"type": "a", "advice": "do z"}]

    assert hoist_repeated_text(items, "type", "advice") == {}
    assert items == [{"type": "a", "advice": "do x"}, {"type": "a", "advice": "do z"}]
