# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared enrichment helper."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from hypertopos.model.objects import Edge, Polygon

from hypertopos_mcp.enrichment import enrich_polygon
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
