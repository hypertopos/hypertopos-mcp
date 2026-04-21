# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for MCP detection tool wrappers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from hypertopos_mcp.server import _state


class TestDetectCrossPatternDiscrepancy:
    def setup_method(self):
        self.nav = MagicMock()
        _state["navigator"] = self.nav
        sphere_mock = MagicMock()
        sphere_mock.patterns = {"pat_a": MagicMock(), "pat_b": MagicMock()}
        sphere_mock.entity_line.side_effect = lambda pid: "customers"
        _state["sphere"] = MagicMock()
        _state["sphere"]._sphere = sphere_mock

    def teardown_method(self):
        _state["navigator"] = None
        _state["sphere"] = None

    def test_detect_cross_pattern_discrepancy_single_pattern_returns_diagnostic(self):
        """When entity_line has only 1 covering pattern, return diagnostic not empty list."""
        from hypertopos_mcp.tools.detection import detect_cross_pattern_discrepancy

        sphere_mock = MagicMock()
        sphere_mock.patterns = {"pat_a": MagicMock()}
        sphere_mock.entity_line.side_effect = lambda pid: "accounts" if pid == "pat_a" else None
        _state["sphere"]._sphere = sphere_mock

        response = json.loads(detect_cross_pattern_discrepancy("accounts"))
        assert response["total_found"] == 0
        assert response["results"] == []
        assert "diagnostic" in response
        assert "requires" in response["diagnostic"].lower()

    def test_returns_valid_json_with_correct_keys(self):
        from hypertopos_mcp.tools.detection import detect_cross_pattern_discrepancy

        self.nav.detect_cross_pattern_discrepancy.return_value = [
            {
                "entity_key": "C001",
                "anomalous_pattern": "pat_a",
                "normal_patterns": ["pat_b"],
                "delta_norm_anomalous": 1.23,
                "delta_rank_pct_anomalous": 97.5,
                "interpretation": "C001 is anomalous in pat_a but normal in pat_b",
            }
        ]
        result = json.loads(detect_cross_pattern_discrepancy("customers"))
        assert result["entity_line"] == "customers"
        assert result["total_found"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["entity_key"] == "C001"

    def test_passes_top_n(self):
        from hypertopos_mcp.tools.detection import detect_cross_pattern_discrepancy

        self.nav.detect_cross_pattern_discrepancy.return_value = []
        detect_cross_pattern_discrepancy("customers", top_n=10)
        self.nav.detect_cross_pattern_discrepancy.assert_called_once_with("customers", top_n=10)


class TestDetectNeighborContamination:
    def setup_method(self):
        self.nav = MagicMock()
        _state["navigator"] = self.nav
        _state["sphere"] = MagicMock()

    def teardown_method(self):
        _state["navigator"] = None
        _state["sphere"] = None

    def test_returns_valid_json_with_params(self):
        from hypertopos_mcp.tools.detection import detect_neighbor_contamination

        self.nav.detect_neighbor_contamination.return_value = [
            {
                "target_key": "C005",
                "is_anomaly_target": False,
                "contamination_rate": 0.8,
                "anomalous_neighbor_count": 8,
                "total_neighbors": 10,
                "neighbor_keys": ["C001", "C002", "C003", "C004", "C006", "C007", "C008", "C009", "C010", "C011"],
            }
        ]
        result = json.loads(detect_neighbor_contamination("pat_a", k=5, sample_size=10))
        assert result["pattern_id"] == "pat_a"
        assert result["k"] == 5
        assert result["sample_size"] == 10
        assert result["total_found"] == 1

    def test_passes_all_params(self):
        from hypertopos_mcp.tools.detection import detect_neighbor_contamination

        self.nav.detect_neighbor_contamination.return_value = []
        detect_neighbor_contamination("pat_a", k=5, sample_size=15, contamination_threshold=0.7)
        self.nav.detect_neighbor_contamination.assert_called_once_with(
            "pat_a", k=5, sample_size=15, contamination_threshold=0.7
        )


class TestDetectTrajectoryAnomaly:
    def setup_method(self):
        self.nav = MagicMock()
        _state["navigator"] = self.nav
        _state["sphere"] = MagicMock()

    def teardown_method(self):
        _state["navigator"] = None
        _state["sphere"] = None

    def test_returns_valid_json(self):
        from hypertopos_mcp.tools.detection import detect_trajectory_anomaly

        self.nav.detect_trajectory_anomaly.return_value = [
            {
                "entity_key": "C001",
                "trajectory_shape": "arch",
                "displacement": 0.5,
                "path_length": 2.3,
                "num_slices": 5,
                "first_timestamp": "2020-01-01",
                "last_timestamp": "2020-12-01",
                "cohort_size": 3,
                "cohort_keys": ["C002", "C003", "C004"],
                "interpretation": "arch trajectory",
            }
        ]
        result = json.loads(detect_trajectory_anomaly("pat_a"))
        assert result["pattern_id"] == "pat_a"
        assert result["total_found"] == 1
        assert result["results"][0]["trajectory_shape"] == "arch"

    def test_value_error_returns_json_error(self):
        from hypertopos_mcp.tools.detection import detect_trajectory_anomaly

        self.nav.detect_trajectory_anomaly.side_effect = ValueError("Pattern is event type")
        result = json.loads(detect_trajectory_anomaly("event_pat"))
        assert "error" in result
        assert "event type" in result["error"]

    def test_detect_trajectory_anomaly_accepts_sample_size(self):
        """detect_trajectory_anomaly MCP tool must accept sample_size parameter."""
        import inspect
        from hypertopos_mcp.tools.detection import detect_trajectory_anomaly
        sig = inspect.signature(detect_trajectory_anomaly)
        assert "sample_size" in sig.parameters

    def test_detect_trajectory_anomaly_passes_sample_size_to_navigator(self):
        """sample_size must be forwarded to the navigator call."""
        from hypertopos_mcp.tools.detection import detect_trajectory_anomaly

        self.nav.detect_trajectory_anomaly.return_value = []
        detect_trajectory_anomaly("pat_a", sample_size=100)
        self.nav.detect_trajectory_anomaly.assert_called_once_with(
            "pat_a",
            displacement_ranks=None,
            top_n_per_range=5,
            sample_size=100,
        )

    def test_detect_trajectory_anomaly_sample_size_in_response(self):
        """sample_size must appear in the returned JSON."""
        from hypertopos_mcp.tools.detection import detect_trajectory_anomaly

        self.nav.detect_trajectory_anomaly.return_value = []
        result = json.loads(detect_trajectory_anomaly("pat_a", sample_size=50))
        assert result["sample_size"] == 50


class TestDetectSegmentShift:
    def setup_method(self):
        self.nav = MagicMock()
        _state["navigator"] = self.nav
        _state["sphere"] = MagicMock()

    def teardown_method(self):
        _state["navigator"] = None
        _state["sphere"] = None

    def test_returns_valid_json(self):
        from hypertopos_mcp.tools.detection import detect_segment_shift

        self.nav.detect_segment_shift.return_value = [
            {
                "segment_property": "nation",
                "segment_value": "DE",
                "anomaly_rate": 0.12,
                "population_rate": 0.05,
                "shift_ratio": 2.4,
                "entity_count": 100,
                "anomalous_count": 12,
                "changepoint_date": "2020-06-01",
                "interpretation": "DE has 2.4x anomaly rate",
            }
        ]
        result = json.loads(detect_segment_shift("pat_a"))
        assert result["pattern_id"] == "pat_a"
        assert result["total_found"] == 1
        assert result["results"][0]["shift_ratio"] == 2.4

    def test_value_error_returns_json_error(self):
        from hypertopos_mcp.tools.detection import detect_segment_shift

        self.nav.detect_segment_shift.side_effect = ValueError("No entity line")
        result = json.loads(detect_segment_shift("bad_pat"))
        assert "error" in result

    def test_detect_segment_shift_no_string_columns_returns_diagnostic(self):
        """Pattern with no string-typed entity columns returns diagnostic, not silent empty."""
        from hypertopos_mcp.tools.detection import detect_segment_shift

        # Build a sphere mock where entity line has only numeric columns (no string columns)
        numeric_col_a = MagicMock()
        numeric_col_a.name = "amount"
        numeric_col_a.type = "int64"
        numeric_col_b = MagicMock()
        numeric_col_b.name = "balance"
        numeric_col_b.type = "float64"

        entity_line_mock = MagicMock()
        entity_line_mock.columns = [numeric_col_a, numeric_col_b]

        sphere_mock = MagicMock()
        sphere_mock.entity_line.return_value = "accounts"
        sphere_mock.lines = {"accounts": entity_line_mock}
        _state["sphere"]._sphere = sphere_mock

        self.nav.detect_segment_shift.return_value = []

        response = json.loads(detect_segment_shift("pattern_with_no_string_cols"))
        assert response["total_found"] == 0
        assert "diagnostic" in response
        assert (
            "string" in response["diagnostic"].lower()
            or "prop_column" in response["diagnostic"].lower()
        )

    def test_detect_segment_shift_string_columns_but_no_shift_returns_diagnostic(self):
        """When string columns exist but no segment exceeded threshold, return diagnostic."""
        from hypertopos_mcp.tools.detection import detect_segment_shift

        string_col = MagicMock()
        string_col.name = "region"
        string_col.type = "string"
        pk_col = MagicMock()
        pk_col.name = "primary_key"
        pk_col.type = "string"

        entity_line_mock = MagicMock()
        entity_line_mock.columns = [pk_col, string_col]

        sphere_mock = MagicMock()
        sphere_mock.entity_line.return_value = "customers"
        sphere_mock.lines = {"customers": entity_line_mock}
        _state["sphere"]._sphere = sphere_mock

        self.nav.detect_segment_shift.return_value = []

        response = json.loads(detect_segment_shift("pattern_with_string_cols_no_shift"))
        assert response["total_found"] == 0
        assert "diagnostic" in response
        assert "min_shift_ratio" in response["diagnostic"]
