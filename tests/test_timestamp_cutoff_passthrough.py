# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Wiring tests for the 0.2.2 timestamp_cutoff parameter on edge-table tools.

These tests do NOT re-verify semantic correctness — that is covered by
``packages/hypertopos-py/tests/test_geometric_path.py`` against the navigator
itself. They only confirm the MCP wrapper threads the parameter through to
the navigator method instead of dropping it on the floor.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from hypertopos_mcp.server import _state
from hypertopos_mcp.tools.analysis import (
    contagion_score,
    contagion_score_batch,
    degree_velocity,
    entity_flow,
    find_counterparties,
    propagate_influence,
)


@pytest.fixture
def fake_navigator(monkeypatch):
    """Replace _state['navigator'] with a MagicMock and return it."""
    nav = MagicMock()
    # Sensible empty-ish defaults so the wrappers do not crash on result use.
    nav.contagion_score.return_value = {
        "primary_key": "X",
        "pattern_id": "p",
        "score": 0.0,
        "total_counterparties": 0,
        "anomalous_counterparties": 0,
        "interpretation": "no counterparties",
    }
    nav.contagion_score_batch.return_value = {
        "pattern_id": "p",
        "total": 0,
        "results": [],
        "summary": {"mean_score": 0.0, "max_score": 0.0, "high_contagion_count": 0},
    }
    nav.entity_flow.return_value = {
        "primary_key": "X",
        "pattern_id": "p",
        "outgoing_total": 0.0,
        "incoming_total": 0.0,
        "net_flow": 0.0,
        "flow_direction": "balanced",
        "counterparties": [],
    }
    nav.degree_velocity.return_value = {
        "primary_key": "X",
        "pattern_id": "p",
        "buckets": [],
        "velocity_out": None,
        "velocity_in": None,
        "warning": "no edges",
    }
    nav.propagate_influence.return_value = {
        "seeds": ["X"],
        "pattern_id": "p",
        "affected_entities": [],
        "summary": {"total_affected": 0, "max_depth_reached": 0, "anomalous_affected": 0},
    }
    nav.find_counterparties.return_value = {
        "primary_key": "X",
        "line_id": "l",
        "outgoing": [],
        "incoming": [],
        "summary": {
            "total_outgoing": 0, "total_incoming": 0,
            "anomalous_outgoing": 0, "anomalous_incoming": 0,
        },
    }
    monkeypatch.setitem(_state, "navigator", nav)
    monkeypatch.setitem(_state, "sphere", MagicMock())
    yield nav


CUTOFF = 1_700_000_000.0


class TestTimestampCutoffPassthrough:
    """Each MCP wrapper must forward timestamp_cutoff to the navigator."""

    def test_contagion_score_threads_cutoff(self, fake_navigator):
        contagion_score("X", "p", timestamp_cutoff=CUTOFF)
        fake_navigator.contagion_score.assert_called_once_with(
            "X", "p", timestamp_cutoff=CUTOFF,
        )

    def test_contagion_score_default_none(self, fake_navigator):
        contagion_score("X", "p")
        fake_navigator.contagion_score.assert_called_once_with(
            "X", "p", timestamp_cutoff=None,
        )

    def test_contagion_score_batch_threads_cutoff(self, fake_navigator):
        contagion_score_batch(["X", "Y"], "p", timestamp_cutoff=CUTOFF)
        fake_navigator.contagion_score_batch.assert_called_once_with(
            ["X", "Y"], "p", max_keys=200, timestamp_cutoff=CUTOFF,
        )

    def test_entity_flow_threads_cutoff(self, fake_navigator):
        entity_flow("X", "p", timestamp_cutoff=CUTOFF)
        fake_navigator.entity_flow.assert_called_once_with(
            "X", "p", top_n=20, timestamp_cutoff=CUTOFF,
        )

    def test_degree_velocity_threads_cutoff(self, fake_navigator):
        degree_velocity("X", "p", timestamp_cutoff=CUTOFF)
        fake_navigator.degree_velocity.assert_called_once_with(
            "X", "p", n_buckets=4, timestamp_cutoff=CUTOFF,
        )

    def test_propagate_influence_threads_cutoff(self, fake_navigator):
        propagate_influence(["X"], "p", timestamp_cutoff=CUTOFF)
        fake_navigator.propagate_influence.assert_called_once_with(
            ["X"], "p",
            max_depth=3, decay=0.7, min_threshold=0.001,
            timestamp_cutoff=CUTOFF,
        )

    def test_find_counterparties_threads_cutoff(self, fake_navigator):
        find_counterparties(
            "X", "lines", "from", "to",
            pattern_id="p",
            timestamp_cutoff=CUTOFF,
        )
        fake_navigator.find_counterparties.assert_called_once_with(
            "X", "lines", "from", "to",
            pattern_id="p", top_n=20, use_edge_table=True,
            timestamp_cutoff=CUTOFF,
        )


class TestReturnedJsonShape:
    """Smoke that wrapper return value is a JSON string with sane content."""

    def test_contagion_score_returns_json_string(self, fake_navigator):
        result = contagion_score("X", "p", timestamp_cutoff=CUTOFF)
        parsed = json.loads(result)
        assert parsed["primary_key"] == "X"

    def test_propagate_influence_returns_json_string(self, fake_navigator):
        result = propagate_influence(["X"], "p", timestamp_cutoff=CUTOFF)
        parsed = json.loads(result)
        assert parsed["seeds"] == ["X"]
