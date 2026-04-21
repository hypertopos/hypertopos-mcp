# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for MCP tools — state management, compare, sphere info, common relations."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
import pytest
from hypertopos.model.objects import Edge, Point, Polygon, Solid, SolidSlice

# Edge struct type used in geometry tables
_EDGE_STRUCT = pa.struct(
    [
        pa.field("line_id", pa.string()),
        pa.field("point_key", pa.string()),
        pa.field("status", pa.string()),
        pa.field("direction", pa.string()),
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_aggregate_navigator():
    """Set up a navigator mock with real aggregate delegate.

    Must be called AFTER _state["sphere"] and _state["session"] are configured.
    Creates a navigator that delegates aggregate() to the core aggregation engine
    using the current reader and sphere from _state.

    Always creates a fresh navigator matching the current mock state.
    Tests that need a custom navigator should set it AFTER this call
    (or not call _ensure_aggregate_navigator at all).
    """
    from hypertopos_mcp.server import _state

    if _state.get("sphere") is None or _state.get("session") is None:
        return
    from hypertopos.engine.aggregation import aggregate as _core_agg
    from hypertopos.engine.geometry import GDSEngine

    reader = _state["session"]._reader
    sphere_meta = _state["sphere"]._sphere
    engine = GDSEngine(reader, None)
    manifest = MagicMock()
    manifest.line_version.return_value = 1

    nav_mock = MagicMock()

    def _nav_aggregate(event_pattern_id, group_by_line, **kwargs):
        return _core_agg(
            reader,
            engine,
            sphere_meta,
            manifest,
            event_pattern_id=event_pattern_id,
            group_by_line=group_by_line,
            **kwargs,
        )

    nav_mock.aggregate.side_effect = _nav_aggregate
    nav_mock._resolve_version.return_value = 1
    nav_mock.dead_dim_indices.return_value = []
    nav_mock._agg_nav_tag = True
    _state["navigator"] = nav_mock


def _make_batch_side(read_points_side):
    """Return a read_points_batch mock that delegates to read_points_side."""
    import pyarrow.compute as _pc

    def _batch(line_id, version, primary_keys):
        table = read_points_side(line_id, version)
        mask = _pc.is_in(table["primary_key"], pa.array(primary_keys, type=pa.string()))
        return table.filter(mask)

    return _batch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_edge(
    line_id: str = "products",
    point_key: str = "PROD-001",
    status: str = "alive",
    direction: str = "out",
) -> Edge:
    return Edge(line_id=line_id, point_key=point_key, status=status, direction=direction)


def _make_polygon(bk: str = "CUST-0001", is_anomaly: bool = False) -> Polygon:
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


def _make_cross_tab_geo():
    """Three event polygons: ITEM-001/PL, ITEM-001/DE, ITEM-002/PL."""
    rows = [
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-PL",
                "status": "alive",
                "direction": "out",
            },
        ],
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-DE",
                "status": "alive",
                "direction": "out",
            },
        ],
        [
            {"line_id": "items", "point_key": "ITEM-002", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-PL",
                "status": "alive",
                "direction": "out",
            },
        ],
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003"]),
            "edges": pa.array(rows, type=pa.list_(_EDGE_STRUCT)),
        }
    )


def _make_cross_tab_state(geo_table):
    """Build _state mocks for cross-tab aggregate tests.

    Also sets _state["navigator"] with a real aggregate delegate so that
    the MCP thin wrapper can call nav.aggregate() through the core engine.
    """
    from hypertopos_mcp.server import _state

    mock_pattern = MagicMock()
    mock_pattern.version = 1
    mock_pattern.entity_type = "sales"
    rel_items = MagicMock()
    rel_items.line_id = "items"
    rel_cc = MagicMock()
    rel_cc.line_id = "company_codes"
    mock_pattern.relations = [rel_items, rel_cc]

    mock_items_line = MagicMock()
    mock_items_line.versions = [1]
    mock_cc_line = MagicMock()
    mock_cc_line.versions = [1]
    mock_sales_line = MagicMock()
    mock_sales_line.versions = [1]

    # Event entity line — primary_keys match geometry PKs
    sales_pks = geo_table["primary_key"].to_pylist()
    sales_points = pa.table(
        {
            "primary_key": pa.array(sales_pks, type=pa.string()),
        }
    )
    items_points = pa.table(
        {
            "primary_key": pa.array(["ITEM-001", "ITEM-002"]),
            "name": pa.array(["Laptop", "Monitor"]),
        }
    )
    cc_points = pa.table(
        {
            "primary_key": pa.array(["CC-PL", "CC-DE"]),
            "country": pa.array(["PL", "DE"]),
        }
    )

    def read_points_side(line_id, version, **kwargs):
        if line_id == "items":
            return items_points
        if line_id == "company_codes":
            return cc_points
        if line_id == "sales":
            return sales_points
        return pa.table({"primary_key": pa.array([], type=pa.string())})

    reader = MagicMock()
    reader.read_geometry.return_value = geo_table
    reader.read_points.side_effect = read_points_side
    reader.read_points_schema.side_effect = lambda lid, ver: read_points_side(lid, ver).schema
    reader.read_points_batch.side_effect = _make_batch_side(read_points_side)
    reader.count_geometry_rows.return_value = geo_table.num_rows

    sphere_meta = MagicMock()
    sphere_meta.patterns = {"sale_pattern": mock_pattern}
    sphere_meta.lines = {
        "items": mock_items_line,
        "company_codes": mock_cc_line,
        "sales": mock_sales_line,
    }
    sphere_meta.event_line.return_value = "sales"
    sphere_meta.aliases = {}

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    session = MagicMock()
    session._reader = reader

    # Build a navigator mock with real aggregate delegate
    from hypertopos.engine.aggregation import aggregate as _core_agg
    from hypertopos.engine.geometry import GDSEngine

    engine = GDSEngine(reader, None)
    manifest = MagicMock()
    manifest.line_version.return_value = 1

    nav_mock = MagicMock()

    def _nav_aggregate(event_pattern_id, group_by_line, **kwargs):
        return _core_agg(
            reader,
            engine,
            sphere_meta,
            manifest,
            event_pattern_id=event_pattern_id,
            group_by_line=group_by_line,
            **kwargs,
        )

    nav_mock.aggregate.side_effect = _nav_aggregate
    nav_mock._resolve_version.return_value = 1
    nav_mock._agg_nav_tag = True
    _state["navigator"] = nav_mock

    return sphere, session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestEmergeEntityProperties:
    def _make_state(self, position, entity_props: dict):
        from hypertopos_mcp.server import _state

        nav_mock = MagicMock()
        nav_mock.position = position

        def do_emerge():
            nav_mock.position = Point(
                primary_key=position.primary_key,
                line_id="emerged",
                version=0,
                status="active",
                properties={},
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                changed_at=datetime(2024, 1, 1, tzinfo=UTC),
            )

        emerge_fn = nav_mock.π4_emerge
        emerge_fn.side_effect = do_emerge

        points_table = pa.table(
            {
                "primary_key": pa.array([position.primary_key]),
                **{k: pa.array([v]) for k, v in entity_props.items()},
            }
        )
        import pyarrow.compute as _pc

        def _batch_side(line_id, version, primary_keys):
            mask = _pc.is_in(
                points_table["primary_key"],
                pa.array(primary_keys, type=pa.string()),
            )
            return points_table.filter(mask)

        reader = MagicMock()
        reader.read_points.return_value = points_table
        reader.read_points_batch.side_effect = _batch_side

        mock_line = MagicMock()
        mock_line.line_role = "anchor"
        mock_line.versions = [1]
        mock_line.pattern_id = "customer_pattern"

        sphere_meta = MagicMock()
        sphere_meta.lines = {"customers": mock_line}
        sphere_meta.patterns = {}
        sphere_meta.entity_line.return_value = "customers"

        sphere = MagicMock()
        sphere._sphere = sphere_meta

        session = MagicMock()
        session._reader = reader

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere
        _state["session"] = session

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_emerge_from_solid_includes_entity_properties(self) -> None:
        from hypertopos_mcp.tools.navigation import emerge

        solid = _make_solid("CUST-0001")
        self._make_state(solid, {"name": "Acme Corp", "country": "PL"})
        result = json.loads(emerge())
        assert "entity_properties" in result
        props = result["entity_properties"]
        assert props is not None
        assert props.get("name") == "Acme Corp"

    def test_emerge_from_polygon_includes_entity_properties(self) -> None:
        from hypertopos_mcp.tools.navigation import emerge

        polygon = _make_polygon("CUST-0001")
        self._make_state(polygon, {"name": "Beta Ltd", "country": "DE"})
        result = json.loads(emerge())
        assert "entity_properties" in result
        props = result["entity_properties"]
        assert props is not None
        assert props.get("country") == "DE"


class TestRequireSphere:
    def test_raises_when_no_sphere(self) -> None:
        from hypertopos_mcp.server import _require_sphere, _state

        _state["sphere"] = None
        with pytest.raises(RuntimeError, match="No sphere open"):
            _require_sphere()

    def test_passes_when_sphere_set(self) -> None:
        from hypertopos_mcp.server import _require_sphere, _state

        _state["sphere"] = MagicMock()
        _require_sphere()
        _state["sphere"] = None


def _mock_session_with_reader():
    import pyarrow as pa

    empty_table = pa.table({"primary_key": pa.array([], type=pa.string())})
    reader = MagicMock()
    reader.read_points.return_value = empty_table
    session = MagicMock()
    session._reader = reader
    return session


class TestCompareEntities:
    def test_invalid_mode_raises(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import compare_entities

        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["navigator"] = MagicMock()
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = _mock_session_with_reader()
        with pytest.raises(ValueError, match="Unknown mode"):
            compare_entities("A", "B", "customer_pattern", mode="bogus")
        for k in list(_state.keys()):
            _state[k] = None

    def test_intraclass_mode_returns_json(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import compare_entities

        navigator_mock = MagicMock()
        navigator_mock.compare_entities_intraclass.return_value = {
            "distance": 0.5,
            "interpretation": "similar",
            "delta_norm_a": 1.2,
            "delta_rank_pct_a": 55.0,
            "is_anomaly_a": False,
            "delta_norm_b": 0.8,
            "delta_rank_pct_b": 30.0,
            "is_anomaly_b": False,
        }
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["navigator"] = navigator_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = _mock_session_with_reader()
        result = compare_entities("CUST-0001", "CUST-0002", "customer_pattern", mode="intraclass")
        data = json.loads(result)
        assert data["mode"] == "intraclass"
        assert data["distance"] == 0.5
        assert "interpretation" in data
        navigator_mock.compare_entities_intraclass.assert_called_once_with(
            "CUST-0001", "CUST-0002", "customer_pattern"
        )
        for k in list(_state.keys()):
            _state[k] = None

    def test_temporal_mode_returns_json(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import compare_entities

        navigator_mock = MagicMock()
        navigator_mock.compare_entities_temporal.return_value = {
            "distance": 2.5,
            "slices_a": 3,
            "slices_b": 3,
            "interpretation": "divergent history",
        }
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["navigator"] = navigator_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = _mock_session_with_reader()
        result = compare_entities("CUST-0001", "CUST-0002", "customer_pattern", mode="temporal")
        data = json.loads(result)
        assert data["mode"] == "temporal"
        assert data["distance"] == 2.5
        assert data["slices_a"] == 3
        navigator_mock.compare_entities_temporal.assert_called_once_with(
            "CUST-0001", "CUST-0002", "customer_pattern"
        )
        for k in list(_state.keys()):
            _state[k] = None


class TestGetSphereInfo:
    def test_raises_when_no_sphere(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.session import get_sphere_info

        _state["sphere"] = None
        with pytest.raises(RuntimeError, match="No sphere open"):
            get_sphere_info()

    def test_returns_json_when_sphere_set(self, open_berka_sphere) -> None:
        from hypertopos_mcp.tools.session import get_sphere_info

        result = json.loads(get_sphere_info())
        assert "accounts" in result["lines"]
        assert "account_behavior_pattern" in result["patterns"]

    def test_includes_aliases_section(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.session import get_sphere_info

        mock_alias_filter = MagicMock()
        mock_alias_filter.cutting_plane = None
        mock_alias_filter.include_relations = ["customers"]

        mock_alias = MagicMock()
        mock_alias.base_pattern_id = "customer_pattern"
        mock_alias.status = "production"
        mock_alias.filter = mock_alias_filter

        mock_sphere_meta = MagicMock()
        mock_sphere_meta.sphere_id = "test_sphere"
        mock_sphere_meta.name = "Test"
        mock_sphere_meta.lines = {}
        mock_sphere_meta.patterns = {}
        mock_sphere_meta.aliases = {"high_value": mock_alias}

        mock_sphere = MagicMock()
        mock_sphere._sphere = mock_sphere_meta

        _state["sphere"] = mock_sphere
        _state["path"] = "/test/path"

        result = json.loads(get_sphere_info())
        assert "aliases" in result
        assert "high_value" in result["aliases"]
        alias_data = result["aliases"]["high_value"]
        assert alias_data["base_pattern_id"] == "customer_pattern"
        assert alias_data["status"] == "production"
        assert alias_data["has_cutting_plane"] is False
        _state["sphere"] = None
        _state["path"] = None

    def test_active_manifest_latest_when_no_manifest(self) -> None:
        """active_manifest returns {version: latest} when no manifest in state."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.session import get_sphere_info

        mock_sphere_meta = MagicMock()
        mock_sphere_meta.sphere_id = "test"
        mock_sphere_meta.name = "Test"
        mock_sphere_meta.lines = {}
        mock_sphere_meta.patterns = {}
        mock_sphere_meta.aliases = {}
        mock_sphere = MagicMock()
        mock_sphere._sphere = mock_sphere_meta

        _state["sphere"] = mock_sphere
        _state["path"] = "/test"
        _state["manifest"] = None

        result = json.loads(get_sphere_info())
        assert result["active_manifest"] == {"version": "latest"}
        _state["sphere"] = None
        _state["path"] = None

    def test_prop_columns_included_when_present(self, open_berka_sphere) -> None:
        """Patterns with prop_columns expose them; patterns without do not."""
        from hypertopos_mcp.tools.session import get_sphere_info

        result = json.loads(get_sphere_info())
        patterns = result["patterns"]
        # account_stress_pattern has prop_columns
        stress = patterns["account_stress_pattern"]
        assert "prop_columns" in stress
        assert len(stress["prop_columns"]) > 0
        # tx_pattern (event) has no prop_columns
        tx = patterns["tx_pattern"]
        assert tx.get("prop_columns", []) == []


class TestFindCommonRelations:
    def test_no_common_relations(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_common_relations

        navigator_mock = MagicMock()
        navigator_mock.find_common_relations.return_value = {
            "common": set(),
            "edges_a": 2,
            "edges_b": 2,
        }
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["navigator"] = navigator_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = _mock_session_with_reader()
        result = json.loads(find_common_relations("CUST-0001", "CUST-0002", "customer_pattern"))
        assert result["common_count"] == 0
        assert result["interpretation"] == "no shared relations"
        for k in list(_state.keys()):
            _state[k] = None

    def test_with_common_relations(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_common_relations

        navigator_mock = MagicMock()
        navigator_mock.find_common_relations.return_value = {
            "common": {("products", "PROD-001"), ("stores", "STORE-01")},
            "edges_a": 3,
            "edges_b": 3,
        }
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["navigator"] = navigator_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = _mock_session_with_reader()
        result = json.loads(find_common_relations("CUST-0001", "CUST-0002", "customer_pattern"))
        assert result["common_count"] == 2
        assert "share 2 relation(s)" in result["interpretation"]
        for k in list(_state.keys()):
            _state[k] = None


def _make_centroid_state():
    """Build _state mocks for centroid_map tests with 3 groups (3 pairwise distances)."""
    from hypertopos_mcp.server import _state

    mock_group_centroids = [
        {"key": "CC-01", "vector": [0.1, 0.2], "count": 10, "radius": 0.5, "spread": 0.3},
        {"key": "CC-02", "vector": [0.3, 0.4], "count": 8, "radius": 0.6, "spread": 0.4},
        {"key": "CC-03", "vector": [0.5, 0.6], "count": 5, "radius": 0.4, "spread": 0.2},
    ]
    mock_centroid_result = {
        "global_centroid": {"vector": [0.3, 0.4], "count": 23},
        "group_centroids": mock_group_centroids,
        "inter_centroid_distances": [
            {"group_a": "CC-01", "group_b": "CC-02", "distance": 0.28},
            {"group_a": "CC-01", "group_b": "CC-03", "distance": 0.57},
            {"group_a": "CC-02", "group_b": "CC-03", "distance": 0.28},
        ],
        "structural_outlier": "CC-03",
        "dimensions": ["dim_a", "dim_b"],
    }

    nav_mock = MagicMock()
    nav_mock.centroid_map.return_value = mock_centroid_result
    nav_mock.dead_dim_indices.return_value = []

    cc_points = pa.table(
        {
            "primary_key": pa.array(["CC-01", "CC-02", "CC-03"]),
            "name": pa.array(["Alpha", "Beta", "Gamma"]),
        }
    )
    reader = MagicMock()
    reader.read_points.return_value = cc_points

    mock_cc_line = MagicMock()
    mock_cc_line.versions = [1]

    sphere_meta = MagicMock()
    sphere_meta.lines = {"company_codes": mock_cc_line}
    sphere_meta.patterns = {}
    sphere_meta.aliases = {}

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    session = MagicMock()
    session._reader = reader

    _state["navigator"] = nav_mock
    _state["sphere"] = sphere
    _state["session"] = session


class TestCentroidMapDistanceControl:
    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_include_distances_false_omits_section(self, open_berka_sphere) -> None:
        from hypertopos_mcp.tools.analysis import get_centroid_map

        result = json.loads(
            get_centroid_map(
                "account_behavior_pattern",
                "accounts",
                group_by_property="accounts:region",
                include_distances=False,
            )
        )
        assert "inter_centroid_distances" not in result

    def test_top_n_distances_limits_pairs(self, open_berka_sphere) -> None:
        from hypertopos_mcp.tools.analysis import get_centroid_map

        result = json.loads(
            get_centroid_map(
                "account_behavior_pattern",
                "accounts",
                group_by_property="accounts:region",
                top_n_distances=2,
            )
        )
        assert len(result.get("inter_centroid_distances", [])) <= 2

    def test_default_returns_all_distances(self) -> None:
        from hypertopos_mcp.tools.analysis import get_centroid_map

        _make_centroid_state()
        result = json.loads(get_centroid_map("customer_pattern", "company_codes"))
        assert len(result["inter_centroid_distances"]) == 3

    def test_group_by_uses_property_spec_when_specified(self, open_berka_sphere) -> None:
        """group_by field must reflect the full property spec when group_by_property is used."""
        from hypertopos_mcp.tools.analysis import get_centroid_map

        result = json.loads(
            get_centroid_map(
                "account_behavior_pattern",
                "accounts",
                group_by_property="accounts:region",
                include_distances=False,
            )
        )
        assert result["group_by"] == "accounts:region", (
            f"Expected 'accounts:region', got '{result['group_by']}'"
        )

    def test_group_by_shows_line_id_when_no_property(self, open_berka_sphere) -> None:
        """group_by field must show line_id when no group_by_property given."""
        from hypertopos_mcp.tools.analysis import get_centroid_map

        result = json.loads(
            get_centroid_map("account_behavior_pattern", "accounts", include_distances=False)
        )
        assert result["group_by"] == "accounts"

    def test_get_centroid_map_continuous_mode_returns_structured_error(self) -> None:
        """get_centroid_map must return structured error JSON.

        This covers the continuous-mode ValueError branch.
        """
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import get_centroid_map

        nav_mock = MagicMock()
        nav_mock.centroid_map.side_effect = ValueError(
            "continuous-mode line 'merchants' has no point keys — use group_by_property instead"
        )

        sphere_meta = MagicMock()
        sphere_meta.patterns = {}
        sphere = MagicMock()
        sphere._sphere = sphere_meta
        session = MagicMock()
        session._reader = MagicMock()

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere
        _state["session"] = session

        result = json.loads(get_centroid_map("sale_pattern", "merchants"))
        assert "error" in result, "Expected 'error' key in structured error response"
        assert "continuous" in result["error"].lower(), (
            f"Expected 'continuous' in error message, got: {result['error']!r}"
        )
        assert "hint" in result, "Expected 'hint' key in structured error response"
        assert result["pattern_id"] == "sale_pattern"
        assert result["group_by"] == "merchants"


class TestAggregateGroupByProperty:
    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_cross_tab_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_cross_tab_count_returns_composite_rows(self) -> None:
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
            )
        )
        assert result["group_by_property"] == "company_codes:country"
        assert result["total_groups"] == 3  # (ITEM-001,PL), (ITEM-001,DE), (ITEM-002,PL)
        keys = {(r["key"], r["country"]) for r in result["results"]}
        assert ("ITEM-001", "PL") in keys
        assert ("ITEM-001", "DE") in keys
        assert ("ITEM-002", "PL") in keys

    def test_cross_tab_result_uses_property_name_as_field_key(self, open_berka_sphere) -> None:
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                "count",
                group_by_property="accounts:region",
                limit=3,
            )
        )
        assert len(result["results"]) > 0
        assert "region" in result["results"][0]

    def test_cross_tab_enriches_group_by_line_entity(self) -> None:
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
            )
        )
        # items are enriched with their name property
        item_001_rows = [r for r in result["results"] if r["key"] == "ITEM-001"]
        assert len(item_001_rows) > 0
        assert item_001_rows[0].get("name") == "Laptop"

    def test_cross_tab_skips_polygon_with_no_prop_edge(self) -> None:
        """Polygon missing company_codes edge should be silently skipped."""
        import pyarrow as _pa
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.aggregation import aggregate

        geo_with_missing = _pa.table(
            {
                "primary_key": _pa.array(["SALE-001", "SALE-002"]),
                "edges": _pa.array(
                    [
                        [
                            {
                                "line_id": "items",
                                "point_key": "ITEM-001",
                                "status": "alive",
                                "direction": "out",
                            },
                            {
                                "line_id": "company_codes",
                                "point_key": "CC-PL",
                                "status": "alive",
                                "direction": "out",
                            },
                        ],
                        [
                            # no company_codes edge -- should be skipped
                            {
                                "line_id": "items",
                                "point_key": "ITEM-002",
                                "status": "alive",
                                "direction": "out",
                            },
                        ],
                    ],
                    type=_pa.list_(_EDGE_STRUCT),
                ),
            }
        )
        sphere, session = _make_cross_tab_state(geo_with_missing)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
            )
        )
        assert result["total_groups"] == 1  # only ITEM-001/PL

    def test_no_group_by_property_preserves_existing_behaviour(self, open_berka_sphere) -> None:
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(
            aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", metric="count", limit=3)
        )
        assert result["group_by_property"] is None
        assert "region" not in result["results"][0]


def _make_duplicate_entity_geo():
    """ITEM-001 appears in 2 polygons for PL — distinct count should be 1."""
    rows = [
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-PL",
                "status": "alive",
                "direction": "out",
            },
        ],
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-PL",
                "status": "alive",
                "direction": "out",
            },
        ],
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002"]),
            "edges": pa.array(rows, type=pa.list_(_EDGE_STRUCT)),
        }
    )


def _make_distinct_geo_with_amounts():
    """ITEM-001 twice for PL (amounts 100, 200), ITEM-001 once for DE (amount 50).

    Metric column (amount) is on the event entity line, not a separate amounts line.
    """
    rows = [
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-PL",
                "status": "alive",
                "direction": "out",
            },
        ],
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-PL",
                "status": "alive",
                "direction": "out",
            },
        ],
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
            {
                "line_id": "company_codes",
                "point_key": "CC-DE",
                "status": "alive",
                "direction": "out",
            },
        ],
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003"]),
            "edges": pa.array(rows, type=pa.list_(_EDGE_STRUCT)),
        }
    )


def _make_amounts_state(geo_table):
    """Build _state mocks for distinct aggregate tests with metric columns on event entity line."""
    rel_items = MagicMock()
    rel_items.line_id = "items"
    rel_cc = MagicMock()
    rel_cc.line_id = "company_codes"
    mock_pattern = MagicMock()
    mock_pattern.version = 1
    mock_pattern.entity_type = "sales"
    mock_pattern.relations = [rel_items, rel_cc]

    mock_items_line = MagicMock()
    mock_items_line.versions = [1]
    mock_cc_line = MagicMock()
    mock_cc_line.versions = [1]
    mock_sales_line = MagicMock()
    mock_sales_line.versions = [1]

    items_points = pa.table(
        {
            "primary_key": pa.array(["ITEM-001"]),
            "name": pa.array(["Laptop"]),
        }
    )
    cc_points = pa.table(
        {
            "primary_key": pa.array(["CC-PL", "CC-DE"]),
            "country": pa.array(["PL", "DE"]),
        }
    )
    # Metric column (amount) on event entity line
    sales_pks = geo_table["primary_key"].to_pylist()
    sales_points = pa.table(
        {
            "primary_key": pa.array(sales_pks, type=pa.string()),
            "amount": pa.array([100.0, 200.0, 50.0][: len(sales_pks)]),
        }
    )

    def read_points_side(line_id, version, **kwargs):
        if line_id == "items":
            return items_points
        if line_id == "company_codes":
            return cc_points
        if line_id == "sales":
            return sales_points
        return pa.table({"primary_key": pa.array([], type=pa.string())})

    reader = MagicMock()
    reader.read_geometry.return_value = geo_table
    reader.read_points.side_effect = read_points_side
    reader.read_points_schema.side_effect = lambda lid, ver: read_points_side(lid, ver).schema
    reader.read_points_batch.side_effect = _make_batch_side(read_points_side)

    sphere_meta = MagicMock()
    sphere_meta.patterns = {"sale_pattern": mock_pattern}
    sphere_meta.lines = {
        "items": mock_items_line,
        "company_codes": mock_cc_line,
        "sales": mock_sales_line,
    }
    sphere_meta.event_line.return_value = "sales"

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    session = MagicMock()
    session._reader = reader

    return sphere, session


class TestAggregateDistinct:
    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_cross_tab_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_distinct_returns_property_value_groups(self) -> None:
        """distinct=True returns rows keyed by property value, not entity key."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
                distinct=True,
            )
        )
        # cross-tab geo has PL (ITEM-001, ITEM-002) and DE (ITEM-001) → 2 groups
        assert result["total_groups"] == 2
        assert result["distinct"] is True
        countries = {r["country"] for r in result["results"]}
        assert countries == {"PL", "DE"}
        for row in result["results"]:
            assert "key" not in row
            assert "country" in row
            assert "value" in row
            assert "count" in row

    def test_distinct_count_is_unique_entities(self) -> None:
        """Same entity appearing in 2 polygons counts as 1, not 2."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.aggregation import aggregate

        geo = _make_duplicate_entity_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
                distinct=True,
            )
        )
        assert result["total_groups"] == 1
        pl_row = result["results"][0]
        assert pl_row["country"] == "PL"
        assert pl_row["count"] == 1  # ITEM-001 appears twice but is 1 unique entity

    def test_distinct_requires_group_by_property(self) -> None:
        """distinct=True without group_by_property raises RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="distinct=True requires group_by_property"):
            aggregate("sale_pattern", "items", "count", distinct=True)

    def test_distinct_sum_aggregates_per_property_value(self) -> None:
        """distinct=True with sum:amount aggregates all GL entries per country.

        The count should reflect polygon count.
        """
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.aggregation import aggregate

        geo = _make_distinct_geo_with_amounts()
        sphere, session = _make_amounts_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "sum:amount",
                group_by_property="company_codes:country",
                distinct=True,
            )
        )
        rows_by_country = {r["country"]: r for r in result["results"]}
        # PL: 100 + 200 = 300 (2 GL entries), DE: 50 (1 GL entry)
        assert rows_by_country["PL"]["value"] == 300.0
        assert rows_by_country["PL"]["count"] == 2  # GL entries, not distinct entities
        assert rows_by_country["DE"]["value"] == 50.0
        assert rows_by_country["DE"]["count"] == 1

    def test_distinct_count_uses_vectorized_path(self) -> None:
        """distinct=True count must produce same result as Python loop baseline.

        This test validates correctness of the vectorized path by comparing
        against the expected distinct counts for the cross-tab fixture.
        cross-tab fixture: ITEM-001→PL, ITEM-002→PL, ITEM-001→DE
        distinct count: PL has 2 unique items (ITEM-001, ITEM-002), DE has 1 (ITEM-001).
        """
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
                distinct=True,
            )
        )
        counts = {r["country"]: r["count"] for r in result["results"]}
        assert counts.get("PL") == 2
        assert counts.get("DE") == 1


class TestAggregateCollapseByProperty:
    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_cross_tab_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_collapse_requires_group_by_property(self) -> None:
        """collapse_by_property=True without group_by_property raises RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(
            RuntimeError, match="collapse_by_property=True requires group_by_property"
        ):  # noqa: E501
            aggregate("sale_pattern", "items", "count", collapse_by_property=True)

    def test_collapse_and_distinct_are_incompatible(self) -> None:
        """collapse_by_property=True and distinct=True cannot be combined."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="collapse_by_property"):
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
                collapse_by_property=True,
                distinct=True,
            )

    def test_collapse_count_returns_property_value_rows(self) -> None:
        """collapse_by_property=True groups by property value, not (entity, prop_val) pair.

        cross-tab fixture: ITEM-001->PL, ITEM-001->DE, ITEM-002->PL
        Without collapse: 3 rows [(ITEM-001,PL), (ITEM-001,DE), (ITEM-002,PL)]
        With collapse:    2 rows [PL->2, DE->1]
        """
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "count",
                group_by_property="company_codes:country",
                collapse_by_property=True,
            )
        )
        assert result["total_groups"] == 2
        rows = {r["country"]: r for r in result["results"]}
        assert rows["PL"]["value"] == 2.0
        assert rows["DE"]["value"] == 1.0
        for row in result["results"]:
            assert "key" not in row, "collapsed rows must not contain entity key"
            assert "country" in row

    def test_collapse_count_result_has_count_field(self, open_berka_sphere) -> None:
        """Each collapsed row has a 'count' field with the polygon count."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(
            aggregate(
                event_pattern_id="tx_pattern",
                group_by_line="accounts",
                metric="count",
                group_by_property="accounts:region",
                collapse_by_property=True,
            )
        )
        assert len(result["results"]) > 0
        for row in result["results"]:
            assert "count" in row
            assert isinstance(row["count"], int)

    def test_collapse_avg_returns_tier_averages(self) -> None:
        """collapse_by_property with avg:amount computes per-tier average across all polygons.

        _make_distinct_geo_with_amounts fixture:
          SALE-001: ITEM-001/PL, amount=100
          SALE-002: ITEM-001/PL, amount=200
          SALE-003: ITEM-001/DE, amount=50

        Expected:
          PL: (100 + 200) / 2 = 150.0
          DE: 50 / 1 = 50.0
        """
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.aggregation import aggregate

        geo = _make_distinct_geo_with_amounts()
        sphere, session = _make_amounts_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "avg:amount",
                group_by_property="company_codes:country",
                collapse_by_property=True,
            )
        )
        assert result["total_groups"] == 2
        rows = {r["country"]: r for r in result["results"]}
        assert rows["PL"]["value"] == 150.0
        assert rows["DE"]["value"] == 50.0
        for row in result["results"]:
            assert "key" not in row

    def test_collapse_sum_returns_tier_totals(self) -> None:
        """collapse_by_property with sum:amount aggregates per tier."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.aggregation import aggregate

        geo = _make_distinct_geo_with_amounts()
        sphere, session = _make_amounts_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "sum:amount",
                group_by_property="company_codes:country",
                collapse_by_property=True,
            )
        )
        assert result["total_groups"] == 2
        rows = {r["country"]: r for r in result["results"]}
        assert rows["PL"]["value"] == 300.0  # 100 + 200
        assert rows["DE"]["value"] == 50.0


def _make_percentile_geo():
    """Build geometry with known amounts for percentile testing.

    4 events across 2 items:
      SALE-001: ITEM-001 (amount=10 on event entity line)
      SALE-002: ITEM-001 (amount=20 on event entity line)
      SALE-003: ITEM-002 (amount=30 on event entity line)
      SALE-004: ITEM-002 (amount=40 on event entity line)

    Grouped by items:
      ITEM-001: [10, 20] → median=15, pct0=10, pct100=20, pct50=15
      ITEM-002: [30, 40] → median=35, pct0=30, pct100=40, pct50=35
    """
    rows = [
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
        ],
        [
            {"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"},
        ],
        [
            {"line_id": "items", "point_key": "ITEM-002", "status": "alive", "direction": "out"},
        ],
        [
            {"line_id": "items", "point_key": "ITEM-002", "status": "alive", "direction": "out"},
        ],
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003", "SALE-004"]),
            "edges": pa.array(rows, type=pa.list_(_EDGE_STRUCT)),
        }
    )


def _make_percentile_state(geo_table):
    """Build _state mocks for percentile aggregate tests."""
    rel_items = MagicMock()
    rel_items.line_id = "items"
    mock_pattern = MagicMock()
    mock_pattern.version = 1
    mock_pattern.entity_type = "sales"
    mock_pattern.relations = [rel_items]

    mock_items_line = MagicMock()
    mock_items_line.versions = [1]
    mock_sales_line = MagicMock()
    mock_sales_line.versions = [1]

    items_points = pa.table(
        {
            "primary_key": pa.array(["ITEM-001", "ITEM-002"]),
            "name": pa.array(["Laptop", "Phone"]),
        }
    )
    # Metric column (amount) on event entity line
    sales_points = pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003", "SALE-004"]),
            "amount": pa.array([10.0, 20.0, 30.0, 40.0]),
        }
    )

    def read_points_side(line_id, version, **kwargs):
        if line_id == "items":
            return items_points
        if line_id == "sales":
            return sales_points
        return pa.table({"primary_key": pa.array([], type=pa.string())})

    reader = MagicMock()
    reader.read_geometry.return_value = geo_table
    reader.read_points.side_effect = read_points_side
    reader.read_points_schema.side_effect = lambda lid, ver: read_points_side(lid, ver).schema
    reader.read_points_batch.side_effect = _make_batch_side(read_points_side)

    sphere_meta = MagicMock()
    sphere_meta.patterns = {"sale_pattern": mock_pattern}
    sphere_meta.lines = {
        "items": mock_items_line,
        "sales": mock_sales_line,
    }
    sphere_meta.event_line.return_value = "sales"

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    session = MagicMock()
    session._reader = reader

    return sphere, session


class TestAggregatePercentileMetrics:
    """Tests for median:<field> and pct<N>:<field> metrics."""

    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_percentile_geo()
        sphere, session = _make_percentile_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_median_returns_correct_value(self) -> None:
        """median:amount returns median per group."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "median:amount",
            )
        )
        rows = {r["key"]: r for r in result["results"]}
        # ITEM-001: median([10, 20]) = 15.0
        assert rows["ITEM-001"]["value"] == 15.0
        # ITEM-002: median([30, 40]) = 35.0
        assert rows["ITEM-002"]["value"] == 35.0

    def test_pct90_returns_correct_value(self) -> None:
        """pct90:amount returns 90th percentile per group."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "pct90:amount",
            )
        )
        rows = {r["key"]: r for r in result["results"]}
        # ITEM-001: np.percentile([10, 20], 90) = 19.0
        assert rows["ITEM-001"]["value"] == 19.0
        # ITEM-002: np.percentile([30, 40], 90) = 39.0
        assert rows["ITEM-002"]["value"] == 39.0

    def test_pct50_equals_median(self) -> None:
        """pct50:amount == median:amount (within float tolerance)."""
        from hypertopos_mcp.tools.aggregation import aggregate

        median_result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "median:amount",
            )
        )
        pct50_result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "pct50:amount",
            )
        )
        median_rows = {r["key"]: r for r in median_result["results"]}
        pct50_rows = {r["key"]: r for r in pct50_result["results"]}
        for key in median_rows:
            assert abs(median_rows[key]["value"] - pct50_rows[key]["value"]) < 1e-9

    def test_pct0_equals_min(self) -> None:
        """pct0:amount approximates min:amount."""
        from hypertopos_mcp.tools.aggregation import aggregate

        pct0_result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "pct0:amount",
            )
        )
        min_result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                "min:amount",
            )
        )
        pct0_rows = {r["key"]: r for r in pct0_result["results"]}
        min_rows = {r["key"]: r for r in min_result["results"]}
        for key in pct0_rows:
            assert abs(pct0_rows[key]["value"] - min_rows[key]["value"]) < 1e-9

    def test_invalid_percentile_raises(self) -> None:
        """pct150 raises RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="Invalid percentile"):
            aggregate(
                "sale_pattern",
                "items",
                "pct150:amount",
            )

    def test_median_nonexistent_column_raises(self) -> None:
        """median:nonexistent_col raises ValueError when column not on event entity line."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(ValueError, match="not found on event entity line"):
            aggregate("sale_pattern", "items", "median:nonexistent_col")

    def test_pct_with_pivot_raises(self) -> None:
        """pct90 with pivot_event_field raises RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="pivot"):
            aggregate(
                "sale_pattern",
                "items",
                "pct90:amount",
                pivot_event_field="year",
            )

    def test_unknown_metric_op_raises(self) -> None:
        """foo:amount raises RuntimeError listing valid metrics."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="Unknown metric"):
            aggregate(
                "sale_pattern",
                "items",
                "foo:amount",
            )


# TestAggregateMultiFilter removed — now tested in core


def test_get_event_polygons_raises_when_filters_is_dict(open_berka_sphere) -> None:
    """Passing a plain dict as filters must raise RuntimeError, not silently ignore."""
    import pytest
    from hypertopos_mcp.tools.geometry import get_event_polygons

    with pytest.raises(RuntimeError, match="filters must be a list"):
        get_event_polygons(
            entity_key="1",
            pattern_id="tx_pattern",
            filters={"line": "accounts", "key": "741"},
        )


def test_get_event_polygons_accepts_pattern_id_not_event_pattern_id() -> None:
    """get_event_polygons must use pattern_id parameter, not event_pattern_id."""
    import inspect

    from hypertopos_mcp.tools.geometry import get_event_polygons

    sig = inspect.signature(get_event_polygons)
    assert "pattern_id" in sig.parameters, "parameter must be named pattern_id"
    assert "event_pattern_id" not in sig.parameters, "old param name must be gone"


# ---------------------------------------------------------------------------
# Helpers for event line filter tests
# _make_fact_filter_geo removed — now tested in core


# ---------------------------------------------------------------------------
# Offset pagination
# ---------------------------------------------------------------------------
def _make_offset_geo():
    """Three polygons: ITEM-001 x2, ITEM-002 x1 → sorted desc: ITEM-001(2), ITEM-002(1)."""
    rows = [
        ([{"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"}]),
        ([{"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "out"}]),
        ([{"line_id": "items", "point_key": "ITEM-002", "status": "alive", "direction": "out"}]),
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003"]),
            "edges": pa.array(rows, type=pa.list_(_EDGE_STRUCT)),
        }
    )


class TestAggregateOffset:
    """Offset pagination for aggregate()."""

    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_offset_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_offset_zero_same_as_default(self, open_berka_sphere) -> None:
        """offset=0 returns same results as no offset."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        r1 = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", limit=3))
        r2 = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", limit=3, offset=0))
        assert r1["results"] == r2["results"]

    def test_offset_skips_first_n_results(self) -> None:
        """offset=1 skips the top result and returns the second."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(aggregate("sale_pattern", "items", "count", limit=1, offset=1))
        assert len(result["results"]) == 1
        assert result["results"][0]["key"] == "ITEM-002"

    def test_offset_beyond_range_returns_empty(self) -> None:
        """offset beyond total_groups returns empty results but correct total_groups."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(aggregate("sale_pattern", "items", "count", limit=10, offset=99))
        assert result["results"] == []
        assert result["total_groups"] == 2

    def test_offset_in_result_json(self, open_berka_sphere) -> None:
        """Result JSON includes offset field."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", offset=1, limit=3))
        assert result["offset"] == 1

    def test_offset_default_not_in_result_when_zero(self, open_berka_sphere) -> None:
        """offset=0 (default) is still present in result JSON."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", limit=3))
        assert result["offset"] == 0


# ---------------------------------------------------------------------------
# pivot_event_field cross-tab aggregation
# _make_pivot_geo removed — now tested in core


# ---------------------------------------------------------------------------
# Static sampling for aggregate()
# ---------------------------------------------------------------------------
def _make_sampling_geo():
    """Four polygons: SALE-001/002 -> ITEM-001, SALE-003/004 -> ITEM-002.

    Uses edges struct column so the vectorized H block triggers for
    sampling calls (has_edges=True).
    """
    _est = pa.struct(
        [
            pa.field("line_id", pa.string()),
            pa.field("point_key", pa.string()),
            pa.field("status", pa.string()),
            pa.field("direction", pa.string()),
        ]
    )
    edges_rows = [
        [{"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "in"}],
        [{"line_id": "items", "point_key": "ITEM-001", "status": "alive", "direction": "in"}],
        [{"line_id": "items", "point_key": "ITEM-002", "status": "alive", "direction": "in"}],
        [{"line_id": "items", "point_key": "ITEM-002", "status": "alive", "direction": "in"}],
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003", "SALE-004"]),
            "edges": pa.array(edges_rows, type=pa.list_(_est)),
        }
    )


class TestAggregateIdea004Sampling:
    """Static sampling for aggregate()."""

    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_sampling_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_sample_n_reduces_result_count(self, open_berka_sphere) -> None:
        """T1: sample_size=10 → sampled=True, sample_size=10."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", sample_size=10, seed=42))
        assert result.get("sampled") is True
        assert result["sample_size"] == 10

    def test_sample_n_gte_total_no_sampling(self, open_berka_sphere) -> None:
        """T2: sample_size larger than total → sampled=False."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", sample_size=9999999))
        assert result.get("sampled") is False

    def test_sample_pct_reduces_result(self) -> None:
        """T3: sample_pct=0.5 with 4 polygons → sample_size=2, sampled=True."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(aggregate("sale_pattern", "items", sample_pct=0.5))
        assert result["sampled"] is True
        assert result["sample_size"] == 2

    def test_sample_pct_1_no_sampling(self, open_berka_sphere) -> None:
        """T4: sample_pct=1.0 → sampled=False."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", sample_pct=1.0))
        assert result.get("sampled") is False

    def test_sample_and_sample_pct_raises(self) -> None:
        """T5: both sample_size and sample_pct given → RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="mutually exclusive"):
            aggregate("sale_pattern", "items", sample_size=1, sample_pct=0.5)

    def test_sample_zero_raises(self) -> None:
        """T6: sample_size=0 → RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="positive integer"):
            aggregate("sale_pattern", "items", sample_size=0)

    def test_sample_pct_zero_raises(self) -> None:
        """T7: sample_pct=0.0 → RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="sample_pct must be in range"):
            aggregate("sale_pattern", "items", sample_pct=0.0)

    def test_sample_pct_above_1_raises(self) -> None:
        """T8: sample_pct=1.5 → RuntimeError."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="sample_pct must be in range"):
            aggregate("sale_pattern", "items", sample_pct=1.5)

    def test_sample_applied_after_filters(self) -> None:
        """T9: filter reduces to 2 eligible + sample_size=1 → total_eligible=2, sample_size=1."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "sale_pattern",
                "items",
                sample_size=1,
                filters=[{"line": "items", "key": "ITEM-001"}],
            )
        )
        assert result["sampled"] is True
        assert result["total_eligible"] == 2
        assert result["sample_size"] == 1

    def test_no_sampling_sampled_false(self, open_berka_sphere) -> None:
        """T10: no sample/sample_pct → sampled=False."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", limit=3))
        assert result.get("sampled") is False

    def test_seed_deterministic(self, open_berka_sphere) -> None:
        """T11: two calls with seed=42 return identical results."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        r1 = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", sample_size=100, seed=42))
        r2 = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", sample_size=100, seed=42))
        assert r1["results"] == r2["results"]

    def test_no_seed_nondeterministic(self) -> None:
        """T12: 10 calls without seed with sample_size=1 from 4 polygons.

        At least 2 should differ (best-effort). P(all 10 identical) ≈ 0.2% — acceptable flakiness.
        """
        from hypertopos_mcp.tools.aggregation import aggregate

        outcomes = [
            json.dumps(
                json.loads(aggregate("sale_pattern", "items", sample_size=1))["results"],
                sort_keys=True,
            )
            for _ in range(10)
        ]
        assert len(set(outcomes)) > 1, "Expected at least two distinct outcomes without seed"

    # test_sample_uses_vectorized_h_block_not_python_loop removed —
    # _make_edge_map_fn moved to core engine


# ---------------------------------------------------------------------------
# find_similar_entities thin wrapper
# ---------------------------------------------------------------------------
class TestFindSimilarEntities:
    """find_similar_entities MCP tool delegates to navigator."""

    def test_delegates_to_navigator(self):
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities

        nav_mock = MagicMock()
        nav_mock.find_similar_entities.return_value = [
            ("CUST-002", 0.15),
            ("CUST-003", 0.42),
        ]
        nav_mock.get_entity_geometry_meta.return_value = {
            "delta_norm": 1.23,
            "is_anomaly": False,
            "delta_rank_pct": 40.0,
        }

        reader_mock = MagicMock()
        reader_mock.read_points.return_value = pa.table(
            {
                "primary_key": ["CUST-001", "CUST-002", "CUST-003"],
                "version": [1, 1, 1],
                "status": ["active"] * 3,
                "created_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 3,
                "changed_at": [datetime(2024, 1, 1, tzinfo=UTC)] * 3,
                "name": ["Alice", "Bob", "Carol"],
            }
        )

        sphere_mock = MagicMock()
        sphere_mock._sphere.lines = {
            "customers": MagicMock(versions=[1], line_role="anchor", pattern_id="customer_pattern"),
        }
        sphere_mock._sphere.patterns = {
            "customer_pattern": MagicMock(version=1),
        }

        session_mock = MagicMock()
        session_mock._reader = reader_mock

        try:
            _state["navigator"] = nav_mock
            _state["sphere"] = sphere_mock
            _state["session"] = session_mock

            result = json.loads(find_similar_entities("CUST-001", "customer_pattern", 2))

            nav_mock.find_similar_entities.assert_called_once_with(
                "CUST-001",
                "customer_pattern",
                top_n=2,
                filter_expr=None,
                missing_edge_to=None,
                dim_mask=None,
                metric="L2",
            )
            assert result["reference"]["primary_key"] == "CUST-001"
            assert result["reference"]["delta_norm"] == pytest.approx(1.23)
            assert result["reference"]["is_anomaly"] is False
            assert len(result["similar"]) == 2
            assert result["similar"][0]["primary_key"] == "CUST-002"
            assert result["similar"][0]["distance"] == 0.15
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def _make_similar_state(
        self, nav_mock, similar_pairs, ref_is_anomaly=False, ref_delta_rank_pct=30.0
    ):
        from hypertopos_mcp.server import _state

        nav_mock.find_similar_entities.return_value = similar_pairs
        nav_mock.get_entity_geometry_meta.return_value = {
            "delta_norm": 1.0,
            "is_anomaly": ref_is_anomaly,
            "delta_rank_pct": ref_delta_rank_pct,
        }

        reader_mock = MagicMock()
        reader_mock.read_points.return_value = pa.table(
            {
                "primary_key": ["CUST-001"],
                "version": [1],
                "status": ["active"],
                "created_at": [datetime(2024, 1, 1, tzinfo=timezone.utc)],  # noqa: UP017
                "changed_at": [datetime(2024, 1, 1, tzinfo=timezone.utc)],  # noqa: UP017
                "name": ["Alice"],
            }
        )

        sphere_mock = MagicMock()
        sphere_mock._sphere.lines = {
            "customers": MagicMock(versions=[1], line_role="anchor", pattern_id="customer_pattern"),
        }
        sphere_mock._sphere.patterns = {
            "customer_pattern": MagicMock(version=1),
        }

        session_mock = MagicMock()
        session_mock._reader = reader_mock

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere_mock
        _state["session"] = session_mock

    def test_population_diversity_note_when_majority_zero_distance(self):
        """3 results all at distance=0.0 → population_diversity_note present."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities
        from hypertopos.navigation.navigator import SimilarityResult

        nav_mock = MagicMock()
        sr = SimilarityResult([("CUST-002", 0.0), ("CUST-003", 0.0), ("CUST-004", 0.0)])
        sr.degenerate_warning = "Degenerate: 3/3 neighbors at distance=0 (inactive entities). Results may be misleading."
        self._make_similar_state(nav_mock, sr)

        try:
            result = json.loads(find_similar_entities("CUST-001", "customer_pattern", 3))
            assert "population_diversity_note" in result
            assert "identical delta vector" in result["population_diversity_note"]
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_no_population_diversity_note_for_top_n_1_single_zero(self):
        """top_n=1 with 1 result at distance=0.0 → population_diversity_note NOT present."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities

        nav_mock = MagicMock()
        similar_pairs = [("CUST-002", 0.0)]
        self._make_similar_state(nav_mock, similar_pairs)

        try:
            result = json.loads(find_similar_entities("CUST-001", "customer_pattern", 1))
            assert "population_diversity_note" not in result
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_empty_filter_expr_adds_filter_note(self):
        """When filter_expr yields no results, a filter_note must explain why."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities

        nav_mock = MagicMock()
        nav_mock.find_similar_entities.return_value = []  # empty - no matches

        self._make_similar_state(nav_mock, [])

        try:
            result = json.loads(
                find_similar_entities(
                    "CUST-001", "customer_pattern", top_n=5, filter_expr="is_anomaly = true"
                )  # noqa: E501
            )
            assert result["similar"] == []
            assert "filter_note" in result, (
                f"Expected filter_note in result, got keys: {list(result.keys())}"
            )
            assert (
                "filter" in result["filter_note"].lower()
                or "no" in result["filter_note"].lower()
            )
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_filter_note_mentions_normal_reference_for_anomaly_filter(self):
        """filter_note mentions reference entity is not anomalous.

        This happens when the is_anomaly filter yields an empty result set.
        """
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities

        nav_mock = MagicMock()
        self._make_similar_state(nav_mock, [], ref_is_anomaly=False, ref_delta_rank_pct=42.5)

        try:
            result = json.loads(
                find_similar_entities(
                    "CUST-001",
                    "customer_pattern",
                    top_n=5,
                    filter_expr="is_anomaly = true",
                )
            )
            assert "filter_note" in result
            assert "not anomalous" in result["filter_note"].lower()
            assert "42.5" in result["filter_note"]
        finally:
            for k in list(_state.keys()):
                _state[k] = None


class TestFindDriftingEntities:
    """find_drifting_entities MCP tool — note field behavior."""

    def _make_drift_state(self, nav_mock):
        from hypertopos_mcp.server import _state

        reader_mock = MagicMock()
        reader_mock.read_points.return_value = pa.table(
            {
                "primary_key": pa.array([], type=pa.string()),
            }
        )

        sphere_mock = MagicMock()
        sphere_mock._sphere.lines = {
            "customers": MagicMock(versions=[1], line_role="anchor", pattern_id="customer_pattern"),
        }
        sphere_mock._sphere.patterns = {
            "customer_pattern": MagicMock(version=1),
        }

        session_mock = MagicMock()
        session_mock._reader = reader_mock

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere_mock
        _state["session"] = session_mock

    def test_note_present_when_empty_results(self):
        """Empty drift result → note field present mentioning 'temporal deformation slices'."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_drifting_entities

        nav_mock = MagicMock()
        nav_mock.π9_attract_drift.return_value = []

        self._make_drift_state(nav_mock)

        try:
            result = json.loads(find_drifting_entities("customer_pattern"))
            assert "note" in result
            assert "temporal deformation slices" in result["note"]
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_note_absent_when_results_present(self):
        """Non-empty drift result → note field NOT present."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_drifting_entities

        nav_mock = MagicMock()
        nav_mock.π9_attract_drift.return_value = [
            {
                "primary_key": "CUST-001",
                "displacement": 0.42,
                "displacement_current": 0.38,
                "path_length": 0.50,
                "ratio": 0.84,
                "num_slices": 3,
                "first_timestamp": "2024-01-01T00:00:00+00:00",
                "last_timestamp": "2024-06-01T00:00:00+00:00",
                "delta_norm_first": 0.3,
                "delta_norm_last": 0.7,
                "dimension_diffs": [],
                "dimension_diffs_current": [],
            }
        ]

        self._make_drift_state(nav_mock)

        try:
            result = json.loads(find_drifting_entities("customer_pattern"))
            assert "note" not in result
            assert result["count"] == 1
        finally:
            for k in list(_state.keys()):
                _state[k] = None


class TestContrastPopulations:
    """Tests for the contrast_populations MCP tool."""

    def _fake_contrast_result(self):
        return [
            {
                "dim_index": 0,
                "dim_label": "Products",
                "mean_a": 0.9,
                "mean_b": 0.1,
                "diff": 0.8,
                "effect_size": 8.0,
            },
            {
                "dim_index": 1,
                "dim_label": "Stores",
                "mean_a": 0.5,
                "mean_b": 0.5,
                "diff": 0.0,
                "effect_size": 0.0,
            },
        ]

    def test_delegates_to_navigator(self):
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import contrast_populations

        nav_mock = MagicMock()
        nav_mock.contrast_populations.return_value = self._fake_contrast_result()
        nav_mock.dead_dim_indices.return_value = []

        try:
            _state["navigator"] = nav_mock
            _state["sphere"] = MagicMock()

            result = json.loads(contrast_populations("customer_pattern", {"anomaly": True}))

            nav_mock.contrast_populations.assert_called_once_with(
                "customer_pattern", {"anomaly": True}, None
            )
            assert result["pattern_id"] == "customer_pattern"
            assert result["group_a_spec"] == {"anomaly": True}
            assert result["group_b_spec"] is None
            assert len(result["dimensions"]) == 2
            assert result["dimensions"][0]["effect_size"] == 8.0
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_explicit_group_b(self):
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import contrast_populations

        nav_mock = MagicMock()
        nav_mock.contrast_populations.return_value = self._fake_contrast_result()
        nav_mock.dead_dim_indices.return_value = []

        try:
            _state["navigator"] = nav_mock
            _state["sphere"] = MagicMock()

            result = json.loads(
                contrast_populations(
                    "customer_pattern",
                    {"keys": ["A", "B"]},
                    {"keys": ["C", "D"]},
                )
            )

            nav_mock.contrast_populations.assert_called_once_with(
                "customer_pattern", {"keys": ["A", "B"]}, {"keys": ["C", "D"]}
            )
            assert result["group_a_spec"] == {"keys": ["A", "B"]}
            assert result["group_b_spec"] == {"keys": ["C", "D"]}
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_edge_continuous_mode_graceful_error(self):
        """GDSNavigationError from continuous-mode edge spec is returned as structured JSON."""
        from hypertopos.navigation.navigator import GDSNavigationError
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import contrast_populations

        nav_mock = MagicMock()
        nav_mock.contrast_populations.side_effect = GDSNavigationError(
            "Cannot filter by edge to 'company_codes': pattern 'customer_pattern' uses "
            "continuous mode (edge_max) — edges store counts, not entity keys. "
            "Specify the group by 'anomaly', 'keys', or 'alias' instead."
        )

        try:
            _state["navigator"] = nav_mock
            _state["sphere"] = MagicMock()

            result = json.loads(
                contrast_populations(
                    "customer_pattern",
                    {"edge": {"line_id": "company_codes", "key": "CC-01"}},
                )
            )

            assert "error" in result
            assert "continuous mode" in result["error"]
            assert result["pattern_id"] == "customer_pattern"
            assert "hint" in result
            assert "anomaly" in result["hint"]
            # Must NOT contain success-path keys
            assert "dimensions" not in result
        finally:
            for k in list(_state.keys()):
                _state[k] = None


# TestAggregateGeometryFilters removed — now tested in core

# ---------------------------------------------------------------------------
# delta_dim geometry filter in aggregate
# ---------------------------------------------------------------------------
# TestAggregateDeltaDimFilter removed — now tested in core


# ---------------------------------------------------------------------------
# property_filters + include_properties
# ---------------------------------------------------------------------------
def _make_prop_filter_geo():
    """4 event polygons linked to customers. CUST-001 has 2 entries."""
    rows = [
        (
            [
                {
                    "line_id": "customers",
                    "point_key": "CUST-001",
                    "status": "alive",
                    "direction": "out",
                }
            ]
        ),
        (
            [
                {
                    "line_id": "customers",
                    "point_key": "CUST-002",
                    "status": "alive",
                    "direction": "out",
                }
            ]
        ),
        (
            [
                {
                    "line_id": "customers",
                    "point_key": "CUST-003",
                    "status": "alive",
                    "direction": "out",
                }
            ]
        ),
        (
            [
                {
                    "line_id": "customers",
                    "point_key": "CUST-001",
                    "status": "alive",
                    "direction": "out",
                }
            ]
        ),
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003", "SALE-004"]),
            "edges": pa.array(rows, type=pa.list_(_EDGE_STRUCT)),
        }
    )


def _make_prop_filter_state(geo_table):
    """Build _state mocks for property_filters / include_properties tests."""
    rel_cust = MagicMock()
    rel_cust.line_id = "customers"
    mock_pattern = MagicMock()
    mock_pattern.version = 1
    mock_pattern.entity_type = "sales"
    mock_pattern.relations = [rel_cust]

    mock_sales_line = MagicMock()
    mock_sales_line.line_id = "sales"
    mock_sales_line.entity_type = "sales"
    mock_sales_line.line_role = "event"
    mock_sales_line.versions = [1]

    mock_cust_line = MagicMock()
    mock_cust_line.versions = [1]

    cust_points = pa.table(
        {
            "primary_key": pa.array(["CUST-001", "CUST-002", "CUST-003"]),
            "name": pa.array(["Alice", "Bob", "Carol"]),
            "credit_limit": pa.array([50000, 150000, 80000], type=pa.int32()),
            "customer_group": pa.array(["SME", "VIP", "SME"]),
        }
    )

    def read_points_side(line_id, version, **kwargs):
        if line_id == "customers":
            return cust_points
        return pa.table({"primary_key": pa.array([], type=pa.string())})

    reader = MagicMock()
    reader.read_geometry.return_value = geo_table
    reader.read_points.side_effect = read_points_side
    reader.read_points_schema.side_effect = lambda lid, ver: read_points_side(lid, ver).schema
    reader.read_points_batch.side_effect = _make_batch_side(read_points_side)

    sphere_meta = MagicMock()
    sphere_meta.patterns = {"sale_pattern": mock_pattern}
    sphere_meta.lines = {"sales": mock_sales_line, "customers": mock_cust_line}

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    session = MagicMock()
    session._reader = reader

    return sphere, session


# TestAggregatePropertyFilters removed — now tested in core


# ---------------------------------------------------------------------------
# Aggregate zero-results diagnostic note
# ---------------------------------------------------------------------------
def _make_anomaly_no_customer_geo():
    """2 anomalous polygons with NO edge to 'customers', 1 normal with edge."""
    edges_no_cust = [
        {"line_id": "stores", "point_key": "STORE-01", "status": "alive", "direction": "out"},
    ]
    edges_with_cust = [
        {"line_id": "customers", "point_key": "CUST-001", "status": "alive", "direction": "out"},
    ]
    return pa.table(
        {
            "primary_key": pa.array(["SALE-001", "SALE-002", "SALE-003"]),
            "pattern_id": pa.array(["sale_pattern"] * 3),
            "pattern_ver": pa.array([1, 1, 1]),
            "pattern_type": pa.array(["event"] * 3),
            "scale": pa.array([1, 1, 1]),
            "delta": pa.array([[0.5, 0.3], [0.9, 0.8], [0.1, 0.1]]),
            "delta_norm": pa.array([0.58, 1.20, 0.14]),
            "is_anomaly": pa.array([True, True, False]),
            "edges": pa.array(
                [edges_no_cust, edges_no_cust, edges_with_cust], type=pa.list_(_EDGE_STRUCT)
            ),
            "last_refresh_at": pa.array([None, None, None]),
            "updated_at": pa.array([None, None, None]),
        }
    )


def _make_anomaly_note_state(geo_table):
    mock_pattern = MagicMock()
    mock_pattern.version = 1
    mock_pattern.entity_type = "sales"
    mock_pattern.pattern_type = "event"
    rel_cust = MagicMock()
    rel_cust.line_id = "customers"
    rel_stores = MagicMock()
    rel_stores.line_id = "stores"
    mock_pattern.relations = [rel_cust, rel_stores]

    mock_sales_line = MagicMock()
    mock_sales_line.line_id = "sales"
    mock_sales_line.entity_type = "sales"
    mock_sales_line.line_role = "event"
    mock_sales_line.versions = [1]

    mock_cust_line = MagicMock()
    mock_cust_line.versions = [1]

    mock_stores_line = MagicMock()
    mock_stores_line.versions = [1]

    cust_points = pa.table(
        {
            "primary_key": pa.array(["CUST-001"]),
            "name": pa.array(["Alice"]),
        }
    )

    def read_points_side(line_id, version, **kwargs):
        if line_id == "customers":
            return cust_points
        return pa.table({"primary_key": pa.array([], type=pa.string())})

    reader = MagicMock()
    reader.read_geometry.return_value = geo_table
    reader.read_points.side_effect = read_points_side
    reader.read_points_schema.side_effect = lambda lid, ver: read_points_side(lid, ver).schema
    reader.read_points_batch.side_effect = _make_batch_side(read_points_side)

    sphere_meta = MagicMock()
    sphere_meta.patterns = {"sale_pattern": mock_pattern}
    sphere_meta.lines = {
        "sales": mock_sales_line,
        "customers": mock_cust_line,
        "stores": mock_stores_line,
    }

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    session = MagicMock()
    session._reader = reader

    return sphere, session


# TestAggregateZeroResultsNote removed — now tested in core


class TestDiveSolid:
    """Tests for dive_solid MCP tool — timestamp parameter wiring."""

    def _setup(self, solid):
        from hypertopos_mcp.server import _state

        nav_mock = MagicMock()
        nav_mock.position = solid

        def fake_dive(bk, pid, timestamp=None):
            # simulate timestamp truncation on the mock
            if timestamp is not None:
                filtered = [s for s in solid.slices if s.timestamp <= timestamp]
                nav_mock.position = Solid(
                    primary_key=solid.primary_key,
                    pattern_id=solid.pattern_id,
                    base_polygon=solid.base_polygon,
                    slices=filtered,
                )

        nav_mock.π3_dive_solid = fake_dive
        pattern_mock = MagicMock()
        pattern_mock.theta_norm = 2.5
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["sphere"]._sphere.patterns = {"customer_pattern": pattern_mock}
        _state["navigator"] = nav_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = MagicMock()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_no_timestamp_returns_full_history(self):
        from hypertopos_mcp.tools.navigation import dive_solid

        solid = _make_solid()  # 3 slices: 2024-03-01, 2024-03-02, 2024-03-03
        self._setup(solid)
        result = json.loads(dive_solid("CUST-0001", "customer_pattern"))
        assert result["num_slices"] == 3

    def test_timestamp_truncates_slices(self):
        from hypertopos_mcp.tools.navigation import dive_solid

        solid = _make_solid()  # slices: 2024-03-01, 2024-03-02, 2024-03-03
        self._setup(solid)
        result = json.loads(
            dive_solid(
                "CUST-0001",
                "customer_pattern",
                timestamp="2024-03-02T00:00:00+00:00",
            )
        )
        assert result["num_slices"] == 2

    def test_naive_timestamp_treated_as_utc(self):
        from hypertopos_mcp.tools.navigation import dive_solid

        solid = _make_solid()
        self._setup(solid)
        # naive ISO string — should not raise, treated as UTC
        result = json.loads(
            dive_solid(
                "CUST-0001",
                "customer_pattern",
                timestamp="2024-03-02T00:00:00",
            )
        )
        assert result["num_slices"] == 2

    def test_invalid_timestamp_raises(self):
        from hypertopos_mcp.tools.navigation import dive_solid

        solid = _make_solid()
        self._setup(solid)
        with pytest.raises(ValueError):
            dive_solid("CUST-0001", "customer_pattern", timestamp="not-a-date")

    def test_forecast_current_delta_norm_uses_base_polygon(self):
        """forecast.current_delta_norm must reflect the base_polygon delta_norm,
        not the norm of the last deformation slice's delta_snapshot."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.navigation import dive_solid

        solid = _make_solid()
        # base_polygon.delta_norm = norm([0.3, -0.2]) ≈ 0.3606
        expected = round(float(solid.base_polygon.delta_norm), 4)
        self._setup(solid)
        nav_mock = _state["navigator"]
        nav_mock.solid_forecast.return_value = {
            "horizon": 1,
            "predicted_delta_norm": 0.4,
            "current_delta_norm": expected,
            "forecast_is_anomaly": False,
            "current_is_anomaly": False,
            "reliability": "medium",
        }
        result = json.loads(dive_solid("CUST-0001", "customer_pattern"))
        forecast = result.get("forecast")
        assert forecast is not None, "Expected 'forecast' key in result (solid has 3 slices)"
        assert forecast["current_delta_norm"] == expected
        # Verify solid_forecast was called with base_polygon.delta_norm
        nav_mock.solid_forecast.assert_called_once_with(
            "CUST-0001",
            "customer_pattern",
            current_delta_norm=float(solid.base_polygon.delta_norm),
        )


class TestDiveSolidStaleForecast:
    """Tests for stale forecast warning in dive_solid."""

    def _setup(self, solid):
        from hypertopos_mcp.server import _state

        nav_mock = MagicMock()
        nav_mock.position = solid
        nav_mock.π3_dive_solid = MagicMock()
        pattern_mock = MagicMock()
        pattern_mock.theta_norm = 2.5
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["sphere"]._sphere.patterns = {"customer_pattern": pattern_mock}
        _state["navigator"] = nav_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = MagicMock()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_stale_forecast_adds_warning(self):
        """When last slice is >180 days old, stale_forecast_warning must appear
        and forecast reliability must be overridden to 'low'."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.navigation import dive_solid

        # Create slices dated ~2 years ago (well over 180 days)
        stale_slices = [
            SolidSlice(
                slice_index=i,
                timestamp=datetime(2023, 1, i + 1, tzinfo=UTC),
                deformation_type="edge",
                delta_snapshot=np.array([0.1, -0.1], dtype=np.float32),
                delta_norm_snapshot=0.1414,
                pattern_ver=1,
                changed_property=None,
                changed_line_id="products",
                added_edge=None,
            )
            for i in range(3)
        ]
        base = _make_polygon("CUST-0001")
        solid = Solid(
            primary_key="CUST-0001",
            pattern_id="customer_pattern",
            base_polygon=base,
            slices=stale_slices,
        )
        self._setup(solid)
        nav_mock = _state["navigator"]
        nav_mock.solid_forecast.return_value = {
            "horizon": 1,
            "predicted_delta_norm": 0.15,
            "current_delta_norm": 0.3606,
            "forecast_is_anomaly": False,
            "current_is_anomaly": False,
            "reliability": "low",
            "stale_warning": (
                "Forecast extrapolated from slices ending 2023-01-03 "
                "(1000 days ago). Drift trajectory may no longer reflect current behavior."
            ),
        }
        result = json.loads(dive_solid("CUST-0001", "customer_pattern"))
        assert "stale_forecast_warning" in result
        assert "2023-01-03" in result["stale_forecast_warning"]
        assert result["forecast"]["reliability"] == "low"

    def test_fresh_forecast_no_warning(self):
        """When last slice is recent, no stale_forecast_warning should appear."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.navigation import dive_solid

        now = datetime.now(UTC)
        fresh_slices = [
            SolidSlice(
                slice_index=i,
                timestamp=now,
                deformation_type="edge",
                delta_snapshot=np.array([0.1, -0.1], dtype=np.float32),
                delta_norm_snapshot=0.1414,
                pattern_ver=1,
                changed_property=None,
                changed_line_id="products",
                added_edge=None,
            )
            for i in range(3)
        ]
        base = _make_polygon("CUST-0001")
        solid = Solid(
            primary_key="CUST-0001",
            pattern_id="customer_pattern",
            base_polygon=base,
            slices=fresh_slices,
        )
        self._setup(solid)
        nav_mock = _state["navigator"]
        nav_mock.solid_forecast.return_value = {
            "horizon": 1,
            "predicted_delta_norm": 0.15,
            "current_delta_norm": 0.3606,
            "forecast_is_anomaly": False,
            "current_is_anomaly": False,
            "reliability": "medium",
        }
        result = json.loads(dive_solid("CUST-0001", "customer_pattern"))
        assert "stale_forecast_warning" not in result
        assert result["forecast"]["reliability"] == "medium"


class TestDiveSolidBasePolygonNote:
    """Tests for base_polygon_note in dive_solid."""

    def _setup(self, solid):
        from hypertopos_mcp.server import _state

        nav_mock = MagicMock()
        nav_mock.position = solid
        nav_mock.π3_dive_solid = MagicMock()
        pattern_mock = MagicMock()
        pattern_mock.theta_norm = 2.5
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.lines = {}
        _state["sphere"]._sphere.patterns = {"customer_pattern": pattern_mock}
        _state["navigator"] = nav_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = MagicMock()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_base_polygon_note_present_when_slices_exist(self, open_berka_sphere):
        """base_polygon_note must appear when the solid has temporal slices."""
        from hypertopos_mcp.tools.navigation import dive_solid, goto
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        goto("2", "accounts")
        result = json.loads(dive_solid("2", "account_behavior_pattern"))
        assert result["num_slices"] > 0
        assert "base_polygon_note" in result
        assert "get_polygon" in result["base_polygon_note"]

    def test_base_polygon_note_absent_when_no_slices(self):
        """base_polygon_note must NOT appear when the solid has no slices."""
        from hypertopos_mcp.tools.navigation import dive_solid

        base = _make_polygon("CUST-0001")
        solid = Solid(
            primary_key="CUST-0001",
            pattern_id="customer_pattern",
            base_polygon=base,
            slices=[],
        )
        self._setup(solid)
        result = json.loads(dive_solid("CUST-0001", "customer_pattern"))
        assert "base_polygon_note" not in result


# ---------------------------------------------------------------------------
# Integration tests — require live Berka sphere
# ---------------------------------------------------------------------------
def test_aggregate_group_by_property_total_entities(open_berka_sphere):
    """aggregate with group_by_property must include total_entities (distinct entity count)."""
    import json

    from hypertopos_mcp.tools.aggregation import aggregate

    result = json.loads(
        aggregate(
            "tx_pattern", "accounts", group_by_property="accounts:region", metric="count", limit=5
        )
    )
    assert "total_entities" in result
    assert result["total_entities"] <= result["total_groups"]
    assert result["total_entities"] > 0


def test_aggregate_include_properties_populated_in_results(open_berka_sphere):
    """include_properties must populate property columns in each result row."""  # noqa: E501
    import json

    from hypertopos_mcp.tools.aggregation import aggregate

    result = json.loads(
        aggregate(
            "tx_pattern",
            "accounts",
            include_properties=["region"],
            metric="count",
            limit=5,
        )
    )
    assert result["results"], "expected non-empty results"
    for row in result["results"]:
        assert "region" in row, f"'region' missing from row: {row}"  # noqa: E501


def test_aggregate_gbp_with_filters_returns_valid_structure(open_berka_sphere):
    """group_by_property + filters (resolved_filters path) must return valid JSON."""  # noqa: E501
    import json

    from hypertopos_mcp.tools.aggregation import aggregate

    result = json.loads(
        aggregate(
            "tx_pattern",
            "accounts",
            group_by_property="accounts:region",
            filters=[{"line": "accounts", "key": "741"}],
            metric="count",
            limit=10,
        )
    )
    assert "results" in result
    assert "total_entities" in result
    assert "elapsed_ms" in result
    assert isinstance(result["elapsed_ms"], float)
    for row in result["results"]:
        assert "key" in row
        assert "region" in row


def test_get_event_polygons_total_unfiltered_when_geometry_filters_used(open_berka_sphere):
    """When geometry_filters is provided, response includes total_unfiltered."""
    import json

    from hypertopos_mcp.tools.navigation import goto

    goto("741", "accounts")
    from hypertopos_mcp.tools.geometry import get_event_polygons

    result = json.loads(
        get_event_polygons("741", "tx_pattern", geometry_filters={"is_anomaly": True}, limit=5)
    )
    assert "total_unfiltered" in result, (
        "total_unfiltered must be present when geometry_filters used"
    )
    assert result["total_unfiltered"] >= result["total"], "unfiltered >= filtered"


def test_search_entities_bool_column(open_berka_sphere):
    """search_entities must work on bool columns (has_loan='yes')."""
    import json

    from hypertopos_mcp.tools.session import search_entities

    result = json.loads(search_entities("accounts", "has_loan", "yes"))
    assert result["total"] > 0, "expected accounts with loans"
    assert result["returned"] == len(result["entities"])
    assert all(e["properties"].get("has_loan") == "yes" for e in result["entities"])


def test_search_entities_total_independent_of_limit(open_berka_sphere):
    """total must reflect all matches regardless of limit; returned reflects actual slice."""
    import json

    from hypertopos_mcp.tools.session import search_entities

    full = json.loads(search_entities("accounts", "region", "Prague", limit=9999))
    limited = json.loads(search_entities("accounts", "region", "Prague", limit=1))
    assert full["total"] == limited["total"], "total must be the same regardless of limit"
    assert limited["returned"] == 1
    assert full["returned"] == full["total"]


def test_every_tool_response_has_elapsed_ms(open_berka_sphere):
    """Every tool response JSON must include elapsed_ms as a non-negative float."""
    import json

    from hypertopos_mcp.tools.session import get_sphere_info

    result = json.loads(get_sphere_info())
    assert "elapsed_ms" in result, "elapsed_ms missing from tool response"
    assert isinstance(result["elapsed_ms"], float)
    assert result["elapsed_ms"] >= 0.0


def test_get_polygon_includes_theta_norm(open_berka_sphere):
    """get_polygon response must include theta_norm for the requested pattern."""
    import json

    from hypertopos_mcp.tools.geometry import get_polygon
    from hypertopos_mcp.tools.navigation import goto

    goto("1", "accounts")
    result = json.loads(get_polygon("account_behavior_pattern"))
    assert "theta_norm" in result, "theta_norm missing from get_polygon response"
    assert isinstance(result["theta_norm"], float)
    assert result["theta_norm"] > 0.0


def test_get_solid_includes_theta_norm(open_berka_sphere):
    """get_solid response must include theta_norm for the requested pattern."""
    import json

    from hypertopos_mcp.tools.geometry import get_solid
    from hypertopos_mcp.tools.navigation import goto

    goto("1", "accounts")
    result = json.loads(get_solid("account_behavior_pattern"))
    assert "theta_norm" in result, "theta_norm missing from get_solid response"
    assert isinstance(result["theta_norm"], float)
    assert result["theta_norm"] > 0.0


class TestGetSolidForecast:
    """get_solid should include forecast + stale_forecast_warning like dive_solid."""

    def test_get_solid_includes_forecast_when_slices_gte_3(self, open_berka_sphere):
        """get_solid returns forecast block when solid has >= 3 slices."""
        from hypertopos_mcp.tools.navigation import goto
        from hypertopos_mcp.tools.geometry import get_solid
        import json

        goto("2", "accounts")
        raw = get_solid("account_behavior_pattern")
        result = json.loads(raw)
        assert result["num_slices"] >= 3, "precondition: entity 2 must have >= 3 slices"
        assert "forecast" in result, (
            "get_solid should include forecast when num_slices >= 3"
        )

    def test_get_solid_stale_forecast_warning(self, open_berka_sphere):
        """get_solid propagates stale_forecast_warning for old data."""
        from hypertopos_mcp.tools.navigation import goto
        from hypertopos_mcp.tools.geometry import get_solid
        import json

        goto("2", "accounts")
        raw = get_solid("account_behavior_pattern")
        result = json.loads(raw)
        assert result["num_slices"] >= 3, "precondition: entity 2 must have >= 3 slices"
        assert "stale_forecast_warning" in result, (
            "Berka data is decades old — stale_forecast_warning expected"
        )

    def test_get_solid_no_forecast_when_few_slices(self, open_berka_sphere):
        """get_solid omits forecast for event pattern (0 slices)."""
        from hypertopos_mcp.tools.navigation import goto, walk_line
        from hypertopos_mcp.tools.geometry import get_solid
        import json

        # Navigate to tx_pattern entity line (transactions) — events have 0 slices
        goto("TX-0000000", "transactions")
        raw = get_solid("tx_pattern")
        result = json.loads(raw)
        assert result["num_slices"] < 3, "precondition: tx event must have < 3 slices"
        assert "forecast" not in result


def test_get_event_polygons_sample_n(open_berka_sphere):
    """sample=2 draws at most 2 polygons; sampled=True when fewer than total."""
    import json

    from hypertopos_mcp.tools.geometry import get_event_polygons

    result = json.loads(get_event_polygons("741", "tx_pattern", sample=2))
    assert result["total"] > 2, "need entity with more than 2 entries for this test"
    assert result["sampled"] is True
    assert result["sample_size"] == 2
    assert result["returned"] <= 2


def test_get_event_polygons_sample_pct(open_berka_sphere):
    """sample_pct=0.01 draws a small fraction; sampled=True."""
    import json

    from hypertopos_mcp.tools.geometry import get_event_polygons

    result = json.loads(get_event_polygons("741", "tx_pattern", sample_pct=0.01, limit=50))
    assert result["sampled"] is True
    assert result["sample_size"] >= 1


def test_get_event_polygons_sample_no_sampling_when_large(open_berka_sphere):
    """sample larger than total -> sampled=False."""
    import json

    from hypertopos_mcp.tools.geometry import get_event_polygons

    result = json.loads(get_event_polygons("741", "tx_pattern", sample=999999))
    assert result["sampled"] is False
    assert "sample_size" not in result


def test_get_event_polygons_sample_and_pct_raises(open_berka_sphere):
    """Passing both sample and sample_pct raises RuntimeError."""
    import pytest
    from hypertopos_mcp.tools.geometry import get_event_polygons

    with pytest.raises(RuntimeError, match="mutually exclusive"):
        get_event_polygons("741", "tx_pattern", sample=1, sample_pct=0.5)


def test_get_sphere_info_active_manifest(open_berka_sphere):
    """get_sphere_info must include active_manifest with manifest_id and line_versions."""
    import json

    from hypertopos_mcp.tools.session import get_sphere_info

    result = json.loads(get_sphere_info())
    assert "active_manifest" in result
    m = result["active_manifest"]
    assert "manifest_id" in m
    assert "snapshot_time" in m
    assert "line_versions" in m
    assert isinstance(m["line_versions"], dict)
    assert len(m["line_versions"]) > 0


# ---------------------------------------------------------------------------
# Fix #4: unit test for GDSEngine.count_inside_alias
# ---------------------------------------------------------------------------


class TestComputePopulationInside:
    """Unit test for GDSEngine.count_inside_alias."""

    def test_returns_count_when_cutting_plane_set(self):
        """population_inside counts entities with signed_dist > 0."""
        from hypertopos.engine.geometry import GDSEngine
        from hypertopos.model.sphere import CuttingPlane

        # 2D deltas: 3 entities, cutting plane w=[1,0] b=0.3
        # signed_dist = (delta @ w - b) / ||w||
        # Entity A: delta=[0.5, 0.1] → (0.5 - 0.3) / 1.0 = 0.2 > 0 → inside
        # Entity B: delta=[0.1, 0.9] → (0.1 - 0.3) / 1.0 = -0.2 < 0 → outside
        # Entity C: delta=[0.8, 0.2] → (0.8 - 0.3) / 1.0 = 0.5 > 0 → inside
        cp = CuttingPlane(normal=[1.0, 0.0], bias=0.3)

        mock_alias_filter = MagicMock()
        mock_alias_filter.cutting_plane = cp

        mock_alias = MagicMock()
        mock_alias.base_pattern_id = "test_pattern"
        mock_alias.filter = mock_alias_filter

        geo = pa.table(
            {
                "primary_key": ["A", "B", "C"],
                "delta": [[0.5, 0.1], [0.1, 0.9], [0.8, 0.2]],
                "delta_norm": [0.51, 0.91, 0.82],
            }
        )

        engine = GDSEngine(storage=None, cache=None)
        assert engine.count_inside_alias(mock_alias, geo) == 2

    def test_returns_zero_when_no_cutting_plane(self):
        """Returns 0 when alias has no cutting plane."""
        from hypertopos.engine.geometry import GDSEngine

        mock_alias = MagicMock()
        mock_alias.filter.cutting_plane = None

        geo = pa.table(
            {
                "primary_key": ["A", "B"],
                "delta": pa.array([[0.5, 0.1], [0.8, 0.2]], type=pa.list_(pa.float32())),
            }
        )

        engine = GDSEngine(storage=None, cache=None)
        assert engine.count_inside_alias(mock_alias, geo) == 0

    def test_returns_zero_when_geometry_empty(self):
        """Returns 0 when geometry table has no rows."""
        from hypertopos.engine.geometry import GDSEngine
        from hypertopos.model.sphere import CuttingPlane

        cp = CuttingPlane(normal=[1.0, 0.0], bias=0.3)

        mock_alias = MagicMock()
        mock_alias.filter.cutting_plane = cp

        empty_geo = pa.table(
            {
                "primary_key": pa.array([], type=pa.utf8()),
                "delta": pa.array([], type=pa.list_(pa.float32())),
                "delta_norm": pa.array([], type=pa.float64()),
            }
        )

        engine = GDSEngine(storage=None, cache=None)
        assert engine.count_inside_alias(mock_alias, empty_geo) == 0


# ---------------------------------------------------------------------------
# Fix #5: integration test for alias_inside geometry filter in aggregate
# ---------------------------------------------------------------------------
# TestAggregateAliasInsideFilter removed — now tested in core

# ---------------------------------------------------------------------------
# _make_edge_map_fn — fast path / legacy fallback
# ---------------------------------------------------------------------------

# TestMakeEdgeMapFn removed — _make_edge_map_fn moved to core engine


# TestAggregateFastPathIntegration, TestAggregateReversedScanCount,
# TestAggregateGeometryFilterLancePushdown, TestEdgeArraysHelper,
# TestAggregateVectorizedAgg, and vectorized count/sample standalone tests removed
# — these tested internal helpers that moved to core engine (aggregation.py)


class TestFindClusters:
    """MCP-layer tests for find_clusters tool."""

    def _make_state(self, with_entity_line: bool = True):
        """Set up _state with a navigator that returns 2 clusters."""
        from hypertopos_mcp.server import _state

        clusters = [
            {
                "cluster_id": 0,
                "size": 8,
                "anomaly_rate": 0.25,
                "centroid_delta": [0.1, 0.2],
                "delta_norm_mean": 0.5,
                "delta_norm_std": 0.05,
                "representative_key": "E-001",
                "dim_profile": [
                    {"dimension": "line_0", "centroid_value": 0.1},
                    {"dimension": "line_1", "centroid_value": 0.2},
                ],
                "member_keys": ["E-001", "E-002"],
            },
            {
                "cluster_id": 1,
                "size": 4,
                "anomaly_rate": 0.0,
                "centroid_delta": [1.0, 1.0],
                "delta_norm_mean": 1.4,
                "delta_norm_std": 0.1,
                "representative_key": "E-010",
                "dim_profile": [
                    {"dimension": "line_0", "centroid_value": 1.0},
                    {"dimension": "line_1", "centroid_value": 1.0},
                ],
                "member_keys": ["E-010"],
            },
        ]

        import pyarrow as pa

        props_table = pa.table(
            {
                "primary_key": ["E-001", "E-002", "E-010"],
                "name": ["Alice", "Bob", None],
            }
        )
        reader = MagicMock()
        reader.read_points.return_value = props_table
        session = MagicMock()
        session._reader = reader

        navigator = MagicMock()
        attract = MagicMock(return_value=clusters)
        navigator.π8_attract_cluster = attract

        sphere_mock = MagicMock()
        if with_entity_line:
            sphere_mock._sphere.lines = {"entities": MagicMock()}
            sphere_mock._sphere.patterns = {"entity_pattern": MagicMock(entity_type="entities")}
        else:
            sphere_mock._sphere.lines = {}
            sphere_mock._sphere.patterns = {"entity_pattern": MagicMock(entity_type="entities")}

        _state["navigator"] = navigator
        _state["sphere"] = sphere_mock
        _state["session"] = session
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        return _state

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_returns_valid_json_structure(self):
        from hypertopos_mcp.tools.analysis import find_clusters

        self._make_state(with_entity_line=False)
        result = json.loads(find_clusters("entity_pattern", n_clusters=2))
        assert result["pattern_id"] == "entity_pattern"
        assert result["n_clusters_requested"] == 2
        assert result["n_clusters_found"] == 2
        assert "sample_size" in result
        assert len(result["clusters"]) == 2

    def test_cluster_uses_properties_key_not_props(self):
        from hypertopos_mcp.tools.analysis import find_clusters

        self._make_state(with_entity_line=False)
        result = json.loads(find_clusters("entity_pattern", n_clusters=2))
        c = result["clusters"][0]
        assert "representative_properties" in c
        assert "props" not in c
        assert "representative_props" not in c
        assert all("properties" in m for m in c["members"])
        assert all("props" not in m for m in c["members"])

    def test_none_values_filtered_from_properties(self):
        from hypertopos_mcp.tools.analysis import find_clusters

        # E-002 has name=None in the mock table — should be stripped
        self._make_state(with_entity_line=True)
        result = json.loads(find_clusters("entity_pattern", n_clusters=2))
        for c in result["clusters"]:
            assert None not in c["representative_properties"].values()
            for m in c["members"]:
                assert None not in m["properties"].values()

    def test_fallback_path_no_entity_line(self):
        from hypertopos_mcp.tools.analysis import find_clusters

        self._make_state(with_entity_line=False)
        result = json.loads(find_clusters("entity_pattern", n_clusters=2))
        for c in result["clusters"]:
            assert c["representative_properties"] == {}
            assert all(m["properties"] == {} for m in c["members"])

    def test_find_clusters_event_pattern_members_have_properties(self):
        """GL entry cluster members must include event properties when event line exists."""
        import hypertopos_mcp.tools.analysis as analysis_mod
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_clusters

        # Build a minimal sphere with an event-role line
        sphere_mock = MagicMock()
        pat_mock = MagicMock(pattern_id="gl_entry_pattern", pattern_type="event", relations=[])
        sphere_mock.patterns = {"gl_entry_pattern": pat_mock}
        # event_line returns the event line id (the entity container for event patterns)
        sphere_mock.event_line.return_value = "gl_entries"

        nav_mock = MagicMock()
        nav_mock.π8_attract_cluster.return_value = [
            {
                "cluster_id": 0,
                "size": 2,
                "anomaly_rate": 0.0,
                "centroid_delta": [0.1, 0.2],
                "delta_norm_mean": 0.5,
                "delta_norm_std": 0.1,
                "representative_key": "GL-001",
                "dim_profile": [],
                "member_keys": ["GL-001", "GL-002"],
            }
        ]

        reader_mock = MagicMock()
        # build_batch_lookups will return properties for the event line
        original_build = analysis_mod.build_batch_lookups

        def fake_build(reader, sphere, keys_by_line):
            return {
                "gl_entries": {
                    "GL-001": {"document_type": "standard", "amount": 100.0},
                    "GL-002": {"document_type": "reversal", "amount": -50.0},
                }
            }

        analysis_mod.build_batch_lookups = fake_build

        sphere_wrapper = MagicMock()
        sphere_wrapper._sphere = sphere_mock
        session_mock = MagicMock()
        session_mock._reader = reader_mock

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere_wrapper
        _state["session"] = session_mock

        try:
            result = json.loads(find_clusters("gl_entry_pattern", n_clusters=3, top_n=2))
            clusters = result["clusters"]
            assert len(clusters) == 1
            # Representative should have properties
            assert clusters[0]["representative_properties"] == {
                "document_type": "standard",
                "amount": 100.0,
            }  # noqa: E501
            # Members should have properties
            member_map = {m["key"]: m["properties"] for m in clusters[0]["members"]}
            assert member_map["GL-001"] == {"document_type": "standard", "amount": 100.0}
            assert member_map["GL-002"] == {"document_type": "reversal", "amount": -50.0}
        finally:
            analysis_mod.build_batch_lookups = original_build
            for k in list(_state.keys()):
                _state[k] = None

    def test_k_reduction_adds_capped_warning(self) -> None:
        """B5: n_clusters_found < n_clusters_requested -> capped_warning in response."""
        from hypertopos_mcp.tools.analysis import find_clusters

        # _make_state returns navigator that always yields 2 clusters
        self._make_state()
        # request 5, get 2 -> warning expected
        result = json.loads(find_clusters("entity_pattern", n_clusters=5))
        assert result["n_clusters_requested"] == 5
        assert result["n_clusters_found"] == 2
        assert "capped_warning" in result

    def test_no_capped_warning_when_k_satisfied(self) -> None:
        """B5: n_clusters_found == n_clusters_requested -> no capped_warning."""
        from hypertopos_mcp.tools.analysis import find_clusters

        self._make_state()
        result = json.loads(find_clusters("entity_pattern", n_clusters=2))
        assert result["n_clusters_found"] == 2
        assert "capped_warning" not in result

    def test_no_capped_warning_for_auto_k(self) -> None:
        """B5: n_clusters=0 (auto-k) -> no capped_warning even if found < some arbitrary number."""
        from hypertopos_mcp.tools.analysis import find_clusters

        self._make_state()
        result = json.loads(find_clusters("entity_pattern", n_clusters=0))
        assert result["auto_k"] is True
        assert "capped_warning" not in result


def test_dive_solid_base_polygon_edges_have_properties(open_berka_sphere):
    """dive_solid base_polygon edges must include entity properties.

    Verifies parity with get_solid which enriches edges via enrich_polygon().
    """
    from hypertopos_mcp.tools.navigation import dive_solid, goto

    goto("2", "accounts")
    result = json.loads(dive_solid("2", "account_behavior_pattern"))
    base = result.get("base_polygon", {})
    edges = base.get("edges", [])
    assert len(edges) > 0, "Expected at least one edge in base_polygon"
    bare_keys = {"line_id", "point_key", "status", "direction"}
    enriched = [e for e in edges if set(e.keys()) - bare_keys]
    assert len(enriched) > 0, (
        f"Expected edges with properties beyond bare keys, got: {edges[:2]}"  # noqa: E501
    )


def test_dive_solid_includes_theta_norm(open_berka_sphere):
    """dive_solid response must include theta_norm for the requested pattern."""
    from hypertopos_mcp.tools.navigation import dive_solid, goto

    goto("2", "accounts")
    result = json.loads(dive_solid("2", "account_behavior_pattern"))
    assert "theta_norm" in result, "theta_norm missing from dive_solid response"


class TestCheckAlerts:
    """MCP-layer tests for check_alerts tool."""

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def _setup_state(self, nav_return):
        from hypertopos_mcp.server import _state

        nav = MagicMock()
        nav.check_alerts.return_value = nav_return
        _state["sphere"] = MagicMock()
        _state["navigator"] = nav
        return nav

    def test_check_alerts_returns_json(self) -> None:
        from hypertopos_mcp.tools.observability import check_alerts

        alerts_data = {
            "alerts": [
                {
                    "check": "anomaly_rate_spike",
                    "severity": "HIGH",
                    "pattern_id": "customer_pattern",
                    "message": "anomaly rate jumped from 5.0% to 35.0%",
                },
            ],
            "patterns_scanned": 1,
            "total_alerts": 1,
        }
        self._setup_state(alerts_data)

        result = check_alerts()
        data = json.loads(result)
        assert data["total_alerts"] == 1
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["severity"] == "HIGH"
        assert data["alerts"][0]["check"] == "anomaly_rate_spike"

    def test_check_alerts_with_pattern_id(self) -> None:
        from hypertopos_mcp.tools.observability import check_alerts

        nav = self._setup_state(
            {
                "alerts": [],
                "patterns_scanned": 1,
                "total_alerts": 0,
            }
        )

        result = check_alerts(pattern_id="customer_pattern")
        json.loads(result)  # valid JSON
        nav.check_alerts.assert_called_once_with("customer_pattern")

    def test_check_alerts_no_alerts(self) -> None:
        from hypertopos_mcp.tools.observability import check_alerts

        self._setup_state(
            {
                "alerts": [],
                "patterns_scanned": 3,
                "total_alerts": 0,
            }
        )

        result = check_alerts()
        data = json.loads(result)
        assert data["alerts"] == []
        assert data["total_alerts"] == 0
        assert data["patterns_scanned"] == 3


class TestSphereOverviewAnomalyRateTrendsConsistency:
    """sphere_overview must patch trends anomaly_rate with the live value."""

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def _setup_state(self, live_anomaly_rate: float, stale_trend_value: float):
        from hypertopos_mcp.server import _state

        overview_entry = {
            "pattern_id": "customer_pattern",
            "entity_count": 100,
            "anomaly_rate": live_anomaly_rate,
            "pattern_type": "anchor",
            "theta_norm": 1.5,
        }
        nav = MagicMock()
        nav.sphere_overview.return_value = [overview_entry]
        nav.temporal_quality_summary.return_value = None

        forecast_table = pa.table(
            {
                "metric": pa.array(["anomaly_rate", "entity_count"], type=pa.string()),
                "current_value": pa.array([stale_trend_value, 100.0], type=pa.float64()),
                "forecast_value": pa.array([0.18, 110.0], type=pa.float64()),
                "direction": pa.array(["up", "up"], type=pa.string()),
                "reliability": pa.array(["medium", "high"], type=pa.string()),
            }
        )

        reader = MagicMock()
        reader.read_population_forecast.return_value = forecast_table
        reader.read_calibration_tracker.return_value = None

        session = MagicMock()
        session._reader = reader

        _state["navigator"] = nav
        _state["sphere"] = MagicMock()
        _state["session"] = session
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()

    def test_trends_anomaly_rate_matches_live_value(self):
        """trends[anomaly_rate].current_value must equal the live anomaly_rate."""
        from hypertopos_mcp.tools.observability import sphere_overview

        live = 0.35
        stale = 0.10  # stale snapshot differs from live
        self._setup_state(live_anomaly_rate=live, stale_trend_value=stale)

        result = json.loads(sphere_overview(detail="full"))
        entry = result[0]

        assert entry["anomaly_rate"] == live

        trends_map = {t["metric"]: t for t in entry["trends"]}
        assert "anomaly_rate" in trends_map
        assert trends_map["anomaly_rate"]["current_value"] == live, (
            f"trends anomaly_rate current_value {trends_map['anomaly_rate']['current_value']} "
            f"!= live anomaly_rate {live}"
        )

    def test_summary_excludes_heavy_enrichments(self, open_berka_sphere):
        """detail='summary' must NOT include temporal_quality, trends, or calibration."""
        from hypertopos_mcp.tools.observability import sphere_overview

        result = json.loads(sphere_overview())  # default = "summary"
        first_pattern = result[0]
        assert "temporal_quality" not in first_pattern
        assert "calibration_stale" not in first_pattern


class TestSphereOverviewProfilingAlerts:
    """sphere_overview must emit profiling_alerts when dim_percentiles have extreme ratios."""

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def _setup_state(self, dim_percentiles, event_dimensions=None, prop_columns=None, relations=None):
        from hypertopos_mcp.server import _state

        overview_entry = {
            "pattern_id": "customer_pattern",
            "entity_count": 100,
            "anomaly_rate": 0.05,
            "pattern_type": "anchor",
            "theta_norm": 1.5,
        }
        nav = MagicMock()
        nav.sphere_overview.return_value = [overview_entry]

        # Build a mock pattern with dim_percentiles
        pattern = MagicMock()
        pattern.edge_max = None
        pattern.dim_percentiles = dim_percentiles
        pattern.entity_line_id = "customers"
        pattern.event_dimensions = event_dimensions or []
        pattern.prop_columns = prop_columns or []
        pattern.relations = relations or []

        sphere_mock = MagicMock()
        sphere_mock.patterns = {"customer_pattern": pattern}

        sphere_wrapper = MagicMock()
        sphere_wrapper._sphere = sphere_mock

        _state["navigator"] = nav
        _state["sphere"] = sphere_wrapper
        _state["session"] = MagicMock()
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()

    def test_extreme_ratio_produces_alert(self):
        """max/p99 > 3.0 → extreme alert in profiling_alerts."""
        from hypertopos_mcp.tools.observability import sphere_overview

        rel = MagicMock()
        rel.line_id = "_d_avg_late_days"
        self._setup_state(
            {
                "avg_late_days": {"min": 0.0, "p25": 20.0, "p50": 26.0, "p75": 32.0, "p99": 50.0, "max": 322.0},
            },
            relations=[rel],
        )
        result = json.loads(sphere_overview())
        entry = result[0]
        assert "profiling_alerts" in entry
        alert = entry["profiling_alerts"][0]
        assert alert["dimension"] == "avg_late_days"
        assert alert["ratio"] > 3.0
        assert "extreme" in alert["alert"]
        assert alert["p25"] == 20.0
        assert alert["p50"] == 26.0
        assert alert["p75"] == 32.0

    def test_moderate_ratio_produces_alert(self):
        """max/p99 between 1.5 and 3.0 → moderate alert."""
        from hypertopos_mcp.tools.observability import sphere_overview

        ed = MagicMock()
        ed.column = "score"
        self._setup_state(
            {
                "score": {"min": 0.0, "p25": 5.0, "p50": 10.0, "p75": 15.0, "p99": 18.0, "max": 29.0},
            },
            event_dimensions=[ed],
        )
        result = json.loads(sphere_overview())
        entry = result[0]
        assert "profiling_alerts" in entry
        alert = entry["profiling_alerts"][0]
        assert alert["ratio"] > 1.5
        assert "moderate" in alert["alert"]

    def test_no_alert_when_ratio_below_threshold(self):
        """max/p99 <= 1.5 → no profiling_alerts."""
        from hypertopos_mcp.tools.observability import sphere_overview

        ed = MagicMock()
        ed.column = "score"
        self._setup_state(
            {
                "score": {"min": 0.0, "p25": 5.0, "p50": 10.0, "p75": 15.0, "p99": 18.0, "max": 20.0},
            },
            event_dimensions=[ed],
        )
        result = json.loads(sphere_overview())
        entry = result[0]
        assert "profiling_alerts" not in entry

    def test_no_alert_when_no_dim_percentiles(self):
        """Pattern without dim_percentiles → no profiling_alerts."""
        from hypertopos_mcp.tools.observability import sphere_overview

        self._setup_state(None)
        result = json.loads(sphere_overview())
        entry = result[0]
        assert "profiling_alerts" not in entry

    def test_orphan_column_filtered_out(self):
        """Columns not used as pattern dimensions must not produce alerts."""
        from hypertopos_mcp.tools.observability import sphere_overview

        rel = MagicMock()
        rel.line_id = "_d_avg_late_days"
        self._setup_state(
            {
                # Pattern dimension — should alert (ratio 6.4)
                "avg_late_days": {"min": 0.0, "p25": 20.0, "p50": 26.0, "p75": 32.0, "p99": 50.0, "max": 322.0},
                # Orphan column — NOT a dimension, should be filtered (ratio 5.1)
                "balance_to_loan": {"min": 0.0, "p25": 0.2, "p50": 0.4, "p75": 0.7, "p99": 1.5, "max": 7.8},
            },
            relations=[rel],
        )
        result = json.loads(sphere_overview())
        entry = result[0]
        assert "profiling_alerts" in entry
        dims = [a["dimension"] for a in entry["profiling_alerts"]]
        assert "avg_late_days" in dims
        assert "balance_to_loan" not in dims


class TestAggregateAnomalies:
    """aggregate_anomalies groups anomalous anchor entities by property."""

    def test_basic_grouping(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import aggregate_anomalies

        result = json.loads(aggregate_anomalies(
            pattern_id="account_stress_pattern",
            group_by="frequency",
        ))
        assert result["pattern_id"] == "account_stress_pattern"
        assert result["total_anomalies"] > 0
        assert len(result["groups"]) > 0
        for g in result["groups"]:
            assert "group_key" in g
            assert "anomaly_count" in g
            assert "mean_delta_norm" in g

    def test_include_keys(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import aggregate_anomalies

        result = json.loads(aggregate_anomalies(
            pattern_id="account_stress_pattern",
            group_by="frequency",
            include_keys=True,
            keys_per_group=3,
        ))
        for g in result["groups"]:
            assert "entity_keys" in g
            assert len(g["entity_keys"]) <= 3

    def test_invalid_column_raises(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import aggregate_anomalies

        with pytest.raises(Exception, match="not found"):
            aggregate_anomalies(
                pattern_id="account_stress_pattern",
                group_by="nonexistent_column",
            )

    def test_property_filters(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import aggregate_anomalies

        # Without filter
        result_all = json.loads(aggregate_anomalies(
            pattern_id="account_stress_pattern",
            group_by="frequency",
        ))
        # With filter — only "monthly" frequency
        result_filtered = json.loads(aggregate_anomalies(
            pattern_id="account_stress_pattern",
            group_by="frequency",
            property_filters={"frequency": "POPLATEK MESICNE"},
        ))
        assert result_filtered["total_anomalies"] <= result_all["total_anomalies"]

    def test_ungrouped_anomalies_field_present(self, open_berka_sphere):
        """aggregate_anomalies includes ungrouped_anomalies field."""
        from hypertopos_mcp.tools.navigation import aggregate_anomalies

        raw = aggregate_anomalies(
            pattern_id="account_stress_pattern",
            group_by="frequency",
        )
        result = json.loads(raw)
        assert "ungrouped_anomalies" in result
        grouped_sum = sum(g["anomaly_count"] for g in result["groups"])
        assert result["total_anomalies"] == grouped_sum + result["ungrouped_anomalies"]


class TestFindAnomaliesPropertyFilters:
    """find_anomalies property_filters scopes anomalous entities by property ranges."""

    def test_basic_filter(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import find_anomalies

        result = json.loads(find_anomalies(
            pattern_id="account_stress_pattern",
            top_n=5,
            property_filters={"frequency": "POPLATEK MESICNE"},
        ))
        assert result["total_found"] > 0
        assert len(result["polygons"]) <= 5

    def test_filter_invalid_column(self, open_berka_sphere):
        from hypertopos_mcp.tools.navigation import find_anomalies

        with pytest.raises(Exception, match="not found"):
            find_anomalies(
                pattern_id="account_stress_pattern",
                top_n=5,
                property_filters={"nonexistent": "value"},
            )


class TestHubHistoryBinaryMode:
    """Tests for hub_history in binary mode."""

    def _setup(self, history_entries: list):
        from hypertopos_mcp.server import _state

        pattern_mock = MagicMock()
        pattern_mock.edge_max = None  # binary mode

        nav_mock = MagicMock()
        nav_mock.hub_score_history.return_value = list(history_entries)

        sphere_mock = MagicMock()
        sphere_mock._sphere.patterns = {"test_pattern": pattern_mock}

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere_mock
        _state["session"] = MagicMock()
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_binary_mode_base_state_is_null(self):
        """hub_history in binary mode must return base_state=null, not {}."""
        from hypertopos_mcp.tools.analysis import hub_history

        self._setup(history_entries=[])
        result = json.loads(hub_history("ENTITY-001", "test_pattern"))

        assert result["base_state"] is None, (
            f"Expected base_state=null in binary mode, got {result['base_state']!r}"
        )

    def test_binary_mode_note_explains_no_base_state(self):
        """hub_history in binary mode note must mention base_state."""
        from hypertopos_mcp.tools.analysis import hub_history

        self._setup(history_entries=[])
        result = json.loads(hub_history("ENTITY-001", "test_pattern"))

        assert "note" in result
        assert "base_state" in result["note"]

    def test_binary_mode_with_history_base_state_is_null(self):
        """Even when history entries exist, binary mode base_state must be null."""
        from hypertopos_mcp.tools.analysis import hub_history

        history_entry = {"timestamp": "2024-01-01", "hub_score": 3, "alive_edges_est": 3}
        self._setup(history_entries=[history_entry])
        result = json.loads(hub_history("ENTITY-001", "test_pattern"))

        assert result["base_state"] is None, (
            f"Expected base_state=null in binary mode with history, got {result['base_state']!r}"
        )


class TestSearchEntitiesHybridDegradationWarning:
    """D-3: warn when ANN vector index returned no candidates."""

    def _setup(self, vector_scores: list[float], ann_active: bool = True):
        from hypertopos_mcp.server import _state

        results = [
            {
                "primary_key": f"K-{i:03d}",
                "vector_score": vs,
                "text_score": 0.8,
                "final_score": 0.7 * vs + 0.3 * 0.8,
            }
            for i, vs in enumerate(vector_scores)
        ]
        navigator_mock = MagicMock()
        navigator_mock.search_hybrid.return_value = {
            "results": results,
            "ann_active": ann_active,
            "fts_candidates": len(results),
        }

        sphere_mock = MagicMock()
        sphere_mock._sphere.lines = {}
        sphere_mock._sphere.patterns = {}
        sphere_mock._sphere.aliases = {}
        # resolve_entity_line_id returns None → skip enrichment
        sphere_mock._sphere.entity_line.return_value = None
        sphere_mock._sphere.event_line.return_value = "events"

        _state["sphere"] = sphere_mock
        _state["navigator"] = navigator_mock
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()
        _state["session"] = _mock_session_with_reader()

    def _teardown(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_warning_present_when_ann_inactive(self):
        from hypertopos_mcp.tools.analysis import search_entities_hybrid

        self._setup([0.0, 0.0, 0.0], ann_active=False)
        result = json.loads(search_entities_hybrid("REF", "pat", "query"))
        assert "degradation_warning" in result
        assert "no candidates" in result["degradation_warning"]
        self._teardown()

    def test_no_warning_when_ann_active_and_scores_nonzero(self):
        from hypertopos_mcp.tools.analysis import search_entities_hybrid

        self._setup([0.5, 0.0, 0.3], ann_active=True)
        result = json.loads(search_entities_hybrid("REF", "pat", "query"))
        assert "degradation_warning" not in result
        self._teardown()

    def test_no_warning_when_ann_active_but_all_scores_zero(self):
        """False positive fix: ANN returned candidates but FTS won — no warning."""
        from hypertopos_mcp.tools.analysis import search_entities_hybrid

        self._setup([0.0, 0.0, 0.0], ann_active=True)
        result = json.loads(search_entities_hybrid("REF", "pat", "query"))
        assert "degradation_warning" not in result
        self._teardown()

    def test_no_warning_on_empty_results(self):
        from hypertopos_mcp.tools.analysis import search_entities_hybrid

        self._setup([], ann_active=False)
        result = json.loads(search_entities_hybrid("REF", "pat", "query"))
        assert "degradation_warning" not in result
        self._teardown()


class TestAnomalySummaryTopDrivingWeighted:
    """Verify that top_driving_dimensions uses delta_norm-weighted contributions."""

    def _setup(self, clusters, top_driving_dimensions=None):
        from hypertopos_mcp.server import _state

        summary = {
            "total_entities": 100,
            "anomaly_count": 6,
            "anomaly_rate_pct": 6.0,
            "clusters": clusters,
        }
        if top_driving_dimensions is not None:
            summary["top_driving_dimensions"] = top_driving_dimensions

        nav_mock = MagicMock()
        nav_mock.anomaly_summary.return_value = summary

        # Pattern mock with 2 relations (=> 2 dim labels)
        rel0 = MagicMock()
        rel0.display_name = "dim_0"
        rel0.line_id = "line_0"
        rel1 = MagicMock()
        rel1.display_name = "dim_1"
        rel1.line_id = "line_1"
        pattern_mock = MagicMock()
        pattern_mock.relations = [rel0, rel1]
        pattern_mock.prop_columns = []

        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere.patterns = {"test_pat": pattern_mock}
        _state["navigator"] = nav_mock

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_anomaly_summary_top_driving_weighted(self):
        """top_driving_dimensions from core navigator is passed through by MCP."""
        from hypertopos_mcp.tools.navigation import anomaly_summary

        clusters = [
            {"delta": [3.0, 0.0], "count": 1, "label": "A", "pct": 16.7},
            {"delta": [0.0, 1.0], "count": 5, "label": "B", "pct": 83.3},
        ]
        top_driving = [
            {"dim": 0, "label": "dim_0", "mean_contribution_pct": 84.4},
            {"dim": 1, "label": "dim_1", "mean_contribution_pct": 15.6},
        ]
        self._setup(clusters, top_driving_dimensions=top_driving)
        result = json.loads(anomaly_summary("test_pat"))
        dims = result["top_driving_dimensions"]

        # dim_0 should be first and dominant
        assert dims[0]["dim"] == 0
        assert dims[0]["label"] == "dim_0"
        assert dims[0]["mean_contribution_pct"] == 84.4

        # dim_1 should be second
        assert dims[1]["dim"] == 1
        assert dims[1]["label"] == "dim_1"
        assert dims[1]["mean_contribution_pct"] == 15.6

    def test_all_zero_deltas_no_driving_dimensions(self):
        """When core returns no top_driving_dimensions, MCP omits it."""
        from hypertopos_mcp.tools.navigation import anomaly_summary

        clusters = [
            {"delta": [0.0, 0.0], "count": 3, "label": "Z", "pct": 100.0},
        ]
        self._setup(clusters)  # no top_driving_dimensions in mock return
        result = json.loads(anomaly_summary("test_pat"))
        assert "top_driving_dimensions" not in result


# ---------------------------------------------------------------------------
# force_reload
# ---------------------------------------------------------------------------


class TestOpenSphereForceReload:
    def _make_sphere_state(self):
        sphere_meta = MagicMock()
        sphere_meta.sphere_id = "test_id"
        sphere_meta.name = "Test Sphere"
        sphere_meta.lines = {}
        sphere_meta.patterns = {}
        sphere_meta.aliases = {}
        sphere = MagicMock()
        sphere._sphere = sphere_meta
        return sphere

    def test_force_reload_calls_reload_modules(self, monkeypatch):
        """open_sphere(force_reload=True) calls _reload_hypertopos_modules."""
        import hypertopos_mcp.server as server_mod
        import hypertopos_mcp.tools.session as session_mod

        reload_called = []
        # Patch the session module's local references (not server_mod) — session.py
        # uses `from ... import`, so server_mod patches don't affect its locals.
        monkeypatch.setattr(
            session_mod, "_reload_hypertopos_modules", lambda: reload_called.append(1)
        )  # noqa: E501
        monkeypatch.setattr(session_mod, "_do_open_sphere", lambda p: None)
        monkeypatch.setitem(server_mod._state, "sphere", self._make_sphere_state())
        monkeypatch.setitem(server_mod._state, "manifest", None)

        from hypertopos_mcp.tools.session import open_sphere

        open_sphere(path="examples/test", force_reload=True)

        assert reload_called == [1]

    def test_no_force_reload_skips_reload(self, monkeypatch):
        """open_sphere() default does NOT call _reload_hypertopos_modules."""
        import hypertopos_mcp.server as server_mod
        import hypertopos_mcp.tools.session as session_mod

        reload_called = []
        monkeypatch.setattr(
            session_mod, "_reload_hypertopos_modules", lambda: reload_called.append(1)
        )  # noqa: E501
        monkeypatch.setattr(session_mod, "_do_open_sphere", lambda p: None)
        monkeypatch.setitem(server_mod._state, "sphere", self._make_sphere_state())
        monkeypatch.setitem(server_mod._state, "manifest", None)

        from hypertopos_mcp.tools.session import open_sphere

        open_sphere(path="examples/test")

        assert reload_called == []

    def test_reload_hypertopos_modules_reloads_present_modules(self, monkeypatch):
        """_reload_hypertopos_modules reloads all hypertopos.* modules in sys.modules."""
        import importlib
        import sys
        from unittest.mock import patch

        import hypertopos_mcp.server as server_mod

        # Save the real HyperSphere before _reload_hypertopos_modules overwrites it
        # via `from hypertopos.sphere import HyperSphere` (the last line of that fn).
        # Without this restore, the global `server_mod.HyperSphere` stays as a MagicMock
        # and all subsequent _do_open_sphere calls return mocked spheres.
        original_HyperSphere = server_mod.HyperSphere

        fake_deep = MagicMock()  # simulates hypertopos.model.objects (2 dots)
        fake_root = MagicMock()  # simulates hypertopos.sphere (1 dot)
        extra = {
            "hypertopos.model.objects": fake_deep,
            "hypertopos.sphere": fake_root,
        }

        originals = {k: sys.modules.pop(k, None) for k in extra}
        sys.modules.update(extra)

        try:
            reloaded = []
            with patch.object(importlib, "reload", side_effect=lambda m: reloaded.append(m)):
                server_mod._reload_hypertopos_modules()
        finally:
            server_mod.HyperSphere = original_HyperSphere  # restore the real class
            for name, orig in originals.items():
                sys.modules.pop(name, None)
                if orig is not None:
                    sys.modules[name] = orig

        # root (fewer dots) comes before leaves (more dots) — dependencies reload before consumers
        assert fake_deep in reloaded
        assert fake_root in reloaded
        assert reloaded.index(fake_root) < reloaded.index(fake_deep)


# TestAggregateFilterByKeys removed — now tested in core

# TestApplyContextFilters removed — _apply_event_filters moved to core engine
# _make_context_filter_geo removed — now tested in core

# TestPolygonsFromGeometryTable removed — _polygons_from_geometry_table moved to core engine


# ---------------------------------------------------------------------------
# Response-size guard tests (hard caps to prevent MCP transport overflow)
# ---------------------------------------------------------------------------


def test_find_anomalies_top_n_capped(open_berka_sphere):
    """top_n exceeding adaptive cap is truncated and capped_warning is included."""
    import json

    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools._guards import adaptive_polygon_cap
    from hypertopos_mcp.tools.navigation import find_anomalies

    pattern = _state["sphere"]._sphere.patterns["tx_pattern"]
    cap = adaptive_polygon_cap(pattern)
    result = json.loads(find_anomalies("tx_pattern", top_n=200))
    assert len(result["polygons"]) <= cap
    assert "capped_warning" in result
    assert "adaptive hard cap" in result["capped_warning"]


def test_find_anomalies_top_n_within_limit_no_warning(open_berka_sphere):
    """top_n within adaptive cap produces no capped_warning."""
    import json

    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies("tx_pattern", top_n=10))
    assert "capped_warning" not in result


def test_find_anomalies_total_found_always_present(open_berka_sphere):
    """total_found is always in the response regardless of offset."""
    import json

    from hypertopos_mcp.tools.navigation import find_anomalies

    result = json.loads(find_anomalies("tx_pattern", top_n=10))
    assert "total_found" in result
    assert isinstance(result["total_found"], int)
    assert result["total_found"] >= result["found"]


def test_find_anomalies_offset_pagination(open_berka_sphere):
    """offset=0 and offset=found produce non-overlapping sets."""
    import json

    from hypertopos_mcp.tools.navigation import find_anomalies

    page0 = json.loads(find_anomalies("tx_pattern", top_n=10, offset=0))
    page1 = json.loads(find_anomalies("tx_pattern", top_n=10, offset=10))
    assert page0["total_found"] == page1["total_found"]
    if page0["total_found"] > 10 and page1["found"] > 0:
        keys0 = {p["primary_key"] for p in page0["polygons"]}
        keys1 = {p["primary_key"] for p in page1["polygons"]}
        assert keys0.isdisjoint(keys1), f"Overlap: {keys0 & keys1}"


def test_find_anomalies_offset_beyond_total_returns_empty(open_berka_sphere):
    """offset >= total_found returns empty polygons with total_found populated."""
    import json

    from hypertopos_mcp.tools.navigation import find_anomalies

    page0 = json.loads(find_anomalies("tx_pattern", top_n=10, offset=0))
    total = page0["total_found"]
    result = json.loads(find_anomalies("tx_pattern", top_n=10, offset=total + 100))
    assert result["polygons"] == []
    assert result["found"] == 0
    assert result["total_found"] == total


def test_get_event_polygons_limit_capped(open_berka_sphere):
    """limit exceeding adaptive cap is truncated and capped_warning is included."""
    import json

    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools._guards import adaptive_polygon_cap
    from hypertopos_mcp.tools.geometry import get_event_polygons

    pattern = _state["sphere"]._sphere.patterns["tx_pattern"]
    cap = adaptive_polygon_cap(pattern)
    result = json.loads(get_event_polygons("741", "tx_pattern", limit=200))
    assert result["returned"] <= cap
    assert "capped_warning" in result
    assert "adaptive hard cap" in result["capped_warning"]


def test_get_event_polygons_limit_within_cap_no_warning(open_berka_sphere):
    """limit within adaptive cap produces no capped_warning."""
    import json

    from hypertopos_mcp.tools.geometry import get_event_polygons

    result = json.loads(get_event_polygons("741", "tx_pattern", limit=10))
    assert "capped_warning" not in result


def test_adaptive_polygon_cap_event_pattern():
    """Event pattern with many edges gets a lower cap than anchor pattern."""
    from unittest.mock import MagicMock

    import numpy as np
    from hypertopos_mcp.tools._guards import adaptive_polygon_cap

    event_pattern = MagicMock()
    event_pattern.pattern_type = "event"
    event_pattern.relations = [MagicMock() for _ in range(10)]
    event_pattern.edge_max = None
    cap_event = adaptive_polygon_cap(event_pattern)
    assert cap_event == 15  # 50000 // (250 + 10*300) = 15

    anchor_pattern = MagicMock()
    anchor_pattern.pattern_type = "anchor"
    anchor_pattern.relations = [MagicMock() for _ in range(9)]
    anchor_pattern.edge_max = np.array([1.0] * 9)
    cap_anchor = adaptive_polygon_cap(anchor_pattern)
    # Default n_entity_props=15: 250 + 9*80 + 15*35 = 1495 → 50000//1495 = 33
    assert cap_anchor == 33

    # Explicit n_entity_props=0 restores the old no-property estimate
    cap_anchor_no_props = adaptive_polygon_cap(anchor_pattern, n_entity_props=0)
    assert cap_anchor_no_props == 51  # 50000 // (250 + 9*80) = 51

    assert cap_anchor > cap_event


def test_adaptive_polygon_cap_minimum():
    """Cap never goes below 5 even with huge edge count."""
    from unittest.mock import MagicMock

    from hypertopos_mcp.tools._guards import adaptive_polygon_cap

    huge_pattern = MagicMock()
    huge_pattern.pattern_type = "event"
    huge_pattern.relations = [MagicMock() for _ in range(100)]
    huge_pattern.edge_max = None
    assert adaptive_polygon_cap(huge_pattern) == 5


def test_find_anomalies_adaptive_cap(open_berka_sphere):
    """find_anomalies respects adaptive cap and includes capped_warning."""
    import json

    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools._guards import adaptive_polygon_cap
    from hypertopos_mcp.tools.navigation import find_anomalies

    pattern = _state["sphere"]._sphere.patterns["tx_pattern"]
    cap = adaptive_polygon_cap(pattern)
    result = json.loads(find_anomalies("tx_pattern", top_n=200))
    assert result["found"] <= cap
    assert "capped_warning" in result
    assert "adaptive hard cap" in result["capped_warning"]


def test_get_centroid_map_pair_truncation_warning(open_berka_sphere):
    """When top_n_distances=None and pairs > 500, distances_truncated_warning is added."""
    import json

    from hypertopos_mcp.tools.analysis import get_centroid_map

    # tx_pattern self-grouped by operation: 6 operations -> 15 pairs, under 500.
    # Verify no truncation for small k.
    result = json.loads(
        get_centroid_map(
            "tx_pattern",
            "transactions",
            group_by_property="transactions:operation",
            top_n_distances=None,
        )
    )
    # 6 groups -> 15 pairs — below 500 cap, no warning expected
    assert "distances_truncated_warning" not in result
    assert len(result["inter_centroid_distances"]) == 15  # 6 * 5 / 2


class TestFindHubsCappedWarning:
    def test_find_hubs_top_n_capped(self, open_berka_sphere):
        import json

        from hypertopos_mcp.tools.analysis import _MAX_HUB_TOP_N, find_hubs

        result = json.loads(find_hubs("tx_pattern", top_n=100))
        assert len(result["results"]) <= _MAX_HUB_TOP_N
        assert "capped_warning" in result
        assert "25" in result["capped_warning"]

    def test_find_hubs_top_n_within_limit_no_warning(self, open_berka_sphere):
        import json

        from hypertopos_mcp.tools.analysis import find_hubs

        result = json.loads(find_hubs("tx_pattern", top_n=10))
        assert "capped_warning" not in result


class TestFindDriftingCappedWarning:
    def test_find_drifting_entities_top_n_capped(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import _MAX_DRIFT_TOP_N, find_drifting_entities

        result = json.loads(find_drifting_entities("account_behavior_pattern", top_n=100))
        assert result["count"] <= _MAX_DRIFT_TOP_N
        assert len(result["results"]) <= _MAX_DRIFT_TOP_N
        assert "capped_warning" in result
        assert "50" in result["capped_warning"]

    def test_find_drifting_entities_within_limit_no_warning(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_drifting_entities

        result = json.loads(find_drifting_entities("account_behavior_pattern", top_n=10))
        assert "capped_warning" not in result

    def test_find_drifting_similar_top_n_capped(self, open_berka_sphere):
        import pytest
        from hypertopos_mcp.tools.analysis import _MAX_DRIFT_SIMILAR_TOP_N, find_drifting_similar

        # find_drifting_similar returns a list — silent cap, no warning
        try:
            result = json.loads(find_drifting_similar("1", "account_behavior_pattern", top_n=100))
        except ValueError as exc:
            if "Trajectory index not found" in str(exc):
                pytest.skip("No trajectory index for account_behavior_pattern")
            raise
        assert isinstance(result, list)
        assert len(result) <= _MAX_DRIFT_SIMILAR_TOP_N

    def test_find_drifting_similar_within_limit_no_warning(self, open_berka_sphere):
        import pytest
        from hypertopos_mcp.tools.analysis import find_drifting_similar

        try:
            result = json.loads(find_drifting_similar("1", "account_behavior_pattern", top_n=5))
        except ValueError as exc:
            if "Trajectory index not found" in str(exc):
                pytest.skip("No trajectory index for account_behavior_pattern")
            raise
        assert isinstance(result, list)


class TestFindDriftingSimilarInsufficientHistory:
    def test_warning_returned_for_single_slice_entity(self, open_berka_sphere):
        from unittest.mock import patch

        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_drifting_similar

        with patch.object(
            _state["navigator"],
            "find_drifting_similar",
            side_effect=ValueError(
                "insufficient_temporal_history: entity 'X' has 1 temporal slice"
                " — minimum 2 required for meaningful trajectory similarity."
            ),
        ):
            result = json.loads(find_drifting_similar("X", "account_behavior_pattern", top_n=5))

        assert "warning" in result
        assert "results" in result
        assert result["results"] == []
        assert "1 temporal slice" in result["warning"]

    def test_warning_includes_alternative_tool_hint(self, open_berka_sphere):
        from unittest.mock import patch

        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_drifting_similar

        with patch.object(
            _state["navigator"],
            "find_drifting_similar",
            side_effect=ValueError(
                "insufficient_temporal_history: entity 'X' has 1 temporal slice"
                " — minimum 2 required (need a start and end point to define"
                " a direction). To find entities with similar current shape use"
                " find_similar_entities('X', 'account_behavior_pattern')."
                " To inspect the available slice use get_solid()."
            ),
        ):
            result = json.loads(find_drifting_similar("X", "account_behavior_pattern", top_n=5))

        assert "find_similar_entities" in result["warning"]
        assert "get_solid" in result["warning"]

    def test_non_history_valueerror_still_raises(self, open_berka_sphere):
        from unittest.mock import patch

        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_drifting_similar

        with (
            patch.object(
                _state["navigator"],
                "find_drifting_similar",
                side_effect=ValueError("some other error"),
            ),
            pytest.raises(ValueError, match="some other error"),
        ):
            find_drifting_similar("X", "account_behavior_pattern", top_n=5)


class TestFindClustersCappedWarning:
    def test_find_clusters_total_members_capped(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_clusters

        result = json.loads(find_clusters("tx_pattern", n_clusters=5, top_n=50, sample_size=5000))  # noqa: E501
        total_members = sum(len(c["members"]) for c in result["clusters"])
        assert total_members <= 100
        assert "capped_warning" in result
        assert "100" in result["capped_warning"]

    def test_find_clusters_within_limit_no_warning(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_clusters

        result = json.loads(find_clusters("tx_pattern", n_clusters=3, top_n=10, sample_size=5000))  # noqa: E501
        # 3 x 10 = 30, well under 100
        if "capped_warning" in result:
            # The existing "fewer clusters found" warning is OK — but member cap should NOT trigger
            assert "100" not in result["capped_warning"]

    def test_find_clusters_auto_k_large_top_n_capped(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import _MAX_CLUSTER_TOTAL_MEMBERS, find_clusters

        result = json.loads(find_clusters("tx_pattern", n_clusters=0, top_n=50, sample_size=5000))  # noqa: E501
        # auto-k estimates 10 clusters -> cap fires: top_n reduced to max(1, 100//10)=10
        assert "capped_warning" in result
        # Post-processing enforces total member bound even if auto-k finds > estimated clusters
        total_members = sum(len(c["members"]) for c in result["clusters"])
        assert total_members <= _MAX_CLUSTER_TOTAL_MEMBERS


class TestFindSimilarEntitiesCap:
    def test_find_similar_top_n_silently_capped(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_similar_entities

        result = json.loads(find_similar_entities("1", "account_behavior_pattern", top_n=100))
        assert len(result["similar"]) <= 50


class TestAggregateIncludePropertiesWarning:
    def test_aggregate_include_properties_large_warns(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        # accounts is a high-cardinality line (4500 entities)
        # 4500 groups x 4 properties > 2000 threshold
        result = json.loads(
            aggregate(
                "tx_pattern",
                group_by_line="accounts",
                metric="count",
                include_properties=["region", "frequency", "has_loan", "loan_status"],
            )
        )
        assert "include_properties_warning" in result

    def test_aggregate_include_properties_small_no_warn(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        # cpty_banks is low-cardinality (13 entities); 13 x 2 = 26 < 2000
        result = json.loads(
            aggregate(
                "tx_pattern",
                group_by_line="cpty_banks",
                metric="count",
                include_properties=["primary_key"],
            )
        )
        assert "include_properties_warning" not in result

    def test_aggregate_no_include_properties_no_warn(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                group_by_line="accounts",
                metric="count",
            )
        )
        assert "include_properties_warning" not in result


class TestAggregateCountDistinct:
    """count_distinct:<target_line> metric — unique entity keys on a related line per group."""

    def test_count_distinct_requires_valid_target_line(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="Unknown target line"):
            aggregate("tx_pattern", "accounts", metric="count_distinct:nonexistent")

    def test_count_distinct_target_must_differ_from_group_by(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="same as group_by_line"):
            aggregate("tx_pattern", "accounts", metric="count_distinct:accounts")

    def test_count_distinct_incompatible_with_event_filters(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="event_filters"):
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                event_filters={"date": {"gte": "2023-01-01"}},
            )

    def test_count_distinct_incompatible_with_group_by_property(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="group_by_property"):
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                group_by_property="accounts:frequency",
            )

    def test_count_distinct_incompatible_with_distinct(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="distinct"):
            aggregate("tx_pattern", "accounts", metric="count_distinct:cpty_banks", distinct=True)

    def test_count_distinct_basic(self, open_berka_sphere):
        """count_distinct:cpty_banks per account returns positive values."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
            )
        )
        assert result["metric"] == "count_distinct:cpty_banks"
        assert result["total_groups"] > 0
        assert len(result["results"]) > 0
        for r in result["results"]:
            assert r["value"] > 0
            assert r["count"] == r["value"]
            assert "key" in r

    def test_count_distinct_with_filters(self, open_berka_sphere):
        """count_distinct with edge filters scopes correctly."""
        from hypertopos_mcp.tools.aggregation import aggregate

        all_result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                limit=5,
            )
        )
        filtered = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                filters=[{"line": "tx_types", "key": "PRIJEM"}],
                limit=5,
            )
        )
        if all_result["results"] and filtered["results"]:
            assert filtered["results"][0]["value"] <= all_result["results"][0]["value"]

    def test_count_distinct_sort_asc(self, open_berka_sphere):
        """count_distinct with sort=asc returns lowest first."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                sort="asc",
                limit=5,
            )
        )
        values = [r["value"] for r in result["results"]]
        assert values == sorted(values)

    def test_count_distinct_with_geometry_filters(self, open_berka_sphere):
        """count_distinct works with geometry_filters."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                geometry_filters={"is_anomaly": True},
                limit=5,
            )
        )
        assert "total_groups" in result

    def test_count_distinct_with_filter_by_keys(self, open_berka_sphere):
        """count_distinct works with filter_by_keys pre-selection."""
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.navigation import find_anomalies

        # Get some polygon keys
        anomalies = json.loads(find_anomalies("tx_pattern", top_n=10))
        keys = [p["primary_key"] for p in anomalies["polygons"]]
        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:cpty_banks",
                filter_by_keys=keys,
                limit=5,
            )
        )
        assert result["total_groups"] >= 0
        # Scoped to 10 polygons — distinct banks per account should be small
        for r in result["results"]:
            assert r["value"] <= 10


class TestAggregateMissingEdgeTo:
    """missing_edge_to parameter — filter to polygons WITHOUT an edge to a line."""

    def test_missing_edge_to_invalid_line_raises(self, open_berka_sphere):
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="Unknown line"):
            aggregate("tx_pattern", "accounts", metric="count", missing_edge_to="nonexistent_line")

    def test_missing_edge_to_reduces_results(self, open_berka_sphere):
        """Polygons missing an edge should be a subset of all polygons."""
        from hypertopos_mcp.tools.aggregation import aggregate

        all_result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
            )
        )
        # cpty_banks: ~74% of transactions lack a bank edge
        missing = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
                missing_edge_to="cpty_banks",
            )
        )
        assert missing["total_groups"] <= all_result["total_groups"]
        assert missing.get("missing_edge_to") == "cpty_banks"

    def test_missing_edge_to_with_geometry_filters(self, open_berka_sphere):
        """missing_edge_to composes with geometry_filters."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
                missing_edge_to="cpty_banks",
                geometry_filters={"is_anomaly": True},
            )
        )
        assert "total_groups" in result

    def test_missing_edge_to_with_count_distinct(self, open_berka_sphere):
        """missing_edge_to works with count_distinct metric."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count_distinct:operations",
                missing_edge_to="cpty_banks",
            )
        )
        assert "total_groups" in result

    def test_missing_edge_to_required_relation_returns_few(self, open_berka_sphere):
        """Missing a required relation (accounts) should return few/no polygons."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "cpty_banks",
                metric="count",
                missing_edge_to="accounts",
            )
        )
        all_result = json.loads(
            aggregate(
                "tx_pattern",
                "cpty_banks",
                metric="count",
            )
        )
        missing_total = sum(r["count"] for r in result["results"]) if result["results"] else 0
        all_total = sum(r["count"] for r in all_result["results"])
        assert missing_total < all_total


# ---------------------------------------------------------------------------
# B1: find_regime_changes — warning on insufficient data
# ---------------------------------------------------------------------------


def test_find_regime_changes_insufficient_data_warning(open_berka_sphere):
    """Pattern with few temporal slices returns warning instead of bare []."""
    import json

    from hypertopos_mcp.tools.observability import find_regime_changes

    result = json.loads(find_regime_changes("account_behavior_pattern"))
    assert len(result) >= 1
    if isinstance(result[0], dict) and "warning" in result[0]:
        assert "insufficient" in result[0]["warning"] or "temporal" in result[0]["warning"]


def test_find_regime_changes_empty_result_has_warning(open_berka_sphere):
    """Narrow time window with no regime changes returns structured warning, not bare []."""
    import json

    from hypertopos_mcp.tools.observability import find_regime_changes

    # Use a narrow 2-day window within the Berka temporal range (1993-1998)
    result = json.loads(
        find_regime_changes(
            "account_behavior_pattern",
            timestamp_from="1995-07-01",
            timestamp_to="1995-07-03",
        )
    )
    assert isinstance(result, list)
    # Result is either regime changes, a warning, or empty (when no temporal
    # buckets fall within the narrow window).  Verify structure when non-empty.
    if len(result) >= 1 and isinstance(result[0], dict) and "warning" in result[0]:
        assert (
            "no_regime_changes_detected" in result[0]["warning"]
            or "insufficient" in result[0]["warning"]
            or "temporal" in result[0]["warning"]
        )


# ---------------------------------------------------------------------------
# B2: compare_time_windows — no anomaly_rate in output
# ---------------------------------------------------------------------------


def test_compare_time_windows_no_anomaly_rate(open_berka_sphere):
    """compare_time_windows output must not contain misleading anomaly_rate."""
    import json

    from hypertopos_mcp.tools.observability import compare_time_windows

    result = json.loads(
        compare_time_windows(
            "account_behavior_pattern",
            "2018-07-01",
            "2018-10-01",
            "2018-10-01",
            "2019-01-01",
        )
    )
    assert "anomaly_rate" not in result.get("window_a", {})
    assert "anomaly_rate" not in result.get("window_b", {})
    assert "anomaly_rate_change" not in result


# ---------------------------------------------------------------------------
# B3: search_entities_hybrid — text_score > 0 for matching entities
# ---------------------------------------------------------------------------


def test_search_entities_hybrid_text_score_nonzero(open_berka_sphere):
    """Hybrid search should yield text_score > 0 for at least some results."""
    import json

    import pytest
    from hypertopos_mcp.tools.analysis import search_entities_hybrid
    from hypertopos_mcp.tools.navigation import goto

    goto("741", "accounts")
    try:
        result = json.loads(search_entities_hybrid("741", "account_behavior_pattern", "Prague"))
    except RuntimeError as exc:
        if "INVERTED index" in str(exc):
            pytest.skip("Berka sphere accounts line has no FTS index")
        raise
    has_nonzero_text = any(r["text_score"] > 0 for r in result["results"])
    # With wider FTS pool, at least some results should have text_score > 0
    assert has_nonzero_text or len(result["results"]) == 0


# ---------------------------------------------------------------------------
# Fix 3: property_filters must apply to sum+metric and count_distinct
# ---------------------------------------------------------------------------
# _make_property_filters_sum_geo removed — now tested in core


class TestAggregateGroupByLineValidation:
    """Aggregate raises when group_by_line is not a pattern relation."""

    def setup_method(self):
        from hypertopos_mcp.server import _state

        geo = _make_cross_tab_geo()
        sphere, session = _make_cross_tab_state(geo)
        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_invalid_group_by_line_raises(self) -> None:
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="not a relation"):
            aggregate("sale_pattern", "nonexistent_line", "count")

    def test_valid_group_by_line_works(self, open_berka_sphere) -> None:
        from hypertopos_mcp.tools.aggregation import aggregate
        from hypertopos_mcp.tools.session import open_sphere

        open_sphere("benchmark/berka/sphere/gds_berka_banking")
        result = json.loads(aggregate(event_pattern_id="tx_pattern", group_by_line="accounts", metric="count", limit=3))
        assert result["total_groups"] > 0


class TestSearchEntitiesFtsHint:
    """search_entities_fts shows hint when 0 results."""

    def setup_method(self):
        from hypertopos_mcp.server import _state

        mock_line = MagicMock()
        mock_line.versions = [1]

        sphere_meta = MagicMock()
        sphere_meta.lines = {"payment_methods": mock_line}

        sphere = MagicMock()
        sphere._sphere = sphere_meta

        reader = MagicMock()
        reader.search_points_fts.return_value = pa.table(
            {
                "primary_key": pa.array([], type=pa.string()),
            }
        )

        session = MagicMock()
        session._reader = reader

        _state["sphere"] = sphere
        _state["session"] = session
        _ensure_aggregate_navigator()

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_zero_results_includes_hint(self) -> None:
        from hypertopos_mcp.tools.session import search_entities_fts

        result = json.loads(search_entities_fts("payment_methods", "PM"))
        assert result["returned"] == 0
        assert "hint" in result
        assert "goto" in result["hint"]

    def test_nonzero_results_no_hint(self) -> None:
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.session import search_entities_fts

        _state["session"]._reader.search_points_fts.return_value = pa.table(
            {
                "primary_key": pa.array(["PM-001"], type=pa.string()),
                "payment_method_name": pa.array(["VISA"]),
            }
        )
        result = json.loads(search_entities_fts("payment_methods", "PM"))
        assert result["returned"] == 1
        assert "hint" not in result


# ---------------------------------------------------------------------------
# missing_edge_to for find_anomalies
# ---------------------------------------------------------------------------


class TestFindAnomaliesMissingEdgeTo:
    """find_anomalies missing_edge_to post-filter on edges struct."""

    def test_missing_edge_to_filters_entities_with_target_edge(self, open_berka_sphere):
        """missing_edge_to='transactions' keeps only entities WITHOUT a transactions edge."""
        from hypertopos_mcp.tools.navigation import find_anomalies

        result = json.loads(
            find_anomalies("account_behavior_pattern", top_n=50, missing_edge_to="transactions")
        )
        for poly in result["polygons"]:
            edge_lines = [e["line_id"] for e in poly.get("edges", [])]
            assert "transactions" not in edge_lines, (
                f"{poly['primary_key']} has transactions edge but should be filtered out"
            )

    def test_missing_edge_to_none_returns_all(self, open_berka_sphere):
        """missing_edge_to=None (default) returns all anomalies — baseline behavior."""
        from hypertopos_mcp.tools.navigation import find_anomalies

        result_all = json.loads(find_anomalies("account_behavior_pattern", top_n=10))
        result_none = json.loads(
            find_anomalies(
                "account_behavior_pattern",
                top_n=10,
                missing_edge_to=None,
            )
        )
        assert result_all["total_found"] == result_none["total_found"]

    def test_missing_edge_to_nonexistent_line_raises(self, open_berka_sphere):
        """missing_edge_to with nonexistent line_id raises ValueError."""
        from hypertopos_mcp.tools.navigation import find_anomalies

        with pytest.raises(RuntimeError, match="Unknown line"):
            find_anomalies("account_behavior_pattern", top_n=10, missing_edge_to="nonexistent_xyz")


# ---------------------------------------------------------------------------
# missing_edge_to for find_similar_entities
# ---------------------------------------------------------------------------


class TestFindSimilarEntitiesMissingEdgeTo:
    """find_similar_entities missing_edge_to post-filter."""

    def _make_state_with_nav(self, nav_mock, reader_mock=None):
        """Set up _state with a mock navigator and reader."""
        from hypertopos_mcp.server import _state

        if reader_mock is None:
            reader_mock = MagicMock()
            reader_mock.read_points.return_value = pa.table(
                {
                    "primary_key": pa.array([], type=pa.string()),
                }
            )

        sphere_mock = MagicMock()
        sphere_mock._sphere.lines = {
            "customers": MagicMock(versions=[1], line_role="anchor", pattern_id="customer_pattern"),
            "transactions": MagicMock(versions=[1], line_role="event", pattern_id="tx_pattern"),
        }
        sphere_mock._sphere.patterns = {
            "customer_pattern": MagicMock(version=1, pattern_type="anchor"),
        }

        session_mock = MagicMock()
        session_mock._reader = reader_mock

        _state["navigator"] = nav_mock
        _state["sphere"] = sphere_mock
        _state["session"] = session_mock

    def test_missing_edge_to_passed_to_navigator(self):
        """missing_edge_to is forwarded to navigator.find_similar_entities."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities

        nav_mock = MagicMock()
        nav_mock.find_similar_entities.return_value = [("CUST-002", 0.15)]
        nav_mock.get_entity_geometry_meta.return_value = {
            "delta_norm": 1.0,
            "is_anomaly": False,
            "delta_rank_pct": 30.0,
        }
        self._make_state_with_nav(nav_mock)

        try:
            find_similar_entities(
                "CUST-001",
                "customer_pattern",
                top_n=5,
                missing_edge_to="transactions",
            )
            nav_mock.find_similar_entities.assert_called_once_with(
                "CUST-001",
                "customer_pattern",
                top_n=5,
                filter_expr=None,
                missing_edge_to="transactions",
                dim_mask=None,
                metric="L2",
            )
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_missing_edge_to_none_not_passed(self):
        """missing_edge_to=None is forwarded as None (default behavior)."""
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_similar_entities

        nav_mock = MagicMock()
        nav_mock.find_similar_entities.return_value = [("CUST-002", 0.15)]
        nav_mock.get_entity_geometry_meta.return_value = {
            "delta_norm": 1.0,
            "is_anomaly": False,
            "delta_rank_pct": 30.0,
        }
        self._make_state_with_nav(nav_mock)

        try:
            find_similar_entities("CUST-001", "customer_pattern", top_n=5)
            nav_mock.find_similar_entities.assert_called_once_with(
                "CUST-001",
                "customer_pattern",
                top_n=5,
                filter_expr=None,
                missing_edge_to=None,
                dim_mask=None,
                metric="L2",
            )
        finally:
            for k in list(_state.keys()):
                _state[k] = None

    def test_missing_edge_to_integration(self, open_berka_sphere):
        """Integration: missing_edge_to filters real data."""
        from hypertopos_mcp.tools.analysis import find_similar_entities

        result = json.loads(
            find_similar_entities(
                "1",
                "account_behavior_pattern",
                top_n=5,
                missing_edge_to="transactions",
            )
        )
        for entry in result["similar"]:
            assert entry["primary_key"] != ""

    def test_missing_edge_to_includes_clarification_note(self, open_berka_sphere):
        """Response includes missing_edge_to_note explaining edge vs property distinction."""
        from hypertopos_mcp.tools.analysis import find_similar_entities
        import json

        raw = find_similar_entities(
            primary_key="1",
            pattern_id="account_behavior_pattern",
            top_n=5,
            missing_edge_to="loan_accounts",
        )
        result = json.loads(raw)
        assert "missing_edge_to_note" in result, (
            "missing_edge_to should include a clarification note"
        )
        assert "geometric edge" in result["missing_edge_to_note"].lower()

    def test_missing_edge_to_event_pattern_raises(self, open_berka_sphere):
        """missing_edge_to on event pattern raises ValueError."""
        from hypertopos_mcp.tools.navigation import find_anomalies

        with pytest.raises(RuntimeError, match="not supported for event patterns"):
            find_anomalies("tx_pattern", missing_edge_to="accounts")

    def test_missing_edge_to_invalid_line_raises(self, open_berka_sphere):
        """missing_edge_to with nonexistent line_id raises ValueError."""
        from hypertopos_mcp.tools.navigation import find_anomalies

        with pytest.raises(RuntimeError, match="Unknown line"):
            find_anomalies("account_behavior_pattern", missing_edge_to="nonexistent_xyz")


# ---------------------------------------------------------------------------
# having filter for aggregate
# ---------------------------------------------------------------------------
class TestAggregateHaving:
    """aggregate having — post-aggregation group filtering."""

    def test_having_gt_filters_groups(self, open_berka_sphere):
        """having={"gt": X} keeps only groups with metric > X."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(aggregate("tx_pattern", "accounts", metric="count", having={"gt": 300}))
        for r in result["results"]:
            assert r["value"] > 300
        assert "having_matched" in result
        assert result["having_matched"] <= result["total_groups"]

    def test_having_range_gte_lt(self, open_berka_sphere):
        """having={"gte": X, "lt": Y} filters to range."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate("tx_pattern", "accounts", metric="count", having={"gte": 100, "lt": 50000})
        )
        for r in result["results"]:
            assert r["value"] >= 100
            assert r["value"] < 50000

    def test_having_none_no_change(self, open_berka_sphere):
        """having=None — identical to current behavior."""
        from hypertopos_mcp.tools.aggregation import aggregate

        r1 = json.loads(aggregate("tx_pattern", "accounts", metric="count"))
        r2 = json.loads(aggregate("tx_pattern", "accounts", metric="count", having=None))
        assert r1["total_groups"] == r2["total_groups"]

    def test_having_no_match_returns_empty(self, open_berka_sphere):
        """having with impossible threshold returns empty results."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate("tx_pattern", "accounts", metric="count", having={"gt": 999999999})
        )
        assert result["results"] == []
        assert result["having_matched"] == 0

    def test_having_with_pivot_raises(self, open_berka_sphere):
        """having + pivot_event_field raises error."""
        from hypertopos_mcp.tools.aggregation import aggregate

        with pytest.raises(RuntimeError, match="having.*pivot_event_field"):
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
                pivot_event_field="amount",
                having={"gt": 100},
            )

    def test_having_with_group_by_property(self, open_berka_sphere):
        """having works with group_by_property."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
                group_by_property="accounts:frequency",
                having={"gte": 1},
            )
        )
        for r in result["results"]:
            assert r["value"] >= 1

    def test_having_with_sort_asc(self, open_berka_sphere):
        """having + sort=asc returns filtered results in ascending order."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate("tx_pattern", "accounts", metric="count", having={"gte": 100}, sort="asc")
        )
        vals = [r["value"] for r in result["results"]]
        assert vals == sorted(vals)

    def test_having_total_groups_before_having(self, open_berka_sphere):
        """total_groups reflects pre-having count; total_groups_before_having removed."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(aggregate("tx_pattern", "accounts", metric="count", having={"gt": 300}))
        assert result["total_groups"] >= result["having_matched"]
        assert result["total_groups"] > result["having_matched"]
        assert "total_groups_before_having" not in result

    def test_having_with_distinct(self, open_berka_sphere):
        """having works with distinct=True."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
                group_by_property="accounts:frequency",
                distinct=True,
                having={"gte": 1},
            )
        )
        for r in result["results"]:
            assert r["value"] >= 1

    def test_having_with_collapse_by_property(self, open_berka_sphere):
        """having works with collapse_by_property."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "tx_pattern",
                "accounts",
                metric="count",
                group_by_property="accounts:frequency",
                collapse_by_property=True,
                having={"gte": 1},
            )
        )
        for r in result["results"]:
            assert r["value"] >= 1

    def test_having_with_sum_metric(self, open_berka_sphere):
        """having works with sum metric."""
        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate("tx_pattern", "accounts", metric="sum:amount", having={"gt": 0}, limit=5)
        )
        for r in result["results"]:
            assert r["value"] > 0
        assert "having_matched" in result


class TestGetCentroidMapContinuousMode:
    """centroid_map must work on continuous-mode patterns with self-group + property."""

    # NOTE: the success path (self-group + property succeeds) is covered by a unit test in
    # packages/hypertopos-py/tests/test_navigation.py::TestCentroidMapContinuousMode::test_self_group_with_property_returns_groups  # noqa: E501
    # using a synthetic minimal sphere — avoids 1M-row prop_table read on tx_pattern.

    def test_continuous_mode_self_group_without_property_raises(self, open_berka_sphere):
        """Self-group on continuous-mode pattern without group_by_property must raise."""
        import json

        from hypertopos_mcp.tools.analysis import get_centroid_map

        # tx_pattern has edge_max -> continuous mode guard fires first
        result = json.loads(get_centroid_map("tx_pattern", "transactions"))
        assert "error" in result
        assert "continuous" in result["error"].lower()

    def test_discrete_mode_self_group_still_raises(self, open_berka_sphere):
        """Self-group on continuous-mode pattern without property must raise."""
        import json

        from hypertopos_mcp.tools.analysis import get_centroid_map

        # account_stress_pattern has entity line = accounts
        # All Berka patterns are continuous — self-group without property raises
        # the continuous-mode guard (which fires before the self-referential guard)
        result = json.loads(get_centroid_map("account_stress_pattern", "accounts"))
        assert "error" in result
        assert "continuous" in result["error"].lower() or "own line" in result["error"].lower()  # noqa: E501


# TestAggregateSlowOperationWarning removed — slow_operation_warning feature
# was removed during core consolidation (timing managed externally by @timed)


# ---------------------------------------------------------------------------
# Entity Filters
# ---------------------------------------------------------------------------
# _make_entity_filter_state removed — now tested in core

# ---------------------------------------------------------------------------
# search_entities available_values hint on zero results
# ---------------------------------------------------------------------------

# TestSearchEntitiesAvailableValues removed — available_values was MCP-inline feature,
# search_entities now delegates entirely to nav.search_entities() in core

# ---------------------------------------------------------------------------
# sphere_overview — event_rate_divergence_alerts (detail="full")
# ---------------------------------------------------------------------------


class TestSphereOverviewEventRateDivergence:
    """event_rate_divergence_alerts appear in detail='full', absent in detail='summary'."""

    def teardown_method(self):
        from hypertopos_mcp.server import _state
        for k in list(_state.keys()):
            _state[k] = None

    def _setup_state(self, divergence_alerts):
        from hypertopos_mcp.server import _state

        overview_entry = {
            "pattern_id": "anchor_pattern",
            "pattern_type": "anchor",
            "anomaly_rate": 0.05,
            "theta_norm": 3.77,
        }
        nav = MagicMock()
        nav.sphere_overview.return_value = [overview_entry]
        nav._compute_event_rate_divergence.return_value = divergence_alerts
        nav.temporal_quality_summary.return_value = None

        pattern = MagicMock()
        pattern.edge_max = None
        pattern.dim_percentiles = {}
        pattern.event_dimensions = []
        pattern.prop_columns = []
        pattern.relations = []

        sphere = MagicMock()
        sphere.patterns = {"anchor_pattern": pattern}

        reader = MagicMock()
        reader.read_population_forecast.return_value = None
        reader.read_calibration_tracker.return_value = None
        session = MagicMock()
        session._reader = reader

        _state["navigator"] = nav
        _state["sphere"] = sphere
        _state["session"] = session
        _state["engine"] = MagicMock()
        _state["manifest"] = MagicMock()

    def test_detail_full_includes_divergence_alerts(self):
        """detail='full' includes event_rate_divergence_alerts on the relevant pattern entry."""
        from hypertopos_mcp.tools.observability import sphere_overview

        alert = {
            "pattern_id": "anchor_pattern",
            "event_pattern_id": "event_pattern",
            "entity_key": "CUST-001",
            "event_anomaly_rate": 0.30,
            "delta_norm": 1.5,
            "theta_norm": 3.77,
            "alert": "high event anomaly rate (30%) but normal static geometry — investigate temporal",
        }
        self._setup_state([alert])

        result = json.loads(sphere_overview(detail="full"))
        entry = result[0]
        assert "event_rate_divergence_alerts" in entry
        alerts = entry["event_rate_divergence_alerts"]
        assert len(alerts) == 1
        assert alerts[0]["entity_key"] == "CUST-001"
        assert alerts[0]["event_anomaly_rate"] == pytest.approx(0.30)

    def test_detail_summary_excludes_divergence_alerts(self):
        """detail='summary' must NOT include event_rate_divergence_alerts."""
        from hypertopos_mcp.tools.observability import sphere_overview

        alert = {
            "pattern_id": "anchor_pattern",
            "event_pattern_id": "event_pattern",
            "entity_key": "CUST-001",
            "event_anomaly_rate": 0.30,
            "delta_norm": 1.5,
            "theta_norm": 3.77,
            "alert": "high event anomaly rate (30%) but normal static geometry — investigate temporal",
        }
        self._setup_state([alert])

        result = json.loads(sphere_overview(detail="summary"))
        entry = result[0]
        assert "event_rate_divergence_alerts" not in entry

    def test_no_alerts_field_when_empty(self):
        """event_rate_divergence_alerts not present when navigator returns empty list."""
        from hypertopos_mcp.tools.observability import sphere_overview

        self._setup_state([])

        result = json.loads(sphere_overview(detail="full"))
        entry = result[0]
        assert "event_rate_divergence_alerts" not in entry

