# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for the chain reliability rollup on chain_full_loop_summary.

Validates that the orchestrator surfaces the four chain-level reliability
fields on the ``summary`` block:

- ``chain_mean_signed_confidence``
- ``chain_n_low_confidence_members``
- ``chain_n_single_dim_driven_members``
- ``chain_confidence_verdict`` ∈ {high, medium, low, label-aware-unavailable}

…and that the ``"low"`` verdict subtracts 10 from the investigation score
without breaking the existing strength / recommended_action mapping.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import pytest
from hypertopos_mcp.server import _state
from hypertopos_mcp.tools.analysis import chain_full_loop_summary

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def fake_state():
    """Stub a navigator with happy-path defaults.

    Per-test overrides drive ``chain_signed_confidence_rollup`` to the
    scenario under inspection (label-aware-available / unavailable /
    empty-chain) and verify the rollup propagates onto ``summary``.
    """
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    _state["sphere"] = MagicMock()

    # Default per-step returns — copied from test_chain_full_loop_summary
    # so the orchestrator's seven base steps never raise.
    nav.find_chains_with_coherent_anomaly.return_value = {
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
    }
    nav.chain_drift_trajectory.return_value = {
        "chain_id": "CH-1",
        "chain_drift_score": 0.55,
    }
    nav.classify_chain_typology.return_value = {
        "chain_id": "CH-1",
        "shape": "monotone-rising",
        "position_in_chain": "transit",
    }
    nav.extend_chain.return_value = {
        "boundary_key": "A-9",
        "candidates": [
            {"entity_key": "X1", "is_anomaly": True},
            {"entity_key": "X2", "is_anomaly": True},
        ],
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
    }
    # Default rollup — label-aware available, high-confidence chain.
    nav.chain_signed_confidence_rollup.return_value = {
        "chain_id": "CH-1",
        "chain_pattern": "chain_pattern",
        "anchor_pattern": "account_pattern",
        "n_members": 4,
        "n_members_resolved": 4,
        "chain_mean_signed_confidence": 1.8,
        "chain_n_low_confidence_members": 0,
        "chain_n_single_dim_driven_members": 0,
        "chain_confidence_verdict": "high",
    }

    yield nav

    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


# -- 1. Label-aware path — 4 members, "low" verdict crosses score gate -----


def test_label_aware_low_verdict_propagates_fields_and_score_penalty(fake_state):
    """Synthetic 4-member chain with 3/4 low-confidence members.

    Per-member confidences:
      - M1: signed_confidence_score = 0.2, reliability_penalty = 0.5
        (single_dim_driven=True)
      - M2: signed_confidence_score = 0.1, reliability_penalty = 0.5
        (single_dim_driven=True)
      - M3: signed_confidence_score = 0.05, reliability_penalty = 1.0
        (single_dim_driven=False, but low_confidence_bucket=True)
      - M4: signed_confidence_score = 1.5, reliability_penalty = 0.0
        (clean signal)

    Aggregates:
      - mean = (0.2 + 0.1 + 0.05 + 1.5) / 4 = 0.4625
      - n_low_confidence (penalty >= 0.5) = 3
      - n_single_dim_driven = 2
      - verdict = "low" because n_low >= 0.5 * n_members (3 >= 2)

    Score path: happy-path defaults fire +20 (coherent) + 15 (witness) +
    15 (drift > 0.3) + 10 (typology) + 20 (investigate substantive) + 10
    (extension candidates 2+0=2 >= 3? extension default has 2 candidates
    forward, fake_state's extend_chain returns 2 each call → 4 total >=
    3 → +10). SAR gated off → 0. Total = 90; "low" verdict subtracts
    10 → 80 → still "strong" / "escalate to SAR".
    """
    fake_state.chain_signed_confidence_rollup.return_value = {
        "chain_id": "CH-1",
        "chain_pattern": "chain_pattern",
        "anchor_pattern": "account_pattern",
        "n_members": 4,
        "n_members_resolved": 4,
        "chain_mean_signed_confidence": 0.4625,
        "chain_n_low_confidence_members": 3,
        "chain_n_single_dim_driven_members": 2,
        "chain_confidence_verdict": "low",
    }

    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]

    # Four rollup fields surfaced on summary
    assert summary["chain_mean_signed_confidence"] == pytest.approx(0.4625)
    assert summary["chain_n_low_confidence_members"] == 3
    assert summary["chain_n_single_dim_driven_members"] == 2
    assert summary["chain_confidence_verdict"] == "low"

    # Score penalty applied. Happy-path score = 90 (all 6 base signals
    # fire under fake_state defaults); -10 penalty → 80 → still strong.
    assert summary["score"] == 80
    assert summary["investigation_strength"] == "strong"
    assert summary["recommended_action"] == "escalate to SAR"
    assert "low-confidence-chain penalty -10" in summary["rationale"]

    # Verdict that flips a moderate score into weak via the -10 penalty
    # is covered separately by the boundary test below.


def test_low_verdict_penalty_can_demote_moderate_to_weak(fake_state):
    """When base score sits exactly at the moderate boundary (40), the
    ``"low"`` penalty must demote it across the 40 → weak boundary."""
    # Gut the typology / extension / investigate signals to land at
    # exactly +20 (coherent) + 20 (investigate substantive) = 40.
    fake_state.classify_chain_typology.return_value = {
        "shape": "no-anomalous-run",
        "position_in_chain": "no-run",
    }
    fake_state.chain_witness_intersection.return_value = {
        "coordinated": False,
    }
    fake_state.chain_drift_trajectory.return_value = {
        "chain_drift_score": 0.0,
    }
    fake_state.extend_chain.return_value = {"candidates": []}
    fake_state.chain_signed_confidence_rollup.return_value = {
        "chain_id": "CH-1",
        "n_members": 4,
        "n_members_resolved": 4,
        "chain_mean_signed_confidence": 0.3,
        "chain_n_low_confidence_members": 3,
        "chain_n_single_dim_driven_members": 0,
        "chain_confidence_verdict": "low",
    }
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]
    # Base 40 - 10 = 30 → weak
    assert summary["score"] == 30
    assert summary["investigation_strength"] == "weak"
    assert summary["recommended_action"] == "false-positive candidate"
    assert summary["chain_confidence_verdict"] == "low"


# -- 2. Label-aware-unavailable path ---------------------------------------


def test_label_aware_unavailable_yields_null_fields_and_no_penalty(fake_state):
    """When the anchor pattern has no label_aware_calibration, all four
    rollup fields are null and the verdict is ``"label-aware-unavailable"``.
    The investigation score MUST NOT be penalised — only ``"low"``
    triggers the -10 adjustment.
    """
    fake_state.chain_signed_confidence_rollup.return_value = {
        "chain_id": "CH-1",
        "n_members": 4,
        "n_members_resolved": 0,
        "chain_mean_signed_confidence": None,
        "chain_n_low_confidence_members": None,
        "chain_n_single_dim_driven_members": None,
        "chain_confidence_verdict": "label-aware-unavailable",
    }
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]

    assert summary["chain_mean_signed_confidence"] is None
    assert summary["chain_n_low_confidence_members"] is None
    assert summary["chain_n_single_dim_driven_members"] is None
    assert summary["chain_confidence_verdict"] == "label-aware-unavailable"

    # No -10 penalty — happy-path score stays 90, "strong".
    assert summary["score"] == 90
    assert summary["investigation_strength"] == "strong"
    assert "low-confidence-chain penalty" not in summary["rationale"]


# -- 3. Empty-chain edge case ----------------------------------------------


def test_empty_chain_yields_all_null_fields_including_verdict(fake_state):
    """Empty chain (``len(keys) == 0``) → all four fields null, verdict
    null. No score penalty.
    """
    fake_state.chain_signed_confidence_rollup.return_value = {
        "chain_id": "CH-1",
        "n_members": 0,
        "n_members_resolved": 0,
        "chain_mean_signed_confidence": None,
        "chain_n_low_confidence_members": None,
        "chain_n_single_dim_driven_members": None,
        "chain_confidence_verdict": None,
    }
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]

    assert summary["chain_mean_signed_confidence"] is None
    assert summary["chain_n_low_confidence_members"] is None
    assert summary["chain_n_single_dim_driven_members"] is None
    assert summary["chain_confidence_verdict"] is None
    # No penalty — empty verdict ≠ "low"
    assert summary["score"] == 90
    assert summary["investigation_strength"] == "strong"


# -- 4. Soft-failure path — rollup step raising must not abort orchestrator -


def test_rollup_step_failure_leaves_summary_with_null_fields(fake_state):
    """If ``chain_signed_confidence_rollup`` raises, the four fields
    default to None and verdict to None — no -10 penalty, no abort.
    """
    fake_state.chain_signed_confidence_rollup.side_effect = RuntimeError(
        "deliberate test failure",
    )
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]

    assert summary["chain_mean_signed_confidence"] is None
    assert summary["chain_n_low_confidence_members"] is None
    assert summary["chain_n_single_dim_driven_members"] is None
    assert summary["chain_confidence_verdict"] is None
    # No penalty applied — base score unchanged
    assert summary["score"] == 90


# -- 5. High-verdict path — no penalty -------------------------------------


def test_high_verdict_does_not_penalise_score(fake_state):
    """``"high"`` verdict must not subtract anything from the score."""
    fake_state.chain_signed_confidence_rollup.return_value = {
        "chain_mean_signed_confidence": 2.5,
        "chain_n_low_confidence_members": 0,
        "chain_n_single_dim_driven_members": 0,
        "chain_confidence_verdict": "high",
    }
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]
    assert summary["chain_confidence_verdict"] == "high"
    assert summary["chain_mean_signed_confidence"] == pytest.approx(2.5)
    assert summary["score"] == 90


def test_medium_verdict_does_not_penalise_score(fake_state):
    """``"medium"`` verdict must not subtract from the score either."""
    fake_state.chain_signed_confidence_rollup.return_value = {
        "chain_mean_signed_confidence": 0.6,
        "chain_n_low_confidence_members": 1,
        "chain_n_single_dim_driven_members": 1,
        "chain_confidence_verdict": "medium",
    }
    body = chain_full_loop_summary(
        "CH-1",
        chain_pattern_id="chain_pattern",
        anchor_pattern_id="account_pattern",
    )
    parsed = json.loads(body)
    summary = parsed["summary"]
    assert summary["chain_confidence_verdict"] == "medium"
    assert summary["score"] == 90
