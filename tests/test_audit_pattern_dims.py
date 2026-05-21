# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for ``audit_pattern_dims``.

Validates: full-field response with label-aware calibration, fallback
shape without it, decision-tree boundary values, ``|cohens_d|`` sort
order, ``top_k`` cap, strict-JSON sanitisation, tier registration.
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import hypertopos_mcp.tools.observability  # noqa: F401 — register tools
import numpy as np
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.observability import audit_pattern_dims


def _dim_cal(*, mu_pos, sigma_pos, mu_neg, sigma_neg, direction):
    """Build a minimal stand-in for ``engine.calibration_label_aware.DimCalibration``.

    The tool reads via attribute access — any object with the five
    attributes works, so we avoid importing the engine dataclass here.
    """
    return SimpleNamespace(
        mu_pos=mu_pos,
        sigma_pos=sigma_pos,
        mu_neg=mu_neg,
        sigma_neg=sigma_neg,
        direction=direction,
    )


def _make_pattern(
    *,
    dim_labels: list[str],
    mu: list[float],
    sigma_diag: list[float],
    label_aware_calibration: dict | None = None,
    dimension_kinds: list[str] | None = None,
):
    """Construct a minimal Pattern-shaped object the tool accepts.

    Only the attributes the tool touches are populated. We intentionally
    do NOT construct a real ``Pattern`` dataclass — this keeps the test
    decoupled from the (still-evolving) sphere model and from the
    ``label_aware_calibration`` field landing later in a follow-up PR.
    """
    pattern = SimpleNamespace(
        dim_labels=dim_labels,
        mu=np.asarray(mu, dtype=float),
        sigma_diag=np.asarray(sigma_diag, dtype=float),
    )
    if label_aware_calibration is not None:
        pattern.label_aware_calibration = label_aware_calibration
    if dimension_kinds is not None:
        pattern.dimension_kinds = dimension_kinds
    return pattern


@pytest.fixture
def fake_state():
    """Stub a sphere with one named pattern into ``_state``.

    Mirrors the save+restore convention used elsewhere in the MCP test
    suite (memory rule ``feedback_fixture_state_save_restore_symmetry``).
    """
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")

    nav = MagicMock()
    sphere_wrapper = MagicMock()
    sphere_core = SimpleNamespace(patterns={})
    sphere_wrapper._sphere = sphere_core

    _state["navigator"] = nav
    _state["sphere"] = sphere_wrapper

    def _install(pattern_id: str, pattern):
        sphere_core.patterns[pattern_id] = pattern

    yield _install

    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def test_unknown_pattern_returns_error_envelope(fake_state):
    body = audit_pattern_dims(pattern_id="missing_pattern")
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["pattern_id"] == "missing_pattern"


def test_top_k_below_one_returns_error(fake_state):
    fake_state(
        "p1",
        _make_pattern(dim_labels=["a"], mu=[0.0], sigma_diag=[1.0]),
    )
    body = audit_pattern_dims(pattern_id="p1", top_k=0)
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["top_k"] == 0


def test_fallback_when_no_label_aware_calibration(fake_state):
    """Pattern without ``label_aware_calibration`` returns raw stats + reason."""
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["d_a", "d_b", "d_c"],
            mu=[0.1, 0.5, 1.0],
            sigma_diag=[0.2, 0.4, 0.6],
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    assert parsed["label_aware_available"] is False
    assert "reason" in parsed
    assert parsed["n_dims_total"] == 3
    assert parsed["n_dims_returned"] == 3
    assert {row["dim_label"] for row in parsed["dims"]} == {"d_a", "d_b", "d_c"}
    for row in parsed["dims"]:
        assert row["recommended_action"] == "keep"
        assert "mu" in row and "sigma" in row
        assert "mu_pos" not in row  # fallback omits label-aware fields


def test_fallback_respects_top_k_cap(fake_state):
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["d0", "d1", "d2", "d3"],
            mu=[0.0, 0.0, 0.0, 0.0],
            sigma_diag=[1.0, 1.0, 1.0, 1.0],
        ),
    )
    body = audit_pattern_dims(pattern_id="p1", top_k=2)
    parsed = json.loads(body)
    assert parsed["n_dims_returned"] == 2
    assert len(parsed["dims"]) == 2


def test_full_field_response_with_label_aware(fake_state):
    """Pattern with label-aware calibration returns full 9-field rows."""
    lac = {
        "high_sep": _dim_cal(
            mu_pos=1.0, sigma_pos=0.1, mu_neg=0.0, sigma_neg=0.1, direction=0.9,
        ),
        "noise": _dim_cal(
            mu_pos=0.5, sigma_pos=0.5, mu_neg=0.5, sigma_neg=0.5, direction=0.01,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["high_sep", "noise"],
            mu=[0.5, 0.5],
            sigma_diag=[0.5, 0.5],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    assert parsed["label_aware_available"] is True
    assert parsed["n_dims_total"] == 2
    assert parsed["n_dims_returned"] == 2

    expected_fields = {
        "dim_label", "mu", "sigma", "mu_pos", "sigma_pos",
        "mu_neg", "sigma_neg", "cohens_d_pos_neg",
        "direction_component", "recommended_action",
    }
    for row in parsed["dims"]:
        assert expected_fields.issubset(row.keys())


def test_sorted_by_abs_cohens_d_descending(fake_state):
    """High-Cohen's-d dims surface first; ``top_k`` slices the head."""
    lac = {
        "low_d": _dim_cal(
            mu_pos=0.05, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.3,
        ),
        "mid_d": _dim_cal(
            mu_pos=0.4, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.3,
        ),
        "high_d": _dim_cal(
            mu_pos=2.0, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.3,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["low_d", "mid_d", "high_d"],
            mu=[0.0, 0.0, 0.0],
            sigma_diag=[1.0, 1.0, 1.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1", top_k=2)
    parsed = json.loads(body)
    labels = [row["dim_label"] for row in parsed["dims"]]
    assert labels == ["high_d", "mid_d"]


def test_action_drop_low_separation_below_threshold(fake_state):
    """``cohens_d < 0.1`` → ``drop_low_separation`` regardless of direction."""
    # cohens_d = |0.099| / sqrt((1+1)/2) = 0.099 — just below the gate.
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.099, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] < 0.1
    assert row["recommended_action"] == "drop_low_separation"


def test_action_keep_at_threshold_with_direction(fake_state):
    """At the 0.1 boundary with strong direction → ``keep``."""
    # cohens_d = 0.10001 — just above the gate.
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.10001, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] >= 0.1
    assert row["recommended_action"] == "keep"


def test_action_investigate_drift_when_direction_small(fake_state):
    """Separation in raw stats but axis says dim doesn't carry the signal."""
    # cohens_d = 0.5 — well above 0.1; direction = 0.04 — below 0.05.
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.5, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.04,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] >= 0.1
    assert abs(row["direction_component"]) < 0.05
    assert row["recommended_action"] == "investigate_drift"


def test_action_split_when_population_sigma_much_wider(fake_state):
    """Strong separation + population sigma > 2 × max class sigma → ``split``."""
    # cohens_d = 2 / sqrt(2) ≈ 1.41 → above 0.5.
    # population sigma = 3 > 2 × max(0.5, 0.5) = 1.
    lac = {
        "dim_a": _dim_cal(
            mu_pos=1.0, sigma_pos=0.5, mu_neg=-1.0, sigma_neg=0.5, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[3.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] >= 0.5
    assert row["recommended_action"] == "split"


def test_action_keep_when_split_threshold_not_met(fake_state):
    """``cohens_d >= 0.5`` AND |direction| >= 0.05 but sigma not 2× → ``keep``."""
    # cohens_d ≈ 1.41 — above 0.5; population sigma = 1.5; max class sigma = 1.
    # 1.5 < 2 × 1 = 2, so split gate not met.
    lac = {
        "dim_a": _dim_cal(
            mu_pos=1.0, sigma_pos=1.0, mu_neg=-1.0, sigma_neg=1.0, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.5],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] >= 0.5
    assert row["recommended_action"] == "keep"


def test_zero_sigma_pos_and_neg_clamps_to_zero(fake_state):
    """Zero pooled denominator clamps Cohen's d to 0 → ``drop_low_separation``."""
    lac = {
        "degenerate": _dim_cal(
            mu_pos=0.5, sigma_pos=0.0, mu_neg=0.0, sigma_neg=0.0, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["degenerate"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] == 0.0
    assert row["recommended_action"] == "drop_low_separation"


def test_dim_missing_from_calibration_falls_back_to_keep(fake_state):
    """Dim present in ``dim_labels`` but missing from calibration → ``keep`` row."""
    lac = {
        "known_dim": _dim_cal(
            mu_pos=1.0, sigma_pos=0.5, mu_neg=0.0, sigma_neg=0.5, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["known_dim", "missing_dim"],
            mu=[0.0, 0.0],
            sigma_diag=[1.0, 1.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    rows_by_label = {row["dim_label"]: row for row in parsed["dims"]}
    assert rows_by_label["missing_dim"]["recommended_action"] == "keep"
    assert rows_by_label["missing_dim"]["cohens_d_pos_neg"] is None
    assert rows_by_label["missing_dim"]["direction_component"] is None
    # Sort sends None-cohens_d row to the end.
    assert parsed["dims"][-1]["dim_label"] == "missing_dim"


def test_response_is_strict_json_no_infinity_literals(fake_state):
    """Non-finite floats (if they slip through) must serialise as ``null``."""
    # Synthetically inject a NaN into population mu — sanitiser must clean it.
    pat = _make_pattern(
        dim_labels=["a"],
        mu=[float("nan")],
        sigma_diag=[1.0],
    )
    fake_state("p1", pat)
    body = audit_pattern_dims(pattern_id="p1")
    # Strict-JSON: no Infinity / NaN literals on the wire.
    assert "Infinity" not in body
    assert "NaN" not in body
    parsed = json.loads(body)  # strict parse must succeed
    assert parsed["dims"][0]["mu"] is None


def test_tier_registration():
    """Tool must be registered in ``_TOOL_TIERS`` as ``base`` (memory rule)."""
    assert _TOOL_TIERS.get("audit_pattern_dims") == "base"


def test_zero_denominator_does_not_raise(fake_state):
    """Defensive: zero-sigma input must not raise ``ZeroDivisionError``."""
    lac = {
        "d": _dim_cal(
            mu_pos=0.0, sigma_pos=0.0, mu_neg=0.0, sigma_neg=0.0, direction=0.0,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["d"],
            mu=[0.0],
            sigma_diag=[0.0],
            label_aware_calibration=lac,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    assert math.isfinite(parsed["dims"][0]["cohens_d_pos_neg"])


def test_action_kind_mismatch_review_when_gaussian_high_d_low_direction(
    fake_state,
):
    """Dim A: gaussian + ``cohens_d=0.5`` + ``|direction|=0.02`` →
    ``kind_mismatch_review``. Preempts every other branch."""
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.5, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.02,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
            dimension_kinds=["gaussian"],
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] >= 0.3
    assert abs(row["direction_component"]) < 0.05
    assert row["recommended_action"] == "kind_mismatch_review"


def test_action_kind_mismatch_review_silent_when_kind_not_gaussian(
    fake_state,
):
    """Same numeric profile but kind='bernoulli' → falls through to
    ``investigate_drift`` (cohens_d above 0.1 + low direction)."""
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.5, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.02,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
            dimension_kinds=["bernoulli"],
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["recommended_action"] == "investigate_drift"


def test_action_kind_mismatch_silent_when_cohens_d_below_threshold(
    fake_state,
):
    """Below the cohens_d=0.3 gate the kind-mismatch branch does NOT
    fire — falls through to ``investigate_drift`` (cohens_d still above
    the 0.1 gate, direction still below 0.05)."""
    # cohens_d = 0.2 / sqrt(1) = 0.2 — between 0.1 and 0.3.
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.2, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.02,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
            dimension_kinds=["gaussian"],
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["cohens_d_pos_neg"] < 0.3
    assert row["recommended_action"] == "investigate_drift"


def test_action_kind_mismatch_silent_when_dimension_kinds_absent(fake_state):
    """Legacy pattern with no ``dimension_kinds`` attribute → falls
    through to ``investigate_drift`` (numeric profile of Dim A but kind
    cannot be confirmed gaussian)."""
    lac = {
        "dim_a": _dim_cal(
            mu_pos=0.5, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.02,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["dim_a"],
            mu=[0.0],
            sigma_diag=[1.0],
            label_aware_calibration=lac,
            dimension_kinds=None,
        ),
    )
    body = audit_pattern_dims(pattern_id="p1")
    parsed = json.loads(body)
    row = parsed["dims"][0]
    assert row["recommended_action"] == "investigate_drift"


def test_decision_tree_preserved_for_gaussian_below_kind_mismatch_gates(
    fake_state,
):
    """Existing branches (``keep`` / ``split`` / ``drop_low_separation``
    / ``investigate_drift``) still resolve correctly on gaussian dims
    when the kind-mismatch criteria are not met simultaneously."""
    lac = {
        # keep: high cohens_d, high direction, narrow population sigma
        "keep_dim": _dim_cal(
            mu_pos=1.0, sigma_pos=1.0, mu_neg=-1.0, sigma_neg=1.0, direction=0.9,
        ),
        # split: high cohens_d, high direction, wide population sigma
        "split_dim": _dim_cal(
            mu_pos=1.0, sigma_pos=0.5, mu_neg=-1.0, sigma_neg=0.5, direction=0.9,
        ),
        # drop_low_separation: cohens_d ≈ 0.05 (below 0.1)
        "drop_dim": _dim_cal(
            mu_pos=0.05, sigma_pos=1.0, mu_neg=0.0, sigma_neg=1.0, direction=0.9,
        ),
    }
    fake_state(
        "p1",
        _make_pattern(
            dim_labels=["keep_dim", "split_dim", "drop_dim"],
            mu=[0.0, 0.0, 0.0],
            # keep_dim: sigma_i = 1.5 < 2 × 1.0; split_dim: sigma_i = 3 > 2 × 0.5
            sigma_diag=[1.5, 3.0, 1.0],
            label_aware_calibration=lac,
            dimension_kinds=["gaussian", "gaussian", "gaussian"],
        ),
    )
    body = audit_pattern_dims(pattern_id="p1", top_k=3)
    parsed = json.loads(body)
    rows_by_label = {row["dim_label"]: row for row in parsed["dims"]}
    assert rows_by_label["keep_dim"]["recommended_action"] == "keep"
    assert rows_by_label["split_dim"]["recommended_action"] == "split"
    assert rows_by_label["drop_dim"]["recommended_action"] == (
        "drop_low_separation"
    )
