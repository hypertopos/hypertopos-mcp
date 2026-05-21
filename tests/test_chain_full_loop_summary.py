# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for chain_full_loop_summary.

Validates:
- composition wiring: orchestrator returns 7 step blocks + summary
- partial-failure isolation: one step raising does not abort the rest
- gating: include_* flags produce ``{ok: True, skipped: True}`` blocks
- score-based classification (strong / moderate / weak) at boundaries
- tier registration as ``base``
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import (
    _classify_chain_full_loop_summary,
    _score_chain_full_loop,
    chain_full_loop_summary,
)

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def fake_state():
    """Stub a navigator into _state with default no-op return values.

    Tests override individual return values / side effects to exercise
    composition, partial failure, and scoring branches.
    """
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    _state["sphere"] = MagicMock()

    # Default returns — each step returns a minimal valid shape so the
    # orchestrator never raises on the happy path.
    nav.find_chains_with_coherent_anomaly.return_value = {
        "pattern_id": "chain_pattern",
        "anchor_pattern_id": "account_pattern",
        "n_results": 1,
        "chains": [{
            "chain_id": "CH-1",
            "run_length": 4,
            "max_delta_norm": 2.5,
            "top_dim": "fan_asymmetry",
        }],
    }
    nav.chain_witness_intersection.return_value = {
        "chain_id": "CH-1",
        "n_members": 4,
        "coordinated": True,
        "mean_pairwise_witness_jaccard": 0.8,
        "intersected_witness_dims": ["risk_score"],
    }
    nav.chain_drift_trajectory.return_value = {
        "chain_id": "CH-1",
        "n_members": 4,
        "chain_drift_score": 0.55,
    }
    nav.classify_chain_typology.return_value = {
        "chain_id": "CH-1",
        "shape": "monotone-rising",
        "position_in_chain": "transit",
    }
    nav.extend_chain.return_value = {
        "boundary_key": "A-9",
        "boundary_position": "end",
        "candidates": [
            {"entity_key": "X1", "is_anomaly": True},
            {"entity_key": "X2", "is_anomaly": True},
        ],
        "summary": {"n_candidates": 2, "n_anomalous_candidates": 2},
    }
    nav.investigate_chain.return_value = {
        "chain_id": "CH-1",
        "trace": {
            "ok": True,
            "data": {
                "n_anomalies": 3,
                "is_cyclic": False,
                "cross_bank_count": 2,
            },
        },
    }
    nav.generate_sar_rationale.return_value = {
        "chain_id": "CH-1",
        "sar_narrative": "Evidence indicates...",
        "confidence": "high",
    }

    yield nav

    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


# -- 1. Composition smoke ----------------------------------------------------


def test_smoke_returns_seven_step_blocks_plus_summary(fake_state):
    """Orchestrator returns every step block + top-level summary."""
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)

    # Top-level identity passthrough
    assert parsed["chain_id"] == "CH-1"
    assert parsed["chain_pattern_id"] == "chain_pattern"
    assert parsed["anchor_pattern_id"] == "account_pattern"

    # All seven step blocks present
    for step in (
        "find_chains_with_coherent_anomaly",
        "chain_witness_intersection",
        "chain_drift_trajectory",
        "classify_chain_typology",
        "extend_chain",
        "investigate_chain",
        "generate_sar_rationale",
    ):
        assert step in parsed, f"missing step block: {step}"
        assert parsed[step]["ok"] is True

    # Summary present + structured
    assert "summary" in parsed
    for key in ("investigation_strength", "recommended_action",
                "rationale", "score"):
        assert key in parsed["summary"]
    assert isinstance(parsed["summary"]["score"], int)
    assert "elapsed_ms" in parsed

    # Each navigator method was invoked once (extend_chain twice — fwd + bwd)
    fake_state.find_chains_with_coherent_anomaly.assert_called_once()
    fake_state.chain_witness_intersection.assert_called_once()
    fake_state.chain_drift_trajectory.assert_called_once()
    fake_state.classify_chain_typology.assert_called_once()
    assert fake_state.extend_chain.call_count == 2
    fake_state.investigate_chain.assert_called_once()
    # SAR is gated off by default
    fake_state.generate_sar_rationale.assert_not_called()


def test_coherent_not_in_set_is_informational_not_error(fake_state):
    """Chain not in coherent-anomaly set still returns ok with note."""
    fake_state.find_chains_with_coherent_anomaly.return_value = {
        "chains": [],  # no match for CH-1
    }
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    block = parsed["find_chains_with_coherent_anomaly"]
    assert block["ok"] is True
    assert block["data"]["in_coherent_set"] is False
    assert "note" in block["data"]


# -- 2. Partial-failure isolation -------------------------------------------


def test_partial_failure_one_step_does_not_abort_others(fake_state):
    """Drift step raising RuntimeError must NOT abort the rest."""
    fake_state.chain_drift_trajectory.side_effect = RuntimeError("boom")

    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)

    # Failing step surfaces ok=False with error string
    drift = parsed["chain_drift_trajectory"]
    assert drift["ok"] is False
    assert "boom" in drift["error"]
    assert "RuntimeError" in drift["error"]

    # All other steps still ran and succeeded
    assert parsed["find_chains_with_coherent_anomaly"]["ok"] is True
    assert parsed["chain_witness_intersection"]["ok"] is True
    assert parsed["classify_chain_typology"]["ok"] is True
    assert parsed["extend_chain"]["ok"] is True
    assert parsed["investigate_chain"]["ok"] is True
    # Summary is still produced
    assert "summary" in parsed
    assert isinstance(parsed["summary"]["score"], int)


# -- 3. Gating ---------------------------------------------------------------


def test_gating_off_yields_skipped_blocks_and_no_calls(fake_state):
    """include_*=False produces {ok: True, skipped: True} and no nav call."""
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
        include_extension=False,
        include_drift=False,
        include_witness=False,
        include_sar_rationale=False,
    )
    parsed = json.loads(body)

    for step in (
        "chain_witness_intersection",
        "chain_drift_trajectory",
        "extend_chain",
        "generate_sar_rationale",
    ):
        block = parsed[step]
        assert block.get("ok") is True
        assert block.get("skipped") is True

    # Gated-off navigator methods must NOT be called
    fake_state.chain_witness_intersection.assert_not_called()
    fake_state.chain_drift_trajectory.assert_not_called()
    fake_state.extend_chain.assert_not_called()
    fake_state.generate_sar_rationale.assert_not_called()

    # Always-on steps still ran
    fake_state.find_chains_with_coherent_anomaly.assert_called_once()
    fake_state.classify_chain_typology.assert_called_once()
    fake_state.investigate_chain.assert_called_once()


def test_include_sar_rationale_true_invokes_sar(fake_state):
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
        include_sar_rationale=True,
    )
    parsed = json.loads(body)
    fake_state.generate_sar_rationale.assert_called_once()
    sar_block = parsed["generate_sar_rationale"]
    assert sar_block["ok"] is True
    assert sar_block["data"]["confidence"] == "high"


# -- 4. Top_n_extensions wiring ---------------------------------------------


def test_top_n_extensions_passes_to_extend_chain_as_max_results(fake_state):
    chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
        top_n_extensions=7,
    )
    # Both forward and backward calls receive max_results=7
    calls = fake_state.extend_chain.call_args_list
    assert len(calls) == 2
    for c in calls:
        assert c.kwargs["max_results"] == 7


# -- 5. Score-based classification at boundaries ---------------------------


def _ok(data):
    return {"ok": True, "data": data}


_NOT_FIRED = {"ok": True, "data": {}}


def _build_blocks(
    *,
    coherent_hit=False,
    coordinated=False,
    drift_score=0.0,
    typology_default=True,
    investigate_substantive=False,
    extension_total=0,
    sar_ok=False,
):
    """Construct per-step block dicts with controlled fire/skip status."""
    coherent = _ok({"in_coherent_set": True}) if coherent_hit else _ok(
        {"in_coherent_set": False, "note": "not in set"}
    )
    witness = _ok({"coordinated": coordinated}) if coordinated else _ok(
        {"coordinated": False}
    )
    drift = _ok({"chain_drift_score": drift_score})
    if typology_default:
        typology = _ok({"shape": "no-anomalous-run",
                        "position_in_chain": "no-run"})
    else:
        typology = _ok({"shape": "monotone-rising",
                        "position_in_chain": "transit"})
    if investigate_substantive:
        investigate = _ok({"trace": {"ok": True, "data": {
            "n_anomalies": 5, "is_cyclic": True, "cross_bank_count": 3,
        }}})
    else:
        investigate = _ok({"trace": {"ok": True, "data": {
            "n_anomalies": 0, "is_cyclic": False, "cross_bank_count": 1,
        }}})
    # extension_total candidates split fwd/bwd
    fwd_count = extension_total // 2
    bwd_count = extension_total - fwd_count
    extension = {
        "ok": True,
        "data": {
            "forward": _ok({"candidates": [{} for _ in range(fwd_count)]}),
            "backward": _ok({"candidates": [{} for _ in range(bwd_count)]}),
        },
    }
    sar = _ok({}) if sar_ok else {"ok": True, "skipped": True}
    return coherent, witness, drift, typology, extension, investigate, sar


def test_score_all_signals_fire_yields_strong(fake_state):
    """All-signals-fire → score >= 70 → strong + escalate to SAR."""
    coherent, witness, drift, typology, extension, investigate, sar = (
        _build_blocks(
            coherent_hit=True,
            coordinated=True,
            drift_score=0.5,
            typology_default=False,
            investigate_substantive=True,
            extension_total=4,
            sar_ok=True,
        )
    )
    score_b = _score_chain_full_loop(
        coherent=coherent, witness=witness, drift=drift,
        typology=typology, extension=extension, investigate=investigate,
        sar=sar, include_sar_rationale=True, top_n_extensions=3,
    )
    # 20+15+15+10+20+10+10 = 100
    assert score_b["score"] == 100
    summary = _classify_chain_full_loop_summary(score_b)
    assert summary["investigation_strength"] == "strong"
    assert summary["recommended_action"] == "escalate to SAR"


def test_score_strong_boundary_exact_70(fake_state):
    """Score exactly 70 → strong (>= 70)."""
    # Pick a subset summing to exactly 70: 20 + 15 + 15 + 20 = 70
    coherent, witness, drift, typology, extension, investigate, sar = (
        _build_blocks(
            coherent_hit=True,         # +20
            coordinated=True,          # +15
            drift_score=0.5,           # +15
            typology_default=True,     # +0
            investigate_substantive=True,  # +20
            extension_total=0,         # +0
            sar_ok=False,              # +0
        )
    )
    score_b = _score_chain_full_loop(
        coherent=coherent, witness=witness, drift=drift,
        typology=typology, extension=extension, investigate=investigate,
        sar=sar, include_sar_rationale=False, top_n_extensions=3,
    )
    assert score_b["score"] == 70
    summary = _classify_chain_full_loop_summary(score_b)
    assert summary["investigation_strength"] == "strong"


def test_score_moderate_boundary_exact_40(fake_state):
    """Score exactly 40 → moderate (>= 40, < 70)."""
    # 20 + 20 = 40
    coherent, witness, drift, typology, extension, investigate, sar = (
        _build_blocks(
            coherent_hit=True,         # +20
            coordinated=False,         # +0
            drift_score=0.0,           # +0
            typology_default=True,     # +0
            investigate_substantive=True,  # +20
            extension_total=0,         # +0
            sar_ok=False,              # +0
        )
    )
    score_b = _score_chain_full_loop(
        coherent=coherent, witness=witness, drift=drift,
        typology=typology, extension=extension, investigate=investigate,
        sar=sar, include_sar_rationale=False, top_n_extensions=3,
    )
    assert score_b["score"] == 40
    summary = _classify_chain_full_loop_summary(score_b)
    assert summary["investigation_strength"] == "moderate"
    assert summary["recommended_action"] == "continue investigation"


def test_score_weak_below_40(fake_state):
    """Score 39 (just below 40) → weak."""
    # 15 + 15 + 0 + ... build with witness + drift only = 30
    coherent, witness, drift, typology, extension, investigate, sar = (
        _build_blocks(
            coherent_hit=False,        # +0
            coordinated=True,          # +15
            drift_score=0.5,           # +15
            typology_default=True,     # +0
            investigate_substantive=False,  # +0
            extension_total=0,         # +0
            sar_ok=False,              # +0
        )
    )
    score_b = _score_chain_full_loop(
        coherent=coherent, witness=witness, drift=drift,
        typology=typology, extension=extension, investigate=investigate,
        sar=sar, include_sar_rationale=False, top_n_extensions=3,
    )
    assert score_b["score"] == 30
    summary = _classify_chain_full_loop_summary(score_b)
    assert summary["investigation_strength"] == "weak"
    assert summary["recommended_action"] == "false-positive candidate"


def test_score_zero_yields_weak_with_no_signal_rationale(fake_state):
    """No firing signals → score 0 → weak with explicit no-signals rationale."""
    coherent, witness, drift, typology, extension, investigate, sar = (
        _build_blocks(
            coherent_hit=False,
            coordinated=False,
            drift_score=0.0,
            typology_default=True,
            investigate_substantive=False,
            extension_total=0,
            sar_ok=False,
        )
    )
    score_b = _score_chain_full_loop(
        coherent=coherent, witness=witness, drift=drift,
        typology=typology, extension=extension, investigate=investigate,
        sar=sar, include_sar_rationale=False, top_n_extensions=3,
    )
    assert score_b["score"] == 0
    summary = _classify_chain_full_loop_summary(score_b)
    assert summary["investigation_strength"] == "weak"
    assert "no load-bearing signals" in summary["rationale"]


# -- 6. Tier mapping --------------------------------------------------------


def test_tool_is_registered_in_base_tier():
    """Tier-mapping lock — must appear as 'base' so it surfaces only
    after sphere_overview (matches investigate_chain / investigate_entity).
    """
    assert _TOOL_TIERS.get("chain_full_loop_summary") == "base"
