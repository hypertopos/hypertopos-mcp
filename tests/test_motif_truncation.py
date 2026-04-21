# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for MCP motif-response truncation (_truncate_motif_instance).

Pre-fix, fan_in hubs with several hundred sources produced ~200k-char JSON
responses that overflowed the MCP token limit and got written to a file
instead of returned inline. The helper caps edges/breakdown at the top 50
contributors by edge_potential DESC while still surfacing population
statistics over the full edge set via ``breakdown_summary``.
"""
from __future__ import annotations

import json
import math

import pytest
from hypertopos_mcp.tools.analysis import _sanitize_for_json, _truncate_motif_instance


def _make_breakdown(n: int, ep_base: float = 1.0) -> list[dict]:
    """Build ``n`` fake breakdown entries with monotonically increasing ep."""
    return [
        {
            "edge": (f"src{i}", "SINK"),
            "edge_potential": ep_base + float(i),
            "delta_distance": 1.0,
            "pair_tx_count": 1,
            "effective_weight": 1.0,
        }
        for i in range(n)
    ]


def _make_inst(k: int) -> dict:
    breakdown = _make_breakdown(k)
    return {
        "motif_type": "fan_in",
        "seed": "SINK",
        "k": k,
        "edges": [e["edge"] for e in breakdown],
        "score": 1.0,
        "log_score": 0.0,
        "score_clamped": False,
        "breakdown": breakdown,
        "found": True,
        "pattern_id": "tx",
        "time_window_hours": 168,
    }


class TestTruncateMotifInstance:

    def test_small_motif_passthrough_untruncated(self):
        """k <= threshold → no truncation, flags False, no summary."""
        inst = _make_inst(3)
        out = _truncate_motif_instance(inst, threshold=50)
        assert out["edges_truncated"] is False
        assert out["breakdown_truncated"] is False
        assert out["edges_total_count"] == 3
        assert "breakdown_summary" not in out
        assert len(out["edges"]) == 3
        assert len(out["breakdown"]) == 3

    def test_large_motif_truncates_to_top_50_by_edge_potential(self):
        inst = _make_inst(200)
        out = _truncate_motif_instance(inst, threshold=50)
        assert out["edges_truncated"] is True
        assert out["breakdown_truncated"] is True
        assert out["edges_total_count"] == 200
        assert len(out["edges"]) == 50
        assert len(out["breakdown"]) == 50
        # Top contributors by edge_potential DESC — ep values are 1.0..200.0,
        # so the top 50 are 200.0 down to 151.0.
        eps = [b["edge_potential"] for b in out["breakdown"]]
        assert eps == sorted(eps, reverse=True)
        assert eps[0] == 200.0
        assert eps[-1] == 151.0
        # breakdown_summary covers the FULL 200-edge population.
        s = out["breakdown_summary"]
        assert s["count"] == 200
        assert s["min"] == pytest.approx(1.0)
        assert s["max"] == pytest.approx(200.0)
        # median of 1..200 = 100.5
        assert s["p50"] == pytest.approx(100.5)
        for key in ("mean", "std", "p25", "p75", "p95"):
            assert key in s
        # edges align with the breakdown order (top ep first).
        assert out["edges"][0] == ("src199", "SINK")
        # Passthrough fields preserved.
        assert out["motif_type"] == "fan_in"
        assert out["seed"] == "SINK"
        assert out["score"] == 1.0
        assert out["log_score"] == 0.0
        assert out["score_clamped"] is False

    def test_response_size_bounded_after_truncation(self):
        """JSON-serialised bounded-truncation output stays well under 10k."""
        inst = _make_inst(500)
        out = _truncate_motif_instance(inst, threshold=50)
        payload = json.dumps(out, default=str)
        assert len(payload) < 10_000, (
            f"Expected truncated payload < 10kB, got {len(payload)}B "
            f"on k=500 motif"
        )

    def test_exact_threshold_is_not_truncated(self):
        """Boundary: k == threshold → passthrough."""
        inst = _make_inst(50)
        out = _truncate_motif_instance(inst, threshold=50)
        assert out["edges_truncated"] is False
        assert out["breakdown_truncated"] is False
        assert "breakdown_summary" not in out


class TestSanitizeForJson:
    """Non-finite floats must become JSON-null before wire serialisation.

    Python's ``json.dumps`` emits ``Infinity`` / ``-Infinity`` / ``NaN`` by
    default — rejected by strict RFC 8259 parsers (browser JSON.parse, many
    non-Python MCP clients). Navigator motif scorers emit ``log_score = -inf``
    on saw_zero (identical-delta endpoints); that must land as ``null`` in
    the MCP response.
    """

    def test_neg_inf_becomes_none(self):
        assert _sanitize_for_json(-math.inf) is None

    def test_pos_inf_becomes_none(self):
        assert _sanitize_for_json(math.inf) is None

    def test_nan_becomes_none(self):
        assert _sanitize_for_json(math.nan) is None

    def test_finite_floats_pass_through(self):
        assert _sanitize_for_json(1.5) == 1.5
        assert _sanitize_for_json(-1.5) == -1.5
        assert _sanitize_for_json(0.0) == 0.0

    def test_nested_dict_recurses(self):
        inst = {"score": 0.0, "log_score": -math.inf, "score_clamped": False}
        out = _sanitize_for_json(inst)
        assert out == {"score": 0.0, "log_score": None, "score_clamped": False}

    def test_list_of_dicts_recurses(self):
        payload = [{"log_score": -math.inf}, {"log_score": 3.5}]
        out = _sanitize_for_json(payload)
        assert out == [{"log_score": None}, {"log_score": 3.5}]

    def test_sanitized_output_parses_under_strict_json(self):
        """Round-trip: sanitized payload must parse with allow_nan=False."""
        inst = {
            "motif_type": "fan_in",
            "seed": "SINK",
            "score": 0.0,
            "log_score": -math.inf,
            "score_clamped": False,
            "breakdown": [{"edge": ("A", "SINK"), "edge_potential": 0.0}],
        }
        sanitized = _sanitize_for_json(inst)
        # allow_nan=False will raise on -Infinity / Infinity / NaN literals.
        wire = json.dumps(sanitized, allow_nan=False)
        parsed = json.loads(wire)
        assert parsed["log_score"] is None
        assert parsed["score"] == 0.0
        assert parsed["score_clamped"] is False

    def test_non_float_types_untouched(self):
        # Strings, ints, bools, None, tuples preserved (tuples → tuples, but
        # json.dumps later turns them into lists — orthogonal concern).
        assert _sanitize_for_json("hello") == "hello"
        assert _sanitize_for_json(42) == 42
        assert _sanitize_for_json(True) is True
        assert _sanitize_for_json(None) is None
        assert _sanitize_for_json(("a", "b")) == ("a", "b")

    def test_numpy_float32_nan_and_inf_sanitized(self):
        """Defensive: np.float32 is NOT a python-float subclass, easy leak."""
        import numpy as np
        assert _sanitize_for_json(np.float32("nan")) is None
        assert _sanitize_for_json(np.float32("inf")) is None
        assert _sanitize_for_json(np.float32("-inf")) is None
        # Finite numpy float passes through (though converted to python float
        # transparently by later json.dumps).
        assert _sanitize_for_json(np.float32(2.5)) == pytest.approx(2.5)
        assert _sanitize_for_json(np.float64(-math.inf)) is None
