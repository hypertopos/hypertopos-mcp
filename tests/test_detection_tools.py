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
        _state["sphere"] = MagicMock()

    def teardown_method(self):
        _state["navigator"] = None
        _state["sphere"] = None

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
