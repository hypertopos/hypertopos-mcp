# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP layer fixes: smart planner keying and step tolerance, dimension ranking,
session switch and failed open, local agent temporal scope, capability detection,
edge enrichment collisions, q-value precision."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from hypertopos_mcp import server
from hypertopos_mcp.server import _state


def _fake_sphere(pattern_ids: list[str]) -> MagicMock:
    fake_pattern = MagicMock()
    fake_pattern.pattern_type = "anchor"
    fake_pattern.relations = []
    fake_sphere = MagicMock()
    fake_sphere.patterns = dict.fromkeys(pattern_ids, fake_pattern)
    fake_sphere.entity_line.return_value = "suppliers"
    return fake_sphere


@pytest.fixture
def mock_smart_state(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    nav = MagicMock()
    sphere = MagicMock()
    sphere._sphere = _fake_sphere(["pat_a", "pat_b", "pat_c"])
    for key, value in (
        ("navigator", nav),
        ("sphere", sphere),
        ("explored_steps", None),
        ("investigation_hints", None),
    ):
        monkeypatch.setitem(_state, key, value)
    monkeypatch.setattr(server, "sphere_capabilities", lambda: {})
    return nav


def _run_plan(plan: dict) -> dict:
    import asyncio

    from hypertopos_mcp.tools import smart

    with patch.object(smart, "_fallback_plan", return_value=plan):
        return json.loads(asyncio.run(smart.detect_pattern("anything", ctx=None)))


# --- smart planner: same-name steps -------------------------------------------


def test_same_name_steps_keep_every_result(mock_smart_state: MagicMock) -> None:
    def per_pattern(pattern_id: str, **_: object) -> tuple:
        poly = SimpleNamespace(primary_key=f"{pattern_id}-1", delta_norm=1.0)
        return [poly], {"pat_a": 1, "pat_b": 2, "pat_c": 3}[pattern_id], None, None

    mock_smart_state.π5_attract_anomaly.side_effect = per_pattern
    plan = {
        "steps": [
            {"name": "find_anomalies", "params": {"pattern_id": pid}}
            for pid in ("pat_a", "pat_b", "pat_c")
        ]
    }
    result = _run_plan(plan)
    totals = {k: v["total_anomalies"] for k, v in result["results"].items()}
    assert totals == {
        "find_anomalies[pat_a]": 1,
        "find_anomalies[pat_b]": 2,
        "find_anomalies[pat_c]": 3,
    }
    explored = set(result["coverage"]["explored"])
    assert {"pat_a/find_anomalies", "pat_b/find_anomalies", "pat_c/find_anomalies"} <= explored


def test_unique_step_name_keeps_plain_key(mock_smart_state: MagicMock) -> None:
    mock_smart_state.π5_attract_anomaly.return_value = ([], 0, None, None)
    result = _run_plan({"steps": [{"name": "find_anomalies", "params": {"pattern_id": "pat_a"}}]})
    assert list(result["results"]) == ["find_anomalies"]


# --- smart planner: steps without params / bare strings -------------------------


@pytest.mark.parametrize(
    "steps",
    [
        [{"name": "find_anomalies"}],
        ["find_anomalies"],
        [{"name": "find_anomalies", "params": None}],
    ],
)
def test_steps_without_params_return_results(mock_smart_state: MagicMock, steps: list) -> None:
    mock_smart_state.π5_attract_anomaly.return_value = ([], 0, None, None)
    result = _run_plan({"steps": steps})
    assert result["results"]["find_anomalies"]["total_anomalies"] == 0
    assert result["zero_results_hint"] is not None


# --- smart planner: dimension-aware ranking -------------------------------------


def test_step_find_anomalies_passes_rank_by_property(mock_smart_state: MagicMock) -> None:
    from hypertopos_mcp.tools.smart import _step_find_anomalies

    mock_smart_state.π5_attract_anomaly.return_value = ([], 0, None, None)
    _step_find_anomalies(
        {"pattern_id": "pat_a", "top_n": 5, "rank_by_property": "tx_count", "rank_order": "asc"}
    )
    mock_smart_state.π5_attract_anomaly.assert_called_once_with(
        "pat_a", top_n=5, rank_by_property="tx_count", rank_order="asc"
    )


def test_fallback_low_value_query_ranks_ascending(open_berka_sphere: str) -> None:
    from hypertopos_mcp.tools.smart import _available_steps, _fallback_plan

    sphere = _state["sphere"]._sphere
    patterns_info = {
        pid: {
            "type": p.pattern_type,
            "entity_line": sphere.entity_line(pid),
            "dimensions": [r.line_id.replace("_d_", "").replace("_", " ") for r in p.relations],
            "dimension_ids": [r.line_id for r in p.relations],
        }
        for pid, p in sphere.patterns.items()
    }
    low = _fallback_plan(
        "accounts with the lowest balance volatility", _available_steps(), patterns_info
    )
    high = _fallback_plan(
        "accounts with anomalous balance volatility", _available_steps(), patterns_info
    )
    low_ranked = [s for s in low["steps"] if "rank_by_property" in s["params"]]
    high_ranked = [s for s in high["steps"] if "rank_by_property" in s["params"]]
    assert low_ranked
    assert high_ranked
    assert all(s["params"].get("rank_order") == "asc" for s in low_ranked)
    assert all(s["params"].get("rank_order", "desc") == "desc" for s in high_ranked)


def test_fallback_dimension_ranking_uses_entity_column(open_berka_sphere: str) -> None:
    from hypertopos_mcp.tools.smart import _available_steps, _fallback_plan

    sphere = _state["sphere"]._sphere
    patterns_info = {
        pid: {
            "type": p.pattern_type,
            "entity_line": sphere.entity_line(pid),
            "dimensions": [r.line_id.replace("_d_", "").replace("_", " ") for r in p.relations],
            "dimension_ids": [r.line_id for r in p.relations],
        }
        for pid, p in sphere.patterns.items()
    }
    plan = _fallback_plan(
        "accounts with anomalous balance volatility", _available_steps(), patterns_info
    )
    ranked = [s for s in plan["steps"] if "rank_by_property" in s["params"]]
    assert ranked
    assert {s["params"]["rank_by_property"] for s in ranked} == {"balance_volatility"}

    from hypertopos_mcp.tools.smart import _step_find_anomalies

    out = _step_find_anomalies(ranked[0]["params"])
    assert "error" not in out


# --- session switch / failed open ------------------------------------------------


def test_open_clears_investigation_hints(reopening_berka_sphere: str) -> None:
    _state["investigation_hints"] = ["hint from another sphere"]
    server._do_open_sphere(reopening_berka_sphere)
    assert _state.get("investigation_hints") is None


def test_failed_open_keeps_previous_session(reopening_berka_sphere: str) -> None:
    from hypertopos_mcp.tools.session import open_sphere

    open_sphere(reopening_berka_sphere)
    _state["phase"] = 3
    _state["manual_mode"] = True
    old_session = _state["session"]
    with pytest.raises(Exception, match="does_not_exist"):
        open_sphere("does_not_exist")
    assert _state["session"] is old_session
    assert old_session._manifest.status != "expired"
    assert _state["phase"] == 3
    assert _state["manual_mode"] is True


# --- local agent temporal scope ---------------------------------------------------


def test_local_open_purges_only_own_agent(
    reopening_berka_sphere: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hypertopos.storage.writer import GDSWriter

    purged: list[str] = []
    monkeypatch.setattr(GDSWriter, "purge_agent_temporal", lambda self, a: purged.append(a) or 0)
    monkeypatch.setattr(
        GDSWriter, "purge_all_agents", lambda self: pytest.fail("purged every agent")
    )
    server._do_open_sphere(reopening_berka_sphere)
    expected = f"mcp-{os.getpid()}"
    assert purged == [expected]
    assert _state["session"]._manifest.agent_id == expected


# --- capability detection ----------------------------------------------------------


def test_empty_temporal_dir_is_not_temporal(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "temporal" / "pat_a").mkdir(parents=True)
    sphere = MagicMock()
    sphere._sphere = _fake_sphere(["pat_a"])
    monkeypatch.setitem(_state, "sphere", sphere)
    monkeypatch.setitem(_state, "path", str(tmp_path))
    monkeypatch.setitem(_state, "navigator", None)
    assert server._detect_capabilities()["has_temporal"] is False
    (tmp_path / "temporal" / "pat_a" / "data.lance").mkdir()
    assert server._detect_capabilities()["has_temporal"] is True


# --- edge enrichment ---------------------------------------------------------------


def test_enrichment_keeps_structural_edge_fields() -> None:
    from hypertopos_mcp._format import tabulate_edge_properties
    from hypertopos_mcp.enrichment import enrich_polygon

    edge = {
        "line_id": "transfers",
        "point_key": "t1",
        "status": "alive",
        "direction": "in",
        "is_jumpable": True,
    }
    poly = {"primary_key": "a1", "edges": [edge]}
    lookups = {"transfers": {"t1": {"direction": "debit", "is_jumpable": "n", "amount": 5}}}
    enrich_polygon(poly, lookups)
    assert edge["direction"] == "in"
    assert edge["is_jumpable"] is True
    related = tabulate_edge_properties([poly])
    table = related["transfers"]
    row = dict(zip(table["columns"], table["rows"][0], strict=True))
    assert row == {
        "point_key": "t1",
        "prop_direction": "debit",
        "prop_is_jumpable": "n",
        "amount": 5,
    }


# --- p-value precision ---------------------------------------------------------------


def test_small_p_and_q_values_survive_serialization() -> None:
    from hypertopos_mcp.serializers import _serialize_polygon

    poly = SimpleNamespace(
        primary_key="a1",
        pattern_id="p",
        delta=np.array([0.5], dtype=np.float32),
        delta_norm=0.5,
        score_rank_pct=None,
        is_anomaly=True,
        edges=[],
        alive_edges=lambda: [],
        conformal_p=3e-9,
        cell_q_spatial=4e-8,
        cell_q_temporal=5e-8,
    )
    out = server._compact_numbers(json.loads(json.dumps(_serialize_polygon(poly))))
    assert out["conformal_p"] == pytest.approx(3e-9)
    assert out["cell_q_spatial"] == pytest.approx(4e-8)
    assert out["cell_q_temporal"] == pytest.approx(5e-8)


def test_attract_boundary_reports_score_rank_and_no_q_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hypertopos_mcp.tools.navigation import attract_boundary

    nav = MagicMock()
    poly = SimpleNamespace(
        primary_key="a1",
        delta_norm=0.5,
        score_rank_pct=42.0,
        is_anomaly=False,
        representativeness=None,
    )
    nav.π6_attract_boundary.return_value = [(poly, 0.1)]
    monkeypatch.setitem(_state, "navigator", nav)
    out = json.loads(attract_boundary("alias", "pat_a"))
    entry = out["results"][0]
    assert entry["score_rank_pct"] == 42.0
    assert "q_value" not in entry
    assert set(nav.π6_attract_boundary.call_args.kwargs) == {"direction", "top_n", "select"}


def test_keyword_plan_with_three_same_name_motif_steps_keeps_every_result(
    mock_smart_state: MagicMock,
) -> None:
    # The keyword fallback (no LLM sampling) plans one motif step per matched
    # pattern; one pattern failing must not overwrite the others' results.
    import asyncio

    from hypertopos_mcp.tools import smart

    def per_pattern(pattern_id: str, **_: object) -> list[dict]:
        if pattern_id == "pat_c":
            raise ValueError("no edge table for pat_c")
        return [{"primary_key": f"{pattern_id}-1", "score": 1.0}]

    mock_smart_state.find_high_potential_motifs.side_effect = per_pattern
    out = json.loads(asyncio.run(smart.detect_pattern("structuring in every pattern", ctx=None)))

    motif_keys = {k: v for k, v in out["results"].items() if k.startswith("find_high_potential")}
    assert set(motif_keys) == {
        "find_high_potential_motifs[pat_a]",
        "find_high_potential_motifs[pat_b]",
        "find_high_potential_motifs[pat_c]",
    }
    assert motif_keys["find_high_potential_motifs[pat_a]"]["results"][0]["primary_key"] == "pat_a-1"
    assert motif_keys["find_high_potential_motifs[pat_b]"]["results"][0]["primary_key"] == "pat_b-1"
    assert "pat_c" in motif_keys["find_high_potential_motifs[pat_c]"]["error"]


def test_follow_up_names_a_dimension_once_per_pattern(mock_smart_state: MagicMock) -> None:
    # Sender and receiver relations to the same line share one dimension name.
    import asyncio

    from hypertopos_mcp.tools import smart

    pattern = _state["sphere"]._sphere.patterns["pat_a"]
    pattern.relations = [SimpleNamespace(line_id="accounts"), SimpleNamespace(line_id="accounts")]
    mock_smart_state.π5_attract_anomaly.return_value = ([], 0, None, None)

    out = json.loads(asyncio.run(smart.detect_pattern("something weird maybe", ctx=None)))

    [dims_hint] = [f for f in out["follow_up"] if f.startswith("For dimension-specific")]
    assert dims_hint.count("'accounts' in pat_a") == 1


def test_open_sphere_pattern_summary_counts_entities_and_labels_roles() -> None:
    from hypertopos_mcp.tools.session import _pattern_summary

    def rel(line_id: str, role: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(line_id=line_id, role=role, display_name=None)

    pattern = SimpleNamespace(
        pattern_type="event",
        population_size=6924049,
        relations=[rel("accounts", "sender"), rel("accounts", "receiver"), rel("currencies")],
        dim_block_names=[],
    )
    summary = _pattern_summary(SimpleNamespace(patterns={"tx_pattern": pattern}))
    assert summary["tx_pattern"] == {
        "type": "event",
        "entities": "6,924,049",
        "dimensions": ["sender", "receiver", "currencies"],
    }


def test_open_sphere_pattern_summary_lists_dimension_block_columns() -> None:
    # A pattern made of metric / geo blocks has no relations; its dimensions
    # must still show, or the agent sees a pattern with nothing to navigate.
    from hypertopos_mcp.tools.session import _pattern_summary

    pattern = SimpleNamespace(
        pattern_type="anchor",
        population_size=2000,
        relations=[SimpleNamespace(line_id="_d_n_out", role=None, display_name=None)],
        dim_block_names=["t:amount_sum_out", "g:lat"],
    )
    summary = _pattern_summary(SimpleNamespace(patterns={"entity_pattern": pattern}))
    assert summary["entity_pattern"]["dimensions"] == ["n out", "amount sum out", "lat"]
