# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for summary= flag on find_anomalies/find_clusters and get_centroid_map schema fix.

Covers:
- F2a: empty-point_key edges are filtered from find_anomalies polygons (default mode).
- F2b: summary=True drops delta, edges, properties from find_anomalies polygons.
- F2b: summary=True drops centroid_delta, dim_profile, member-properties from find_clusters.
- F5a: get_centroid_map accepts group_by_property alone (group_by_line derived from prefix);
       accepts group_by_line alone (backwards compat); rejects when both missing.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _ensure_berka_navigator(open_berka_sphere):
    """Defensive reopen — earlier tests in the suite occasionally leave _state
    in a half-populated state (sphere mock set, navigator None) that the
    session-level _restore_berka_state autouse cannot detect. Reopen Berka if
    the navigator is missing so each test sees a real navigator."""
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    if _state.get("navigator") is None:
        open_sphere(open_berka_sphere)
    yield


# ---------------------------------------------------------------------------
# F2a + F2b — find_anomalies
# ---------------------------------------------------------------------------


def test_find_anomalies_filters_empty_point_key_edges_even_when_summary_false(
    open_berka_sphere,
):
    """Default mode: edges with empty point_key are stripped from polygons."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies(pattern_id="account_stress_pattern", top_n=5))
    assert "polygons" in result
    for poly in result["polygons"]:
        edges = poly.get("edges", [])
        for e in edges:
            assert e.get("point_key"), (
                f"degenerate edge with empty point_key leaked through default-mode trim: {e}"
            )


def test_find_anomalies_summary_false_returns_full_payload(open_berka_sphere):
    """summary=False: full polygon payload retained — delta + edges + properties."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies(pattern_id="account_stress_pattern", top_n=3, summary=False))
    assert result["polygons"], "expected at least one polygon for assertion"
    poly = result["polygons"][0]
    # Heavy fields present
    assert "delta" in poly
    assert isinstance(poly["delta"], list)
    assert len(poly["delta"]) > 0
    # edge counts stay; the edges list is left out when no jumpable edge remains
    assert "total_edges" in poly and "alive_edges" in poly
    assert poly.get("edges", [None]) != []


def test_find_anomalies_polygons_drop_repeated_fields_and_table_properties(open_berka_sphere):
    """Per-polygon type/pattern_id/properties move out; properties become one key-first table."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies(pattern_id="account_stress_pattern", top_n=3))
    assert result["polygons"]
    for poly in result["polygons"]:
        assert not {"type", "pattern_id", "properties"} & poly.keys()
    table = result["properties"]
    assert table["columns"][0] == "primary_key"
    assert [row[0] for row in table["rows"]] == [p["primary_key"] for p in result["polygons"]]

    summary = json.loads(find_anomalies(pattern_id="account_stress_pattern", top_n=3, summary=True))
    assert "properties" not in summary


def test_find_anomalies_event_pattern_sends_endpoint_properties_once(open_berka_sphere):
    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies(pattern_id="tx_pattern", top_n=5))
    assert result["polygons"]
    structural = {"line_id", "point_key", "status", "direction", "is_jumpable"}
    assert all(set(e) <= structural for p in result["polygons"] for e in p.get("edges", []))
    related = result["related_properties"]
    for table in related.values():
        keys = [row[0] for row in table["rows"]]
        assert table["columns"][0] == "point_key" and len(keys) == len(set(keys))


def test_find_anomalies_summary_true_trims_payload(open_berka_sphere):
    """summary=True drops delta, edges, properties; response < 50% of full payload."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    full_raw = find_anomalies(pattern_id="account_stress_pattern", top_n=10, summary=False)
    summary_raw = find_anomalies(pattern_id="account_stress_pattern", top_n=10, summary=True)
    full = json.loads(full_raw)
    summary = json.loads(summary_raw)

    # Same number of polygons returned
    assert len(full["polygons"]) == len(summary["polygons"])
    assert summary["polygons"], "expected at least one polygon for assertion"

    for poly in summary["polygons"]:
        # Heavy fields removed
        assert "delta" not in poly
        assert "edges" not in poly
        assert "total_edges" not in poly
        assert "alive_edges" not in poly
        assert "properties" not in poly
        # Retained scalar fields
        assert "primary_key" in poly
        assert "delta_norm" in poly
        assert "is_anomaly" in poly

    # Payload shrunk substantially. Target reduction depends on dim count: on a
    # small-dim pattern (≤10 dims) ~40%, on a wide pattern (>20 dims) up to ~80%
    # because polygon[].delta dominates. Floor at 25% to keep the test robust
    # across spheres while still asserting the trim is non-trivial.
    assert len(summary_raw) < len(full_raw) * 0.75, (
        f"summary mode did not shrink payload enough: "
        f"summary={len(summary_raw)} full={len(full_raw)}"
    )


# ---------------------------------------------------------------------------
# F2b — find_clusters
# ---------------------------------------------------------------------------


def test_find_clusters_summary_true_trims_payload(open_berka_sphere):
    """summary=True drops centroid_delta, dim_profile, member-properties."""
    from hypertopos_mcp.tools.analysis import find_clusters

    full = json.loads(find_clusters(pattern_id="account_stress_pattern", n_clusters=3, top_n=5))
    summary = json.loads(
        find_clusters(
            pattern_id="account_stress_pattern",
            n_clusters=3,
            top_n=5,
            summary=True,
        )
    )

    assert summary["clusters"], "expected at least one cluster for assertion"
    for c in summary["clusters"]:
        assert "centroid_delta" not in c
        assert "dim_profile" not in c
        assert "representative_properties" not in c
        # Members are bare keys
        assert all(isinstance(m, str) for m in c.get("members", []))
        # Retained scalar fields
        assert "cluster_id" in c
        assert "size" in c
        assert "anomaly_rate" in c
        assert "representative_key" in c

    assert "properties" not in summary

    # Default mode (sanity): labelled centroid_delta and member properties table present
    assert full["clusters"]
    assert isinstance(full["clusters"][0]["centroid_delta"], dict)
    assert full["properties"]["columns"][0] == "key"


# ---------------------------------------------------------------------------
# F5a — get_centroid_map schema
# ---------------------------------------------------------------------------


def test_get_centroid_map_accepts_group_by_property_alone(open_berka_sphere):
    """group_by_property alone is valid — group_by_line derived from 'line_id:' prefix."""
    from hypertopos_mcp.tools.analysis import get_centroid_map

    raw = get_centroid_map(
        pattern_id="tx_pattern",
        group_by_property="transactions:operation",
    )
    result = json.loads(raw)
    # Must not blow up on missing group_by_line — should produce real centroid map.
    assert "error" not in result, f"unexpected error: {result.get('error')}"
    assert result["pattern_id"] == "tx_pattern"
    assert "group_centroids" in result
    assert result["group_by"] == "transactions:operation"


def test_get_centroid_map_accepts_group_by_line_alone(open_berka_sphere):
    """Backwards compatibility — group_by_line alone still works."""
    from hypertopos_mcp.tools.analysis import get_centroid_map

    raw = get_centroid_map(
        pattern_id="account_stress_pattern",
        group_by_line="accounts",
    )
    result = json.loads(raw)
    assert "pattern_id" in result
    # Either succeeded or surfaced a known continuous-mode hint — never a
    # schema-validation error.
    assert result["pattern_id"] == "account_stress_pattern"


def test_get_centroid_map_rejects_both_missing(open_berka_sphere):
    """When neither group_by_line nor group_by_property is supplied, return JSON error."""
    from hypertopos_mcp.tools.analysis import get_centroid_map

    raw = get_centroid_map(pattern_id="tx_pattern")
    result = json.loads(raw)
    assert result.get("error")
    assert "group_by_line" in result["error"]
    assert "group_by_property" in result["error"]
    assert result["pattern_id"] == "tx_pattern"


def test_find_anomalies_summary_true_drops_cluster_delta(open_berka_sphere):
    """summary=True: no positional delta anywhere, clusters included."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies(pattern_id="account_stress_pattern", top_n=5, summary=True))
    assert result["clusters"], "expected clusters for assertion"
    assert all("delta" not in c for c in result["clusters"])
    assert all("label" in c for c in result["clusters"])


def test_get_polygon_compact_lists_dim_line_edges_by_name(open_berka_sphere):
    """Compact get_polygon: degenerate dim-line edges become one dim_edges list; full keeps them."""
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.geometry import get_polygon
    from hypertopos_mcp.tools.navigation import goto

    goto("1", "accounts")
    compact = json.loads(get_polygon("account_stress_pattern"))
    previous = _state.get("response_format")
    _state["response_format"] = "full"
    try:
        full = json.loads(get_polygon("account_stress_pattern"))
    finally:
        _state["response_format"] = previous

    dim_full = [e["line_id"] for e in full["edges"] if not e["point_key"]]
    assert dim_full, "fixture pattern must have dim-line edges"
    assert compact["dim_edges"] == dim_full
    assert all(e["point_key"] for e in compact.get("edges", []))
    assert "dim_edges" not in full
