"""Token-lean and labelled response shapes found by the live MCP smoke test.

Berka-fixture tests drive the real tools; mock tests cover tools that need an
edge table the fixture does not have.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import hypertopos_mcp.tools.analysis as analysis
import hypertopos_mcp.tools.geometry as geometry
import hypertopos_mcp.tools.navigation as navigation
import hypertopos_mcp.tools.session as session_tools
from hypertopos_mcp._format import compact_polygon, labelled
from hypertopos_mcp.server import _state

_A = "account_behavior_pattern"
_ANOMALOUS = "85"  # top anomaly of the fixture's account pattern


@pytest.fixture
def compact(open_berka_sphere, monkeypatch):
    monkeypatch.setitem(_state, "response_format", "compact")
    monkeypatch.delenv("HYPERTOPOS_MCP_FORMAT", raising=False)
    return _state["sphere"]._sphere


@pytest.fixture
def fake_nav():
    nav = MagicMock()
    saved = {k: _state.get(k) for k in ("navigator", "sphere", "session")}
    _state["navigator"] = nav
    _state["sphere"] = MagicMock()
    _state["session"] = MagicMock()
    yield nav
    _state.update(saved)


# ---------------------------------------------------------------- helpers


def test_labelled_refuses_duplicate_labels() -> None:
    # Two relations to the same line share a label: a map would collapse them.
    assert labelled([1.0, 2.0], ["accounts", "accounts"]) is None
    assert labelled([1.0], ["a", "b"]) is None
    assert labelled([1.0, 2.0], ["a", "b"]) == {"a": 1.0, "b": 2.0}


def test_compact_polygon_keeps_positional_delta_for_duplicate_labels() -> None:
    item = {"type": "Polygon", "pattern_id": "p", "delta": [1.0, 2.0], "properties": {"n": 3}}
    omitted = compact_polygon(item, ["accounts", "accounts"], "p")
    assert item["delta"] == [1.0, 2.0]
    assert item["dim_labels"] == ["accounts", "accounts"]  # positions stay named
    assert "delta_by_dim" not in item
    assert omitted == {"properties": ["n"]}


# ---------------------------------------------------------------- geometry tools


def test_get_polygon_compact_is_labelled_and_text_only(compact) -> None:
    navigation.goto("1", "accounts")
    out = json.loads(geometry.get_polygon(_A))
    labels = compact.patterns[_A].dim_labels
    assert "delta" not in out
    assert list(out["delta_by_dim"]) == labels
    assert all(isinstance(v, str) for v in out["properties"].values())
    assert "tx_count" in out["omitted"]["properties"]
    assert "type" not in out and "pattern_id" not in out


def test_get_polygon_event_tabulates_endpoint_properties(compact) -> None:
    events = json.loads(analysis_event_polygons())
    tx_key = events["polygons"][0]["primary_key"]
    navigation.goto(tx_key, "transactions")
    out = json.loads(geometry.get_polygon("tx_pattern"))
    for edge in out["edges"]:
        assert set(edge) <= {"line_id", "point_key", "status", "direction", "is_jumpable"}
    assert "accounts" in out["related_properties"]
    assert "amount" in out["properties"]  # an event's own amounts are its data


def analysis_event_polygons(**kw) -> str:
    return geometry.get_event_polygons(primary_key="1", pattern_id="tx_pattern", top_n=5, **kw)


def test_get_event_polygons_compact_drops_positional_delta_of_anomalies(compact) -> None:
    out = json.loads(analysis_event_polygons(geometry_filters={"is_anomaly": True}))
    assert out["polygons"]
    for poly in out["polygons"]:
        assert "delta" not in poly
        assert poly["anomaly_dimensions"]


def test_anomaly_summary_compact_clusters_have_no_positional_delta(compact) -> None:
    out = json.loads(navigation.anomaly_summary(_A, max_clusters=3))
    assert out["clusters"]
    assert all("delta" not in c and "label" in c for c in out["clusters"])


@pytest.mark.parametrize("tool", ["get_solid", "dive_solid"])
def test_solid_compact_shape(compact, tool) -> None:
    navigation.goto("1", "accounts")
    if tool == "get_solid":
        out = json.loads(geometry.get_solid(_A))
    else:
        out = json.loads(navigation.dive_solid("1", _A))
    base = out["base_polygon"]
    assert "edges" not in base and base["dim_edges"]
    assert "delta" not in base and base["delta_by_dim"]
    assert all(isinstance(v, str) for v in base.get("properties", {}).values())
    assert out["slices"]
    width = len(out["slices"][0]["delta_snapshot"])
    assert out["slice_dim_labels"] == compact.patterns[_A].dim_labels[:width]
    assert all(
        "changed_property" not in s for s in out["slices"] if s.get("changed_property") is None
    )


def test_walk_line_from_a_solid_says_how_to_continue(compact) -> None:
    navigation.dive_solid("1", _A)
    with pytest.raises(RuntimeError, match=r"goto\("):
        navigation.walk_line("accounts")


# ---------------------------------------------------------------- anomaly tools


def test_find_anomalies_default_ranks_a_calibrated_pattern_by_its_score(compact) -> None:
    assert compact.patterns[_A].score_dims
    out = json.loads(navigation.find_anomalies(_A, top_n=3))
    assert out["ranked_by"] == "anomaly_score"
    assert "radius" not in out
    for poly in out["polygons"]:
        assert "score_rank_pct" in poly
        assert "conformal_p" in poly
        for key in ("delta_rank_pct", "q_value", "min_q_per_dim", "q_values_per_dim"):
            assert key not in poly


def test_find_anomalies_and_attract_boundary_drop_entity_fdr_parameters() -> None:
    import inspect

    fa = inspect.signature(navigation.find_anomalies).parameters
    for removed in ("p_value_method", "fdr_axis"):
        assert removed not in fa
    assert fa["radius"].default is None
    assert fa["rank_by"].default is None
    ab = inspect.signature(navigation.attract_boundary).parameters
    for removed in ("fdr_alpha", "fdr_method", "p_value_method"):
        assert removed not in ab


def test_explain_anomaly_drops_zero_share_dimensions(compact) -> None:
    out = json.loads(analysis.explain_anomaly(_ANOMALOUS, _A))
    assert out["top_dimensions"]
    assert all(d["pct_of_total"] > 0 for d in out["top_dimensions"])


def test_investigate_entity_does_not_repeat_explain_fields(compact) -> None:
    out = json.loads(
        analysis.investigate_entity(_ANOMALOUS, _A, "accounts", include_root_cause=False)
    )
    explain = out["explain_anomaly"]
    assert "reliability_flags" not in explain
    assert "primary_key" not in explain and "pattern_id" not in explain
    assert all(d["pct_of_total"] > 0 for d in explain["top_dimensions"])
    assert out["reliability_flags"]


# ---------------------------------------------------------------- entity tables


def _assert_one_properties_table(out: dict, list_field: str) -> None:
    assert all("properties" not in e for e in out[list_field])
    table = out["properties"]
    assert table["columns"][0] == "primary_key"
    assert [r[0] for r in table["rows"]] == [e["primary_key"] for e in out[list_field]]


def test_search_entities_returns_one_properties_table(compact) -> None:
    out = json.loads(session_tools.search_entities("accounts", "frequency", "POPLATEK MESICNE", 3))
    _assert_one_properties_table(out, "entities")


def test_search_entities_hybrid_returns_one_properties_table(compact) -> None:
    out = json.loads(analysis.search_entities_hybrid("1", _A, "POPLATEK", top_n=3))
    _assert_one_properties_table(out, "results")


def test_search_entities_fts_drops_bookkeeping_columns(fake_nav) -> None:
    import pyarrow as pa

    line = MagicMock()
    line.has_fts.return_value = True
    line.versions = [1]
    _state["sphere"]._sphere.lines = {"accounts": line}
    _state["session"]._reader.search_points_fts.return_value = pa.table(
        {
            "primary_key": ["1", "2"],
            "status": ["active", "active"],
            "frequency": ["X", "Y"],
            "version": [1, 1],
            "created_at": ["t", "t"],
            "changed_at": ["t", "t"],
            "_score": [1.0, 0.5],
        }
    )
    out = json.loads(session_tools.search_entities_fts("accounts", "X", 2))
    _assert_one_properties_table(out, "entities")
    assert out["properties"]["columns"] == ["primary_key", "frequency"]


@pytest.mark.parametrize("tool", ["compare_entities", "find_common_relations"])
def test_pair_tools_tabulate_both_entities(compact, tool) -> None:
    out = json.loads(getattr(analysis, tool)("1", "2", _A))
    assert "entity_a" not in out and "entity_b" not in out
    assert [r[0] for r in out["properties"]["rows"]] == ["1", "2"]


def test_find_common_relations_folds_dim_edges(compact) -> None:
    out = json.loads(analysis.find_common_relations("1", "2", _A))
    assert all(r["point_key"] for r in out["common_relations"])
    assert out["common_dim_lines"]
    assert out["common_count"] == len(out["common_relations"]) + len(out["common_dim_lines"])


# ---------------------------------------------------------------- envelopes


def test_get_sphere_info_collapses_derived_dimension_lines(compact) -> None:
    out = json.loads(session_tools.get_sphere_info())
    assert not any(k.startswith("_d_") for k in out["lines"])
    assert "_d_tx_count" in out["derived_dimension_lines"]


def test_find_hubs_echoes_nothing_unset_or_twice(compact) -> None:
    out = json.loads(analysis.find_hubs(_A, top_n=3))
    assert "line_id_filter" not in out
    assert "max_hub_score" not in out["score_stats"]


def test_find_calibration_influencers_entries_have_no_null_fields(compact) -> None:
    out = json.loads(
        analysis.find_calibration_influencers(
            "account_bank_pairs_pattern", top_n=3, sample_size=500
        )
    )
    assert out["entries"]
    assert all(v is not None for e in out["entries"] for v in e.values())


def test_contrast_populations_does_not_echo_unset_group_b(compact) -> None:
    out = json.loads(analysis.contrast_populations(_A, {"anomaly": True}))
    assert "group_b_spec" not in out


def test_get_centroid_map_is_labelled_and_names_grouping_once(compact) -> None:
    out = json.loads(
        analysis.get_centroid_map(
            _A, group_by_property="accounts:frequency", sample_size=500, max_groups=3
        )
    )
    labels = compact.patterns[_A].dim_labels
    assert out["group_by"] == "accounts:frequency"
    assert "group_by_property" not in out
    assert list(out["global_centroid"]["vector"]) == labels
    assert all(list(g["vector"]) == labels for g in out["group_centroids"])


def test_contagion_score_batch_does_not_repeat_pattern_id(fake_nav) -> None:
    fake_nav.contagion_score_batch.return_value = {
        "pattern_id": "p",
        "total": 2,
        "results": [{"primary_key": "A", "pattern_id": "p", "score": 0.5}] * 2,
        "summary": {},
    }
    out = json.loads(analysis.contagion_score_batch(["A", "B"], "p"))
    assert all("pattern_id" not in r for r in out["results"])


def test_simulate_edge_removal_hoists_constant_edge_fields(fake_nav) -> None:
    edge = {"edge_id": "e", "edge_line_id": "tx", "dimensions_simulated": [0, 1, 2]}
    fake_nav.simulate_edge_removal.return_value = {
        "edges": [dict(edge, edge_id="e1"), dict(edge, edge_id="e2")],
        "edges_total": 2,
    }
    out = json.loads(analysis.simulate_edge_removal("A", "p", "l"))
    assert out["edge_line_id"] == "tx"
    assert out["dimensions_simulated"] == [0, 1, 2]
    assert all(set(e) == {"edge_id"} for e in out["edges"])


def test_find_high_potential_edges_drops_rank_alias(fake_nav) -> None:
    cached = [{"from_key": "A", "score_rank_pct": 99.0, "score_rank_pct_global": 99.0}]
    fake_nav.attract_edge_potential.return_value = cached
    out = json.loads(analysis.find_high_potential_edges("p"))
    assert out["results"] == [{"from_key": "A", "score_rank_pct_global": 99.0}]
    assert "score_rank_pct" in cached[0]  # the navigator's cached rows stay intact


def test_find_high_potential_motifs_does_not_repeat_motif_type(fake_nav) -> None:
    fake_nav.find_high_potential_motifs.return_value = [
        {"motif_type": "cycle_2", "score": 1.0, "edges": []},
        {"motif_type": "cycle_2", "score": 0.5, "edges": []},
    ]
    out = json.loads(analysis.find_high_potential_motifs("p", "cycle_2"))
    assert out["motif_type"] == "cycle_2"
    assert all("motif_type" not in r for r in out["results"])


def test_continuous_hub_tools_drop_the_edge_count_estimate(compact) -> None:
    # In continuous mode the "edge count" is only the rounded hub score.
    assert compact.patterns[_A].edge_max is not None
    hubs = json.loads(analysis.find_hubs(_A, top_n=3))
    assert hubs["mode"] == "continuous"
    assert all("alive_edges" not in r for r in hubs["results"])
    history = json.loads(analysis.hub_history("1", _A))
    assert history["history"]
    assert all("alive_edges_est" not in h for h in history["history"])
    assert "alive_edges_est" not in (history["base_state"] or {})


def test_aggregate_forwards_group_by_relation(fake_nav) -> None:
    import hypertopos_mcp.tools.aggregation as aggregation

    fake_nav.aggregate.return_value = {"results": []}
    aggregation.aggregate("tx_pattern", "accounts", group_by_relation="sender")
    assert fake_nav.aggregate.call_args.kwargs["group_by_relation"] == "sender"


def test_get_sphere_info_names_the_calibrated_score_per_pattern(compact) -> None:
    from hypertopos_mcp.tools.session import get_sphere_info

    out = json.loads(get_sphere_info())
    calibrated = out["patterns"][_A]
    assert calibrated["score_dims"] == list(compact.patterns[_A].score_dims)
    assert calibrated["score_axes"] == list(compact.patterns[_A].score_axes)
    assert calibrated["anomaly_percentile"] == compact.patterns[_A].anomaly_percentile
    assert "score_dims" not in out["patterns"]["tx_pattern"]
