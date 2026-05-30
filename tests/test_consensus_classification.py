# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for consensus_classification.

Coverage matrix:
- Unit: focal-row extraction from a stubbed population sweep.
- Discriminator: materially-distinct classifications for distinct keys MUST
  produce distinct classification labels (rules out a constant/wrong-row bug).
- not-found envelope: a key outside the scored sample returns found=false plus
  a note, never an empty/None payload.
- top_n / sample_size cap handling: the underlying sweep is always called with a
  high top_n so a scored entity is never lost to list truncation.
- JSON sanitisation: ±inf / NaN from p_per_detector / hmp must surface as JSON
  null, never as Infinity/NaN literals.
- Tier registration: must appear in _TOOL_TIERS as "multi_pattern" (mirrors the
  underlying classify_detector_consensus tier).
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import consensus_classification


@pytest.fixture
def fake_nav():
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    # _require_navigator → _require_sphere demands a non-None sphere; the tool
    # itself does not read the sphere object, so a sentinel suffices.
    _state["sphere"] = MagicMock()
    yield nav
    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def _entry(pk, classification, *, anomalous=None, normal=None, borderline=None,
           hmp=0.01, rank=1, p_per_detector=None):
    return {
        "primary_key": pk,
        "classification": classification,
        "anomalous_detectors": anomalous or [],
        "normal_detectors": normal or [],
        "borderline_detectors": borderline or [],
        "n_detectors_fired": (
            len(anomalous or []) + len(normal or []) + len(borderline or [])
        ),
        "hmp": hmp,
        "p_per_detector": p_per_detector or {},
        "rank": rank,
    }


def test_tool_is_registered_in_multi_pattern_tier():
    assert _TOOL_TIERS.get("consensus_classification") == "multi_pattern"


def test_extracts_focal_row(fake_nav):
    fake_nav.classify_detector_consensus.return_value = [
        _entry("E1", "mixed_signal",
               anomalous=["delta_norm"], normal=["segment_shift"], rank=1),
        _entry("E2", "normal_consensus",
               normal=["delta_norm", "segment_shift"], rank=2),
    ]
    body = consensus_classification("E2", "account_pattern")
    parsed = json.loads(body)
    assert parsed["found"] is True
    assert parsed["primary_key"] == "E2"
    assert parsed["classification"] == "normal_consensus"
    assert parsed["population_rank"] == 2
    assert "recommended_next_steps" in parsed


def test_calls_underlying_with_high_top_n_to_defeat_truncation(fake_nav):
    fake_nav.classify_detector_consensus.return_value = [
        _entry("E1", "anomalous_consensus", anomalous=["a", "b"]),
    ]
    consensus_classification("E1", "account_pattern", sample_size=5000)
    _, kwargs = fake_nav.classify_detector_consensus.call_args
    # top_n forced high so a scored entity is never lost to list truncation.
    assert kwargs["top_n"] >= 100_000
    assert kwargs["sample_size"] == 5000


def test_not_in_sample_returns_found_false_with_note(fake_nav):
    fake_nav.classify_detector_consensus.return_value = [
        _entry("OTHER", "mixed_signal", anomalous=["a"], normal=["b"]),
    ]
    body = consensus_classification("GHOST", "account_pattern", sample_size=100)
    parsed = json.loads(body)
    assert parsed["found"] is False
    assert parsed["classification"] is None
    assert "note" in parsed
    assert "100" in parsed["note"]


def test_not_found_note_does_not_suggest_raising_when_full_scan(fake_nav):
    """When the caller already scanned the full population (sample_size=None),
    the not-found note must not contradict itself by suggesting 'raise
    sample_size / pass null'."""
    fake_nav.classify_detector_consensus.return_value = [
        _entry("OTHER", "mixed_signal", anomalous=["a"], normal=["b"]),
    ]
    body = consensus_classification("GHOST", "account_pattern", sample_size=None)
    parsed = json.loads(body)
    assert parsed["found"] is False
    assert "raise sample_size" not in parsed["note"]
    assert "full-population" in parsed["note"]


def test_discriminator_distinct_keys_distinct_classifications(fake_nav):
    """Four engineered entities with distinct classifications must each be
    extracted correctly — a constant or wrong-row bug collapses them."""
    fake_nav.classify_detector_consensus.return_value = [
        _entry("MIX", "mixed_signal",
               anomalous=["delta_norm"], normal=["segment_shift"], rank=1),
        _entry("ANOM", "anomalous_consensus",
               anomalous=["delta_norm", "density_gap"], rank=2),
        _entry("SINGLE", "single_detector_signal",
               anomalous=["delta_norm"], borderline=["segment_shift"], rank=3),
        _entry("NORM", "normal_consensus",
               normal=["delta_norm", "segment_shift"], rank=4),
    ]
    got = {}
    for key in ("MIX", "ANOM", "SINGLE", "NORM"):
        parsed = json.loads(consensus_classification(key, "account_pattern"))
        got[key] = parsed["classification"]
    assert got == {
        "MIX": "mixed_signal",
        "ANOM": "anomalous_consensus",
        "SINGLE": "single_detector_signal",
        "NORM": "normal_consensus",
    }, f"Focal-row extraction collapsed — got {got}"


def test_sanitises_non_finite_p_values(fake_nav):
    fake_nav.classify_detector_consensus.return_value = [
        _entry("E1", "mixed_signal", anomalous=["a"], normal=["b"],
               hmp=math.nan,
               p_per_detector={"a": 0.0, "b": math.inf}),
    ]
    body = consensus_classification("E1", "account_pattern")
    parsed = json.loads(body)
    assert parsed["hmp"] is None
    assert parsed["p_per_detector"]["b"] is None
    assert "NaN" not in body
    assert "Infinity" not in body


def test_returns_json_error_when_underlying_raises(fake_nav):
    fake_nav.classify_detector_consensus.side_effect = ValueError(
        "pattern needs ≥2 patterns on entity line"
    )
    body = consensus_classification("E1", "account_pattern")
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["primary_key"] == "E1"
    assert parsed["pattern_id"] == "account_pattern"
