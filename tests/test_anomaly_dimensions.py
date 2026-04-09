# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for anomaly_dimensions (GDSEngine static method)."""

from __future__ import annotations

from hypertopos.engine.geometry import GDSEngine

anomaly_dimensions = GDSEngine.anomaly_dimensions


def test_top_dimensions_sorted_by_contribution() -> None:
    """Returns top-N dimensions sorted by squared contribution descending."""
    delta = [3.0, 0.1, 2.0, 0.0]
    labels = ["customers", "products", "regions", "channels"]
    result = anomaly_dimensions(delta, labels, top_n=3)
    assert len(result) >= 1
    assert result[0]["label"] == "customers"
    assert result[0]["contribution_pct"] > 50.0
    # Sorted descending
    pcts = [r["contribution_pct"] for r in result]
    assert pcts == sorted(pcts, reverse=True)


def test_zero_delta_returns_empty() -> None:
    """All-zero delta vector produces empty list."""
    result = anomaly_dimensions([0.0, 0.0, 0.0], ["a", "b", "c"])
    assert result == []


def test_single_dimension() -> None:
    """Single-dim delta returns one entry at 100%."""
    result = anomaly_dimensions([5.0], ["only_dim"])
    assert len(result) == 1
    assert result[0]["label"] == "only_dim"
    assert result[0]["contribution_pct"] == 100.0


def test_equal_contributions() -> None:
    """Equal delta values produce equal contributions."""
    result = anomaly_dimensions([1.0, 1.0, 1.0], ["a", "b", "c"], top_n=3)
    assert len(result) == 3
    for r in result:
        assert abs(r["contribution_pct"] - 33.3) < 1.0


def test_negligible_dims_excluded() -> None:
    """Dimensions contributing < 5% are excluded."""
    # dim 0 dominates, dim 1 is negligible
    delta = [10.0, 0.1]
    labels = ["big", "tiny"]
    result = anomaly_dimensions(delta, labels, top_n=2)
    assert len(result) == 1
    assert result[0]["label"] == "big"


def test_dim_labels_shorter_than_delta() -> None:
    """When dim_labels is shorter than delta, fallback label is used."""
    delta = [1.0, 2.0, 3.0]
    labels = ["a"]  # only 1 label for 3 dims
    result = anomaly_dimensions(delta, labels, top_n=3)
    # dim 2 (index 2) should have fallback label
    dim2 = next((r for r in result if r["dim"] == 2), None)
    assert dim2 is not None
    assert dim2["label"] == "dim_2"
