# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared enrichment helper."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
from hypertopos.model.objects import Edge, Polygon
from hypertopos_mcp.enrichment import build_entity_lookups, enrich_polygon
from hypertopos_mcp.serializers import _serialize_polygon


def _make_polygon(bk: str = "CUST-0001") -> Polygon:
    edges = [
        Edge(line_id="products", point_key="PROD-001", status="alive", direction="out"),
        Edge(line_id="stores", point_key="STORE-01", status="alive", direction="out"),
        Edge(line_id="products", point_key="PROD-002", status="dead", direction="out"),
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
        is_anomaly=False,
        edges=edges,
        last_refresh_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _customers_table() -> pa.Table:
    return pa.table(
        {
            "primary_key": ["CUST-0001", "CUST-0002"],
            "version": [1, 1],
            "status": ["active", "active"],
            "name": ["Alice", "Bob"],
            "region": ["EMEA", "AMER"],
            "created_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
            "changed_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
        }
    )


def _products_table() -> pa.Table:
    return pa.table(
        {
            "primary_key": ["PROD-001", "PROD-002"],
            "version": [1, 1],
            "status": ["active", "active"],
            "name": ["Keyboard", "Mouse"],
            "category": ["Peripherals", "Peripherals"],
            "price": [99.99, 49.99],
            "created_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
            "changed_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
        }
    )


def _stores_table() -> pa.Table:
    return pa.table(
        {
            "primary_key": ["STORE-01", "STORE-02"],
            "version": [1, 1],
            "status": ["active", "active"],
            "name": ["Downtown Mall", "Uptown Plaza"],
            "city": ["Warsaw", "Krakow"],
            "created_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
            "changed_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 2,
        }
    )


def _mock_sphere_and_reader():
    sphere = MagicMock()
    line_cust = MagicMock()
    line_cust.line_role = "anchor"
    line_cust.versions = [1]
    line_prod = MagicMock()
    line_prod.line_role = "anchor"
    line_prod.versions = [1]
    line_store = MagicMock()
    line_store.line_role = "anchor"
    line_store.versions = [1]
    sphere.lines = {
        "customers": line_cust,
        "products": line_prod,
        "stores": line_store,
    }

    reader = MagicMock()

    def _read_points(line_id, version):
        tables = {
            "customers": _customers_table(),
            "products": _products_table(),
            "stores": _stores_table(),
        }
        return tables[line_id]

    reader.read_points = MagicMock(side_effect=_read_points)
    return sphere, reader


class TestBuildEntityLookups:
    def test_returns_props_for_requested_lines(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        lookups = build_entity_lookups(reader, sphere, {"customers", "stores"})
        assert "customers" in lookups
        assert "stores" in lookups
        assert "products" not in lookups

    def test_customer_props_include_name_and_region(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        lookups = build_entity_lookups(reader, sphere, {"customers"})
        assert lookups["customers"]["CUST-0001"]["name"] == "Alice"
        assert lookups["customers"]["CUST-0001"]["region"] == "EMEA"

    def test_store_props_include_name_and_city(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        lookups = build_entity_lookups(reader, sphere, {"stores"})
        assert lookups["stores"]["STORE-01"]["name"] == "Downtown Mall"
        assert lookups["stores"]["STORE-01"]["city"] == "Warsaw"

    def test_skips_meta_columns(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        lookups = build_entity_lookups(reader, sphere, {"customers"})
        props = lookups["customers"]["CUST-0001"]
        assert "primary_key" not in props
        assert "version" not in props
        assert "status" not in props
        assert "created_at" not in props
        assert "changed_at" not in props

    def test_reads_only_requested_lines(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        build_entity_lookups(reader, sphere, {"stores"})
        reader.read_points.assert_called_once_with("stores", 1)

    def test_empty_line_ids_returns_empty(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        lookups = build_entity_lookups(reader, sphere, set())
        assert lookups == {}

    def test_unknown_line_id_skipped(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        lookups = build_entity_lookups(reader, sphere, {"nonexistent"})
        assert lookups == {}


class TestBuildEntityLookupsEvent:
    def test_event_line_included(self) -> None:
        sphere, reader = _mock_sphere_and_reader()
        event_line = MagicMock()
        event_line.line_role = "event"
        event_line.versions = [1]
        sphere.lines["sales_values"] = event_line

        event_table = pa.table(
            {
                "primary_key": ["SALE-001"],
                "version": [1],
                "status": ["active"],
                "amount": [150.0],
                "quantity": [3],
                "created_at": [datetime(2024, 1, 1, tzinfo=UTC)],
                "changed_at": [datetime(2024, 1, 1, tzinfo=UTC)],
            }
        )
        original_side_effect = reader.read_points.side_effect

        def _read_points_with_event(line_id, version):
            if line_id == "sales_values":
                return event_table
            return original_side_effect(line_id, version)

        reader.read_points = MagicMock(side_effect=_read_points_with_event)
        lookups = build_entity_lookups(reader, sphere, {"sales_values"})
        assert lookups["sales_values"]["SALE-001"]["amount"] == 150.0
        assert lookups["sales_values"]["SALE-001"]["quantity"] == 3


class TestEnrichPolygon:
    def test_enriches_edges_with_names(self) -> None:
        poly = _make_polygon()
        sp = _serialize_polygon(poly)
        lookups = {
            "products": {"PROD-001": {"name": "Keyboard", "category": "Peripherals"}},
            "stores": {"STORE-01": {"name": "Downtown Mall", "city": "Warsaw"}},
        }
        enriched = enrich_polygon(sp, lookups, entity_line_id="customers")
        prod_edge = next(e for e in enriched["edges"] if e["point_key"] == "PROD-001")
        assert prod_edge["name"] == "Keyboard"
        assert prod_edge["category"] == "Peripherals"
        store_edge = next(e for e in enriched["edges"] if e["point_key"] == "STORE-01")
        assert store_edge["name"] == "Downtown Mall"
        assert store_edge["city"] == "Warsaw"

    def test_enriches_entity_properties(self) -> None:
        poly = _make_polygon("CUST-0001")
        sp = _serialize_polygon(poly)
        lookups = {
            "customers": {"CUST-0001": {"name": "Alice", "region": "EMEA"}},
            "products": {"PROD-001": {"name": "Keyboard"}},
            "stores": {"STORE-01": {"name": "Downtown Mall"}},
        }
        enriched = enrich_polygon(sp, lookups, entity_line_id="customers")
        assert enriched["properties"]["name"] == "Alice"
        assert enriched["properties"]["region"] == "EMEA"

    def test_missing_lookup_no_crash(self) -> None:
        poly = _make_polygon()
        sp = _serialize_polygon(poly)
        enriched = enrich_polygon(sp, {}, entity_line_id="customers")
        assert "properties" not in enriched or enriched.get("properties") == {}
        for e in enriched["edges"]:
            assert "name" not in e

    def test_partial_lookup(self) -> None:
        poly = _make_polygon()
        sp = _serialize_polygon(poly)
        lookups = {
            "stores": {"STORE-01": {"name": "Downtown Mall"}},
        }
        enriched = enrich_polygon(sp, lookups, entity_line_id="customers")
        store_edge = next(e for e in enriched["edges"] if e["point_key"] == "STORE-01")
        assert store_edge["name"] == "Downtown Mall"
        prod_edge = next(e for e in enriched["edges"] if e["point_key"] == "PROD-001")
        assert "name" not in prod_edge
