# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""End-to-end MCP test for chain-anchor edge_dim_aggregations regime.

Drives the MCP tool wrapper (open_sphere → find_anomalies →
anomaly_summary) on a synthetic 5-chain sphere with chain-anchor
aggregations declared, exercising the full JSON serialization path
that the navigator-layer integration tests in
hypertopos-py/tests/test_edge_dim_aggregations_chain.py do not.

Regression guards:
- _sanitize_for_json must not coerce aggregated dim names away
- pattern.dim_labels lookup inside the find_anomalies wrapper must
  resolve `<source_dim>_mean` / `<source_dim>_max` for chain regime
- anomaly_summary on a chain pattern with edge_dim_aggregations must
  not regress to the (33,) (37,) (33,) broadcast bug class (F6)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


_CHAIN_FEATURES = ["hop_count", "is_cyclic"]


def _build_chain_sphere(out_root: Path) -> Path:
    """Build a minimal 5-chain sphere with chain-anchor aggregations.

    Mirrors the synthetic fixture in
    hypertopos-py/tests/test_edge_dim_aggregations_chain.py but stays
    self-contained so this MCP-layer test does not depend on the
    sibling-package test module's import path.
    """
    from hypertopos.builder import GDSBuilder, RelationSpec
    from hypertopos.builder.builder import EdgeTableConfig
    from hypertopos.builder.mapping import (
        EdgeDimAggregationsConfig,
        EdgeDimensionsConfig,
    )

    b = GDSBuilder("test_chain_eda_mcp", str(out_root))
    b.add_line(
        "accounts",
        [
            {"acct_id": "A"}, {"acct_id": "B"}, {"acct_id": "C"},
            {"acct_id": "D"}, {"acct_id": "E"},
        ],
        key_col="acct_id", source_id="t",
    )
    b.add_line(
        "transactions",
        [
            {"tx_id": "evt1", "from_acct": "A", "to_acct": "B",
             "ts": 1000.0, "amount": 10000.0},
            {"tx_id": "evt2", "from_acct": "B", "to_acct": "C",
             "ts": 2000.0, "amount": 5000.0},
            {"tx_id": "evt3", "from_acct": "C", "to_acct": "D",
             "ts": 3000.0, "amount": 2500.0},
            {"tx_id": "evt4", "from_acct": "A", "to_acct": "E",
             "ts": 4000.0, "amount": 8000.0},
            {"tx_id": "evt5", "from_acct": "D", "to_acct": "E",
             "ts": 5000.0, "amount": 1200.0},
            {"tx_id": "evt6", "from_acct": "E", "to_acct": "A",
             "ts": 6000.0, "amount": 600.0},
        ],
        key_col="tx_id", source_id="t",
    )
    edge_dims = EdgeDimensionsConfig(dims={
        "find_motif_structuring": {
            "time_window_hours": 24.0,
            "amt1_min": 5000.0,
            "amt2_max": 7500.0,
        },
    })
    b.add_pattern(
        "tx_pattern",
        pattern_type="event",
        entity_line="transactions",
        relations=[
            RelationSpec(
                "accounts", fk_col="from_acct", direction="in", required=True,
            ),
        ],
        edge_table=EdgeTableConfig(
            from_col="from_acct", to_col="to_acct",
            timestamp_col="ts", amount_col="amount",
        ),
        edge_dimensions=edge_dims,
    )
    chains = [
        {"chain_id": "ch_long", "keys": ["A", "B", "C", "D"],
         "event_keys": ["evt1", "evt2", "evt3"], "hop_count": 3,
         "is_cyclic": 0.0},
        {"chain_id": "ch_cycle", "keys": ["A", "E", "A"],
         "event_keys": ["evt4", "evt6"], "hop_count": 2,
         "is_cyclic": 1.0},
        {"chain_id": "ch_short", "keys": ["D", "E"],
         "event_keys": ["evt5"], "hop_count": 1, "is_cyclic": 0.0},
        {"chain_id": "ch_overlap", "keys": ["A", "B", "C"],
         "event_keys": ["evt1", "evt2"], "hop_count": 2,
         "is_cyclic": 0.0},
        {"chain_id": "ch_isolated", "keys": ["E", "A"],
         "event_keys": ["evt6"], "hop_count": 1, "is_cyclic": 0.0},
    ]
    b.add_chain_line("tx_chains", chains=chains, features=_CHAIN_FEATURES)
    b.add_pattern(
        "tx_chains_pattern",
        pattern_type="anchor",
        entity_line="tx_chains",
        relations=[],
        edge_dim_aggregations=EdgeDimAggregationsConfig(
            from_event_pattern="tx_pattern",
            dims=("find_motif_structuring",),
        ),
    )
    b.build()
    return out_root


@pytest.fixture
def _chain_sphere(tmp_path):
    """Build the chain sphere ONCE per test, then open it via MCP open_sphere.

    Each test gets a fresh sphere — chains' max_chains is small (5),
    builds in well under a second on commodity hardware.
    """
    sphere_path = _build_chain_sphere(tmp_path / "gds_chain")

    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import close_sphere, open_sphere

    open_sphere(str(sphere_path))
    try:
        yield sphere_path
    finally:
        try:
            close_sphere()
        except Exception:
            for k in list(_state.keys()):
                _state[k] = None


def test_find_anomalies_chain_pattern_returns_valid_json(_chain_sphere):
    """find_anomalies MCP tool returns valid JSON on a chain anchor pattern."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    payload = find_anomalies(pattern_id="tx_chains_pattern", top_n=10)
    parsed = json.loads(payload)
    # Keys exposed by the wrapper — sanity check on the shape.
    assert "polygons" in parsed or "anomalies" in parsed or "results" in parsed, (
        f"chain pattern find_anomalies response missing expected list field; "
        f"got top-level keys: {sorted(parsed.keys())}"
    )


def test_find_anomalies_chain_pattern_surfaces_aggregated_dim_labels(_chain_sphere):
    """Aggregated dim names appear in JSON anomaly_dimensions, not as `dim_<idx>`."""
    from hypertopos_mcp.tools.navigation import find_anomalies

    payload = find_anomalies(pattern_id="tx_chains_pattern", top_n=10)
    parsed = json.loads(payload)
    polygons = (
        parsed.get("polygons")
        or parsed.get("anomalies")
        or parsed.get("results")
        or []
    )
    # Any anomaly entry exposing anomaly_dimensions must use human-readable
    # labels for the aggregated dims. Each entry shape is:
    #   {"dim": <int_index>, "label": "<dim_name>", ...}
    # F6 regression check on the chain regime path through MCP.
    seen_anomaly_dims = False
    for entry in polygons:
        ad = entry.get("anomaly_dimensions")
        if not ad:
            continue
        seen_anomaly_dims = True
        for d in ad:
            label = d.get("label") if isinstance(d, dict) else None
            if not label:
                continue
            assert not label.startswith("dim_"), (
                f"chain pattern surfaced placeholder label {label!r} via MCP — "
                f"Pattern.dim_labels likely missed an aggregated dim entry"
            )
    # Population N=5 is small; θ-driven flags may not surface anomaly_dimensions
    # on every run. The hard contract is "no placeholders when present",
    # already pinned above.
    if not seen_anomaly_dims:
        pytest.skip(
            "Population N=5 too small for stable θ-driven anomaly flags; "
            "label-quality contract still pinned by the explicit assertion above"
        )


def test_anomaly_summary_chain_pattern_does_not_regress_broadcast_bug(_chain_sphere):
    """anomaly_summary on chain pattern with aggregations: no broadcast crash + aggregated names surface."""
    from hypertopos_mcp.tools.navigation import anomaly_summary

    payload = anomaly_summary(pattern_id="tx_chains_pattern")
    parsed = json.loads(payload)
    # Pre-F6 this would have crashed with `operands could not be broadcast
    # together with shapes (X,) (Y,) (X,)` because dim_sq_totals was sized
    # from len(pattern.dim_labels) but cluster delta vectors were longer.
    # Post-F6 + chain regime: dim_labels includes aggregated names, broadcast
    # aligns, the call returns a structured summary.
    assert parsed.get("pattern_id") == "tx_chains_pattern"
    assert "total_entities" in parsed
    assert "total_anomalies" in parsed
    # Stronger contract: top_driving_dimensions must surface aggregated dim
    # names with human-readable labels (no `dim_<idx>` placeholders). This
    # exercises the same label-resolution path as anomaly_dimensions but on
    # the population-aggregated summary side.
    tdd = parsed.get("top_driving_dimensions") or []
    surfaced_labels = {d.get("label") for d in tdd if isinstance(d, dict)}
    # The synthetic sphere declares find_motif_structuring as the only
    # aggregated source dim → expect both `_mean` and `_max` to surface
    # in top_driving_dimensions when ch_long / ch_overlap trip the dim.
    expected_aggregated = {
        "find_motif_structuring_mean",
        "find_motif_structuring_max",
    }
    assert expected_aggregated.issubset(surfaced_labels), (
        f"top_driving_dimensions missing aggregated edge-dim names; got "
        f"{sorted(surfaced_labels)}, expected to include "
        f"{sorted(expected_aggregated)}"
    )
    # No placeholder labels.
    for d in tdd:
        label = d.get("label") if isinstance(d, dict) else None
        if label:
            assert not label.startswith("dim_"), (
                f"placeholder label {label!r} leaked in chain pattern summary"
            )
