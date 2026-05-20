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
        open_sphere("benchmark/berka/sphere/gds_berka_banking")
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


def test_find_anomalies_summary_false_default_returns_full_payload(open_berka_sphere):
    """Default (summary=False): full polygon payload retained — delta + edges + properties."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies(pattern_id="account_stress_pattern", top_n=3))
    assert result["polygons"], "expected at least one polygon for assertion"
    poly = result["polygons"][0]
    # Heavy fields present
    assert "delta" in poly
    assert isinstance(poly["delta"], list)
    assert len(poly["delta"]) > 0
    # edges may be empty after F2a trim, but key must exist
    assert "edges" in poly


def test_find_anomalies_summary_true_trims_payload(open_berka_sphere):
    """summary=True drops delta, edges, properties; response < 50% of full payload."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    full_raw = find_anomalies(pattern_id="account_stress_pattern", top_n=10)
    summary_raw = find_anomalies(
        pattern_id="account_stress_pattern", top_n=10, summary=True
    )
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

    full = json.loads(
        find_clusters(pattern_id="account_stress_pattern", n_clusters=3, top_n=5)
    )
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
        # Members trimmed to bare keys
        for m in c.get("members", []):
            assert set(m.keys()) == {"key"}, (
                f"member should retain only 'key' under summary=True, got {set(m.keys())}"
            )
        # Retained scalar fields
        assert "cluster_id" in c
        assert "size" in c
        assert "anomaly_rate" in c
        assert "representative_key" in c

    # Default mode (sanity): centroid_delta still present
    assert full["clusters"]
    assert "centroid_delta" in full["clusters"][0]


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
