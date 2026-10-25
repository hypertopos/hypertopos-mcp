# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Compact vs full response format: env default, session override, per-call knobs."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def fmt_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the session format override and the env default for each test."""
    from hypertopos_mcp.server import _state

    monkeypatch.setitem(_state, "response_format", None)
    monkeypatch.delenv("HYPERTOPOS_MCP_FORMAT", raising=False)


@pytest.fixture
def berka(open_berka_sphere: str, fmt_state: None):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    if _state.get("navigator") is None:
        open_sphere(open_berka_sphere)
    return open_berka_sphere


def test_default_format_is_compact(fmt_state: None) -> None:
    from hypertopos_mcp.server import active_response_format

    assert active_response_format() == "compact"


def test_env_selects_full_and_ignores_unknown_values(
    fmt_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hypertopos_mcp.server import active_response_format

    monkeypatch.setenv("HYPERTOPOS_MCP_FORMAT", "full")
    assert active_response_format() == "full"
    monkeypatch.setenv("HYPERTOPOS_MCP_FORMAT", "weird")
    assert active_response_format() == "compact"


def test_session_override_wins_over_env(fmt_state: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from hypertopos_mcp.server import _state, active_response_format

    monkeypatch.setenv("HYPERTOPOS_MCP_FORMAT", "full")
    _state["response_format"] = "compact"
    assert active_response_format() == "compact"


@pytest.mark.parametrize(
    ("explicit", "session", "expected"),
    [
        (None, "compact", False),
        (None, "full", True),
        (True, "compact", True),
        (False, "full", False),
    ],
)
def test_verbose_inherits_the_session_format(
    fmt_state: None, explicit: bool | None, session: str, expected: bool
) -> None:
    from hypertopos_mcp.server import _state, resolve_verbose

    _state["response_format"] = session
    assert resolve_verbose(explicit) is expected


def test_open_sphere_sets_and_reports_the_session_format(berka: str) -> None:
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import get_session_stats, open_sphere

    out = json.loads(open_sphere(berka, response_format="full"))
    assert out["response_format"] == "full"
    assert _state["response_format"] == "full"
    assert json.loads(get_session_stats())["response_format"] == "full"

    out = json.loads(open_sphere(berka))
    assert out["response_format"] == "compact"


def test_open_sphere_rejects_an_unknown_format(berka: str) -> None:
    from hypertopos_mcp.tools.session import open_sphere

    with pytest.raises(Exception, match="response_format"):
        open_sphere(berka, response_format="tiny")


def test_find_anomalies_compact_drops_positional_deltas_and_numeric_properties(
    berka: str,
) -> None:
    from hypertopos_mcp.tools.navigation import find_anomalies

    out = json.loads(find_anomalies("account_stress_pattern", top_n=5))

    assert out["polygons"]
    for poly in out["polygons"]:
        assert "anomaly_dimensions" in poly and "delta" not in poly
    assert all("delta" not in c for c in out["clusters"])
    assert "delta" in out["omitted"]
    columns = out["properties"]["columns"]
    rows = out["properties"]["rows"]
    for i, name in enumerate(columns[1:], start=1):
        assert all(r[i] is None or isinstance(r[i], str) for r in rows), name
    assert out["omitted"]["properties"]


def test_find_anomalies_omits_an_empty_edge_list(berka: str) -> None:
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.navigation import find_anomalies

    empty = []
    for pattern_id in _state["sphere"]._sphere.patterns:
        for summary in (None, False):
            out = json.loads(find_anomalies(pattern_id, top_n=5, summary=summary))
            empty += [pattern_id for p in out["polygons"] if p.get("edges") == []]
    assert not empty, f"polygons carry edges: [] in {sorted(set(empty))}"


def test_find_anomalies_compact_keeps_the_ranked_property(berka: str) -> None:
    from hypertopos_mcp.tools.navigation import find_anomalies

    out = json.loads(
        find_anomalies("account_stress_pattern", top_n=5, rank_by_property="min_balance")
    )

    assert "min_balance" in out["properties"]["columns"]


def test_find_anomalies_full_keeps_everything_and_labels_deltas(berka: str) -> None:
    from hypertopos_mcp.tools.navigation import find_anomalies

    full = json.loads(find_anomalies("account_stress_pattern", top_n=5, summary=False))

    assert all("delta" in p for p in full["polygons"])
    assert len(full["dim_labels"]) == len(full["polygons"][0]["delta"])
    assert "omitted" not in full


def test_session_full_format_applies_without_per_call_flags(berka: str) -> None:
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.navigation import find_anomalies

    _state["response_format"] = "full"
    out = json.loads(find_anomalies("account_stress_pattern", top_n=5))

    assert all("delta" in p for p in out["polygons"]) and "omitted" not in out


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("find_hubs", ("account_stress_pattern",)),
        ("find_similar_entities", ("741", "account_stress_pattern")),
    ],
)
def test_entity_list_tools_project_properties_in_compact_only(
    berka: str, tool: str, args: tuple[str, ...]
) -> None:
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import analysis

    fn = getattr(analysis, tool)
    compact = json.loads(fn(*args, top_n=5))
    _state["response_format"] = "full"
    full = json.loads(fn(*args, top_n=5))

    assert len(compact["properties"]["columns"]) < len(full["properties"]["columns"])
    assert compact["omitted"]["properties"] and "omitted" not in full


def test_find_clusters_compact_projects_member_properties(berka: str) -> None:
    from hypertopos_mcp.tools.analysis import find_clusters

    compact = json.loads(find_clusters("account_stress_pattern", n_clusters=2, top_n=5))
    full = json.loads(find_clusters("account_stress_pattern", n_clusters=2, top_n=5, summary=False))

    assert len(compact["properties"]["columns"]) < len(full["properties"]["columns"])
    assert set(compact["omitted"]["properties"]) <= set(full["properties"]["columns"])
