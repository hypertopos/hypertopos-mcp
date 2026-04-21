# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for serialization helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from hypertopos.model.objects import Edge, Point, Polygon, Solid, SolidSlice
from hypertopos_mcp.serializers import (
    _serialize_edge,
    _serialize_point,
    _serialize_polygon,
    _serialize_position,
    _serialize_slice,
    _serialize_solid,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_point(
    bk: str = "CUST-0001", line_id: str = "customers", properties: dict | None = None
) -> Point:
    return Point(
        primary_key=bk,
        line_id=line_id,
        version=1,
        status="active",
        properties=properties if properties is not None else {"name": "Alice", "region": "EMEA"},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        changed_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _make_edge(
    line_id: str = "products",
    point_key: str = "PROD-001",
    status: str = "alive",
    direction: str = "out",
) -> Edge:
    return Edge(line_id=line_id, point_key=point_key, status=status, direction=direction)


def _make_polygon(
    bk: str = "CUST-0001", is_anomaly: bool = False, delta_rank_pct: float | None = 75.0
) -> Polygon:
    edges = [
        _make_edge("products", "PROD-001", "alive", "out"),
        _make_edge("products", "PROD-002", "dead", "out"),
        _make_edge("stores", "STORE-01", "alive", "out"),
    ]
    delta = np.array([0.3, -0.2], dtype=np.float32)
    return Polygon(
        primary_key=bk,
        pattern_id="customer_pattern",
        pattern_ver=1,
        pattern_type="anchor",
        scale=1,
        delta=delta,
        delta_norm=float(np.linalg.norm(delta)),
        is_anomaly=is_anomaly,
        edges=edges,
        last_refresh_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        delta_rank_pct=delta_rank_pct,
    )


def _make_slice(idx: int = 0) -> SolidSlice:
    return SolidSlice(
        slice_index=idx,
        timestamp=datetime(2024, 3, idx + 1, tzinfo=UTC),
        deformation_type="edge",
        delta_snapshot=np.array([0.1, -0.1], dtype=np.float32),
        delta_norm_snapshot=0.1414,
        pattern_ver=1,
        changed_property=None,
        changed_line_id="products",
        added_edge=None,
    )


def _make_solid(bk: str = "CUST-0001") -> Solid:
    base = _make_polygon(bk)
    slices = [_make_slice(i) for i in range(3)]
    return Solid(primary_key=bk, pattern_id="customer_pattern", base_polygon=base, slices=slices)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestSerializeEdge:
    def test_alive_edge(self) -> None:
        edge = _make_edge()
        result = _serialize_edge(edge)
        assert result["line_id"] == "products"
        assert result["point_key"] == "PROD-001"
        assert result["status"] == "alive"
        assert result["direction"] == "out"

    def test_dead_edge(self) -> None:
        edge = _make_edge(status="dead")
        result = _serialize_edge(edge)
        assert result["status"] == "dead"


class TestSerializePoint:
    def test_basic_fields(self) -> None:
        pt = _make_point()
        result = _serialize_point(pt)
        assert result["type"] == "Point"
        assert result["primary_key"] == "CUST-0001"
        assert result["line_id"] == "customers"
        assert result["status"] == "active"
        assert result["properties"]["name"] == "Alice"

    def test_properties_are_strings(self) -> None:
        pt = _make_point()
        result = _serialize_point(pt)
        for v in result["properties"].values():
            assert isinstance(v, str)

    def test_datetime_property_serializes_to_iso_string(self) -> None:
        from datetime import datetime

        pt = _make_point(properties={"posting_date": datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)})
        result = _serialize_point(pt)
        assert result["properties"]["posting_date"] == "2024-06-15T10:30:00+00:00"

    def test_date_property_serializes_to_iso_string(self) -> None:
        from datetime import date

        pt = _make_point(properties={"birth_date": date(2024, 1, 1)})
        result = _serialize_point(pt)
        assert result["properties"]["birth_date"] == "2024-01-01"

    def test_non_datetime_properties_unchanged(self) -> None:
        pt = _make_point(properties={"name": "Alice", "amount": 42.5, "active": True, "code": None})
        result = _serialize_point(pt)
        assert result["properties"] == {
            "name": "Alice",
            "amount": 42.5,
            "active": True,
            "code": None,
        }


class TestSerializePolygon:
    def test_basic_fields(self) -> None:
        poly = _make_polygon()
        result = _serialize_polygon(poly)
        assert result["type"] == "Polygon"
        assert result["primary_key"] == "CUST-0001"
        assert result["pattern_id"] == "customer_pattern"
        assert isinstance(result["delta"], list)
        assert isinstance(result["delta_norm"], float)

    def test_only_alive_edges_in_edges(self) -> None:
        poly = _make_polygon()
        result = _serialize_polygon(poly)
        assert result["total_edges"] == 3
        assert result["alive_edges"] == 2
        assert len(result["edges"]) == 2
        for e in result["edges"]:
            assert e["status"] == "alive"

    def test_anomaly_flag(self) -> None:
        poly = _make_polygon(is_anomaly=True)
        result = _serialize_polygon(poly)
        assert result["is_anomaly"] is True

    def test_delta_rounded(self) -> None:
        poly = _make_polygon()
        result = _serialize_polygon(poly)
        assert isinstance(result["delta_norm"], float)
        assert len(str(result["delta_norm"]).split(".")[-1]) <= 4

    def test_delta_rank_pct_included(self) -> None:
        poly = _make_polygon(delta_rank_pct=75.0)
        result = _serialize_polygon(poly)
        assert "delta_rank_pct" in result
        assert result["delta_rank_pct"] == 75.0

    def test_delta_rank_pct_omitted_when_none(self) -> None:
        poly = _make_polygon(delta_rank_pct=None)
        result = _serialize_polygon(poly)
        assert "delta_rank_pct" not in result


class TestSerializeSlice:
    def test_basic_fields(self) -> None:
        sl = _make_slice(0)
        result = _serialize_slice(sl)
        assert result["slice_index"] == 0
        assert result["deformation_type"] == "edge"
        assert result["changed_line_id"] == "products"
        assert result["changed_property"] is None
        assert "T" in result["timestamp"]

    def test_timestamp_is_iso(self) -> None:
        sl = _make_slice(2)
        result = _serialize_slice(sl)
        dt = datetime.fromisoformat(result["timestamp"])
        assert dt.year == 2024


def _make_slice_with_prop(idx: int = 0, prop_values: list[float] | None = None) -> SolidSlice:
    """Make a SolidSlice with 2 structural + optional prop column values in delta_snapshot."""
    structural = [0.1, -0.1]
    extra = prop_values if prop_values is not None else []
    delta = np.array(structural + extra, dtype=np.float32)
    return SolidSlice(
        slice_index=idx,
        timestamp=datetime(2024, 3, idx + 1, tzinfo=UTC),
        deformation_type="property",
        delta_snapshot=delta,
        delta_norm_snapshot=float(np.linalg.norm(delta)),
        pattern_ver=1,
        changed_property="some_prop",
        changed_line_id=None,
        added_edge=None,
    )


class _MockPattern:
    """Minimal mock for Pattern with relations and prop_columns."""

    def __init__(
        self,
        n_relations: int,
        prop_columns: list[str],
        mu: np.ndarray | None = None,
        sigma_diag: np.ndarray | None = None,
    ) -> None:
        self.relations = [object()] * n_relations
        self.prop_columns = prop_columns
        n_total = n_relations + len(prop_columns)
        self.mu = mu if mu is not None else np.zeros(n_total, dtype=np.float32)
        self.sigma_diag = (
            sigma_diag if sigma_diag is not None else np.ones(n_total, dtype=np.float32)
        )


class TestSerializeSolid:
    def test_basic_fields(self) -> None:
        solid = _make_solid()
        result = _serialize_solid(solid)
        assert result["type"] == "Solid"
        assert result["primary_key"] == "CUST-0001"
        assert result["pattern_id"] == "customer_pattern"
        assert result["num_slices"] == 3
        assert len(result["slices"]) == 3

    def test_base_polygon_included(self) -> None:
        solid = _make_solid()
        result = _serialize_solid(solid)
        assert result["base_polygon"]["type"] == "Polygon"

    def test_slice_delta_trimmed_with_pattern(self) -> None:
        """delta_snapshot is trimmed to n_rel dims and prop_column_states is added."""
        sl = _make_slice_with_prop(prop_values=[0.25, -4.75])
        pattern = _MockPattern(
            n_relations=2,
            prop_columns=["flag_a", "flag_b"],
            mu=np.array([0.0, 0.0, 0.95, 0.95], dtype=np.float32),
            sigma_diag=np.array([1.0, 1.0, 0.2, 0.2], dtype=np.float32),
        )
        result = _serialize_slice(sl, pattern=pattern)
        # delta_snapshot must only contain structural dims (first 2)
        assert len(result["delta_snapshot"]) == 2
        assert result["delta_snapshot"] == [round(0.1, 4), round(-0.1, 4)]
        # prop_column_states must be present
        assert "prop_column_states" in result
        # flag_a: shape = 0.25 * 0.2 + 0.95 = 1.0 > 0.5 → True
        assert result["prop_column_states"]["flag_a"] is True
        # flag_b: shape = -4.75 * 0.2 + 0.95 = 0.0 > 0.5 → False
        assert result["prop_column_states"]["flag_b"] is False

    def test_slice_no_pattern_full_delta(self) -> None:
        """Without pattern, full delta_snapshot is returned and no prop_column_states."""
        sl = _make_slice_with_prop(prop_values=[75.0, -10.0])
        result = _serialize_slice(sl)
        assert len(result["delta_snapshot"]) == 4
        assert "prop_column_states" not in result

    def test_prop_column_states_bool_values(self) -> None:
        """Boundary: shape = delta*sigma+mu; shape > 0.5 → True, else False."""
        # With mu=0.95, sigma=0.2:
        #   shape(0.0) = 0*0.2+0.95 = 0.95 > 0.5 → True (borderline present)
        #   shape(0.25) = 0.25*0.2+0.95 = 1.0 > 0.5 → True
        #   shape(-4.75) = -4.75*0.2+0.95 = 0.0 ≤ 0.5 → False
        sl = _make_slice_with_prop(prop_values=[0.0, 0.25, -4.75])
        pattern = _MockPattern(
            n_relations=2,
            prop_columns=["borderline", "present", "absent"],
            mu=np.array([0.0, 0.0, 0.95, 0.95, 0.95], dtype=np.float32),
            sigma_diag=np.array([1.0, 1.0, 0.2, 0.2, 0.2], dtype=np.float32),
        )
        result = _serialize_slice(sl, pattern=pattern)
        assert result["prop_column_states"]["borderline"] is True
        assert result["prop_column_states"]["present"] is True
        assert result["prop_column_states"]["absent"] is False


def test_serialize_point_null_property_is_json_null():
    """None property values must become JSON null, not the string 'None'."""
    import json

    from hypertopos_mcp.serializers import _serialize_point

    class _MockPoint:
        primary_key = "GLE-0000001"
        line_id = "gl_values"
        status = "active"
        properties = {"quantity": None, "unit": None, "amount_local": 86390.55}

    result = json.loads(json.dumps(_serialize_point(_MockPoint())))
    assert result["properties"]["quantity"] is None, "expected JSON null, got string"
    assert result["properties"]["unit"] is None, "expected JSON null, got string"
    assert result["properties"]["amount_local"] == 86390.55


class TestSerializePosition:
    def test_none_position(self) -> None:
        result = _serialize_position(None)
        assert result["type"] == "None"

    def test_point_position(self) -> None:
        pt = _make_point()
        result = _serialize_position(pt)
        assert result["type"] == "Point"
        assert result["primary_key"] == "CUST-0001"

    def test_polygon_position(self) -> None:
        poly = _make_polygon()
        result = _serialize_position(poly)
        assert result["type"] == "Polygon"

    def test_solid_position(self) -> None:
        solid = _make_solid()
        result = _serialize_position(solid)
        assert result["type"] == "Solid"


class TestSerializePolygonAnomalyConfidence:
    def test_omits_anomaly_confidence_when_zero(self) -> None:
        """anomaly_confidence=0.0 means bootstrap was skipped; field must be omitted from output."""
        poly = _make_polygon()
        poly.anomaly_confidence = 0.0
        result = _serialize_polygon(poly)
        assert "anomaly_confidence" not in result

    def test_omits_anomaly_confidence_when_none(self) -> None:
        """anomaly_confidence=None means bootstrap was skipped; field must be omitted."""
        poly = _make_polygon()
        poly.anomaly_confidence = None
        result = _serialize_polygon(poly)
        assert "anomaly_confidence" not in result

    def test_includes_anomaly_confidence_when_positive(self) -> None:
        """anomaly_confidence > 0 is a real calibrated value; must appear in output."""
        import pytest
        poly = _make_polygon()
        poly.anomaly_confidence = 0.85
        result = _serialize_polygon(poly)
        assert "anomaly_confidence" in result
        assert result["anomaly_confidence"] == pytest.approx(0.85, abs=0.001)
