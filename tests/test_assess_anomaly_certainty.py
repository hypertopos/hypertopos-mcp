# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for assess_anomaly_certainty.

Coverage matrix:
- Unit: navigator-stubbed composition produces the documented verdict for
  engineered inputs.
- Discriminator: four materially-distinct inputs MUST produce four distinct
  verdict labels.
- JSON sanitisation: ±inf / NaN from any composed primitive must surface as
  JSON null, never as Infinity/NaN literals.
- Tier registration: must appear in ``_TOOL_TIERS`` as ``"base"`` so the
  tool is gated behind ``sphere_overview`` like its peers.
- Step-status uniformity: each sub-composition step produces ``{ok: true}``
  or ``{ok: false, error: <message>}`` — never nulls, never missing keys.
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import hypertopos_mcp.tools.analysis  # noqa: F401 — register tools
import numpy as np
import pytest
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.analysis import assess_anomaly_certainty


def _conformal_p_for_stability(stability_per_alpha: tuple[bool, bool, bool]) -> float:
    """Map a (0.005, 0.01, 0.05) stability tuple to a conformal_p value.

    Stability at alpha is now ``conformal_p <= alpha`` (a direct, sample-free
    lookup of the focal entity's stored calibrated p-value). Because the alphas
    are sorted ascending and the test always declares stability as trailing
    True flags (stable at the loosest alphas first), exactly ``n_true`` of the
    three alphas — the ``n_true`` largest — must pass. conformal_p is placed at
    the smallest passing alpha so precisely that many alphas clear the
    threshold.
    """
    alphas = (0.005, 0.01, 0.05)
    n_true = sum(stability_per_alpha)
    if n_true == 0:
        return 0.5  # > 0.05 → stable at no alpha
    # The n_true largest alphas pass; the smallest passing alpha is the
    # threshold. conformal_p == that alpha → it (and every larger alpha) clears.
    smallest_passing = alphas[len(alphas) - n_true]
    return smallest_passing


def _make_polygon(primary_key: str):
    """Lightweight Polygon stand-in — only attribute used by the tool is
    ``primary_key``; mocking the full dataclass adds no signal."""
    return SimpleNamespace(primary_key=primary_key)


def _stub_sphere_with_theta(theta_norm: float):
    """Stub ``_state["sphere"]._sphere.patterns[pattern_id].theta`` so the
    tool's near_data_boundary derivation can read ``np.linalg.norm(theta)``."""
    # Build a theta vector whose L2 norm is exactly theta_norm.
    theta = np.array([theta_norm, 0.0], dtype=np.float64)
    pattern = SimpleNamespace(theta=theta)
    sphere = SimpleNamespace(patterns={"account_pattern": pattern})
    state_sphere = MagicMock()
    state_sphere._sphere = sphere
    return state_sphere


@pytest.fixture
def fake_state():
    """Install a navigator + sphere stub into _state; restore on teardown."""
    nav = MagicMock()
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = nav
    _state["sphere"] = _stub_sphere_with_theta(theta_norm=2.5)
    yield nav
    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


# ---------------------------------------------------------------------------
# Helper: stub a "default healthy" set of navigator responses; tests override
# individual return values.
# ---------------------------------------------------------------------------
_CONFORMAL_P_FROM_STABILITY = object()  # sentinel: derive conformal_p from tuple


def _stub_default_navigator(
    nav: MagicMock,
    *,
    is_anomaly: bool = True,
    delta_norm: float = 5.0,
    conformal_p: float | None | object = _CONFORMAL_P_FROM_STABILITY,
    signed_confidence: float | None = 0.85,
    single_dim_driven: bool = False,
    stability_per_alpha: tuple[bool, bool, bool] = (True, True, True),
    calibration_health: str = "good",
    cross_pattern_signals: dict | None = None,
) -> None:
    # Stability at each alpha is now ``conformal_p <= alpha`` — a direct,
    # sample-free lookup of the focal entity's stored calibrated p-value. When
    # the caller does not pin conformal_p explicitly, derive it from the
    # requested per-alpha stability tuple so existing scenarios keep their
    # intended verdict.
    if conformal_p is _CONFORMAL_P_FROM_STABILITY:
        conformal_p = _conformal_p_for_stability(stability_per_alpha)

    nav.get_entity_geometry_meta.return_value = {
        "delta_norm": delta_norm,
        "is_anomaly": is_anomaly,
        "delta_rank_pct": 99.5 if is_anomaly else 50.0,
    }
    nav.explain_anomaly.return_value = {
        "severity": "high" if is_anomaly else "normal",
        "delta_norm": delta_norm,
        "theta_norm": 2.5,
        "conformal_p": conformal_p,
        "signed_confidence": signed_confidence,
        "reliability_flags": {
            "single_dim_driven": single_dim_driven,
            "low_confidence_bucket": False,
            "dominant_dim": "amount_std",
            "dominant_share": 0.75 if single_dim_driven else 0.30,
            "confidence": None,
            "flags": ["single_dim_driven"] if single_dim_driven else [],
        },
    }

    nav.sphere_overview.return_value = [
        {
            "pattern_id": "account_pattern",
            "calibration_health": calibration_health,
            "total_entities": 1000,
        }
    ]

    nav.cross_pattern_profile.return_value = {
        "primary_key": "E1",
        "line_id": "accounts",
        "signals": cross_pattern_signals
        if cross_pattern_signals is not None
        else {"chain_pattern": {"is_anomaly": True}},
    }


# ---------------------------------------------------------------------------
# Tier registration
# ---------------------------------------------------------------------------
def test_tool_is_registered_in_base_tier():
    assert _TOOL_TIERS.get("assess_anomaly_certainty") == "base"


# ---------------------------------------------------------------------------
# Unit — verdict derivation per documented rule
# ---------------------------------------------------------------------------
def test_high_certainty_anomalous_full_agreement(fake_state):
    """Stable across all 3 alphas, multi-dim driven, far from boundary,
    healthy calibration, cross-pattern agreement → "high"."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,  # far above 1.2 * 2.5 = 3.0 → not boundary
        single_dim_driven=False,
        stability_per_alpha=(True, True, True),
        calibration_health="good",
    )
    body = assess_anomaly_certainty("E1", "account_pattern")
    parsed = json.loads(body)
    assert parsed["is_anomalous"] is True
    assert parsed["certainty_verdict"] == "high"
    assert parsed["reliability_flags"]["near_data_boundary"] is False
    assert parsed["reliability_flags"]["calibration_stale"] is False
    assert parsed["stability_across_alphas"] == {
        "0.005": True, "0.01": True, "0.05": True,
    }
    assert parsed["certainty_score"] >= 0.90


def test_high_certainty_confident_normal(fake_state):
    """Non-anomalous entity, far from boundary, healthy calibration → "high"
    even though is_anomalous=False — verdict is certainty about classification,
    not about anomaly status."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=False,
        delta_norm=0.5,  # well below 0.8 * 2.5 = 2.0 → not boundary
        stability_per_alpha=(False, False, False),
        calibration_health="good",
        cross_pattern_signals={"chain_pattern": {"is_anomaly": False}},
    )
    body = assess_anomaly_certainty("E1", "account_pattern")
    parsed = json.loads(body)
    assert parsed["is_anomalous"] is False
    assert parsed["certainty_verdict"] == "high"
    assert parsed["reliability_flags"]["near_data_boundary"] is False


def test_contested_single_dim_on_boundary(fake_state):
    """Anomalous, single-dim-driven, sitting on the boundary → "contested"."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=2.6,  # inside [0.8 * 2.5, 1.2 * 2.5] = [2.0, 3.0]
        single_dim_driven=True,
        stability_per_alpha=(True, True, True),
    )
    body = assess_anomaly_certainty("E1", "account_pattern")
    parsed = json.loads(body)
    assert parsed["certainty_verdict"] == "contested"
    assert parsed["reliability_flags"]["single_dim_driven"] is True
    assert parsed["reliability_flags"]["near_data_boundary"] is True


def test_low_certainty_unstable_anomaly(fake_state):
    """Anomalous in raw classification but only stable at the loosest alpha
    (1/3 stability count) → "low"."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        stability_per_alpha=(False, False, True),
    )
    body = assess_anomaly_certainty("E1", "account_pattern")
    parsed = json.loads(body)
    assert parsed["certainty_verdict"] == "low"
    assert sum(parsed["stability_across_alphas"].values()) == 1


# ---------------------------------------------------------------------------
# Discriminator — 4 distinct engineered inputs → 4 distinct verdicts
# ---------------------------------------------------------------------------
def test_discriminator_four_distinct_verdicts(fake_state):
    """The four engineered scenarios above must produce four distinct verdicts.
    If any two collapse to the same label the verdict rule is degenerate."""
    verdicts: set[str] = set()

    # Case 1 — full agreement, multi-dim, off boundary, healthy → high
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        single_dim_driven=False,
        stability_per_alpha=(True, True, True),
        calibration_health="good",
    )
    verdicts.add(json.loads(
        assess_anomaly_certainty("E1", "account_pattern")
    )["certainty_verdict"])

    # Case 2 — single-dim on boundary → contested
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=2.6,
        single_dim_driven=True,
        stability_per_alpha=(True, True, True),
    )
    verdicts.add(json.loads(
        assess_anomaly_certainty("E1", "account_pattern")
    )["certainty_verdict"])

    # Case 3 — only loosest alpha keeps it anomalous (1/3) → low
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        single_dim_driven=False,
        stability_per_alpha=(False, False, True),
    )
    verdicts.add(json.loads(
        assess_anomaly_certainty("E1", "account_pattern")
    )["certainty_verdict"])

    # Case 4 — 2/3 stability, healthy → moderate
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        single_dim_driven=False,
        stability_per_alpha=(False, True, True),
    )
    verdicts.add(json.loads(
        assess_anomaly_certainty("E1", "account_pattern")
    )["certainty_verdict"])

    assert verdicts == {"high", "contested", "low", "moderate"}, (
        f"Discriminator collapsed — got {sorted(verdicts)}; "
        "verdict rule does not discriminate four engineered scenarios."
    )


# ---------------------------------------------------------------------------
# Sampling independence — stability is a direct conformal_p lookup, never a
# sampled population scan. (Regression: the prior sampled-sweep membership
# check returned False for any entity outside the random sample, yielding a
# false "contested" verdict on most large-sphere entities.)
# ---------------------------------------------------------------------------
def test_stability_does_not_call_population_scan(fake_state):
    """Stability is derived from the focal entity's stored conformal_p, so the
    composer must NOT run any π5_attract_anomaly population scan for the sweep —
    the verdict is independent of which entities a random sample happens to
    draw."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        stability_per_alpha=(True, True, True),
    )

    assess_anomaly_certainty("E1", "account_pattern")

    fake_state.π5_attract_anomaly.assert_not_called()


def test_genuinely_anomalous_entity_not_contested_without_sampling(fake_state):
    """A genuinely-anomalous focal entity (tiny conformal_p — extreme tail of
    the population) MUST receive a confident verdict, not "contested", even
    though no population sample is drawn. Regression for the sample-membership
    inversion that flagged ~80-98% of large-sphere entities as contested
    because they were not in the random scan set."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=12.0,          # far above 1.2 * 2.5 = 3.0 → not boundary
        conformal_p=0.0005,       # in the extreme tail → stable at every alpha
        single_dim_driven=False,
        calibration_health="good",
    )

    body = assess_anomaly_certainty("E1", "account_pattern")
    parsed = json.loads(body)

    assert parsed["is_anomalous"] is True
    # All three alphas pass (0.0005 <= 0.005/0.01/0.05) → stability_count == 3.
    assert parsed["stability_across_alphas"] == {
        "0.005": True, "0.01": True, "0.05": True,
    }
    assert parsed["certainty_verdict"] != "contested"
    assert parsed["certainty_verdict"] == "high"
    # No population scan was needed to reach the verdict.
    fake_state.π5_attract_anomaly.assert_not_called()


# ---------------------------------------------------------------------------
# JSON sanitisation — NaN/inf from any composed primitive → null
# ---------------------------------------------------------------------------
def test_sanitises_non_finite_conformal_p_and_signed_confidence(fake_state):
    """An NaN conformal_p or +inf signed_confidence emitted by explain_anomaly
    must serialise as JSON null, never as Infinity/NaN literals (RFC 8259
    strict parsers reject Infinity / NaN)."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        conformal_p=math.nan,
        signed_confidence=math.inf,
        stability_per_alpha=(True, True, True),
    )
    body = assess_anomaly_certainty("E1", "account_pattern")
    # Strict-JSON parse must succeed.
    parsed = json.loads(body)
    assert parsed["conformal_p"] is None
    assert parsed["signed_confidence"] is None
    # Defence in depth — raw body has no Infinity/NaN literal tokens.
    assert "NaN" not in body
    assert "Infinity" not in body


# ---------------------------------------------------------------------------
# Step-status uniformity — sub-composition failures must surface as
# {ok: false, error: <message>}, never as nulls/missing keys.
# ---------------------------------------------------------------------------
def test_step_status_records_failure_for_cross_pattern(fake_state):
    """When cross_pattern_profile raises, the tool MUST still return a valid
    payload with the step marked {ok: false, error: ...} — never crash."""
    _stub_default_navigator(
        fake_state,
        is_anomaly=True,
        delta_norm=10.0,
        stability_per_alpha=(True, True, True),
    )
    fake_state.cross_pattern_profile.side_effect = ValueError(
        "no anchor line for entity"
    )

    body = assess_anomaly_certainty("E1", "account_pattern")
    parsed = json.loads(body)

    # Tool still returns a result — does not bubble the ValueError.
    assert parsed["primary_key"] == "E1"
    # Failed step is captured with ok=false + error message.
    assert parsed["steps_status"]["cross_pattern_consistency"] == {
        "ok": False,
        "error": "no anchor line for entity",
    }
    # Successful steps are still ok=true.
    assert parsed["steps_status"]["entity_geometry"] == {"ok": True}
    assert parsed["steps_status"]["explain_anomaly"] == {"ok": True}
    # Cross-pattern counters fall back to zero, not null.
    assert parsed["cross_pattern_consistency"]["n_other_patterns_anomalous"] == 0


def test_returns_json_error_on_entity_not_found(fake_state):
    """Entity not in geometry → JSON error envelope, not unhandled exception."""
    fake_state.get_entity_geometry_meta.side_effect = KeyError(
        "Entity 'ghost' not found in account_pattern v3"
    )
    body = assess_anomaly_certainty("ghost", "account_pattern")
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["primary_key"] == "ghost"
    assert parsed["pattern_id"] == "account_pattern"
