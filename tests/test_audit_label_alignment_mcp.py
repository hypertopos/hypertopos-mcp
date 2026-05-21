# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests for ``audit_label_alignment``.

Validates: full-field response on a synthetic 2-class sphere built with
the ``label_audit:`` block, fallback shape when label-aware calibration
is missing, error envelopes for bad input, strict-JSON sanitisation,
and tier registration.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import hypertopos_mcp.tools.observability  # noqa: F401 — register tool
import pytest
from hypertopos.sphere import HyperSphere
from hypertopos_mcp.server import _TOOL_TIERS, _state
from hypertopos_mcp.tools.observability import audit_label_alignment


def _load_two_class_builder():
    """Load ``_build_two_class_sphere`` from the core-test sibling file.

    Both test files share the same basename, which would collide on the
    sys.modules import path; load the core file under an aliased module
    name via importlib so the helper stays a single source of truth.
    """
    core_path = (
        Path(__file__).resolve().parent.parent.parent
        / "hypertopos-py" / "tests" / "test_audit_label_alignment.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_core_audit_label_alignment_tests", core_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._build_two_class_sphere


_build_two_class_sphere = _load_two_class_builder()


@pytest.fixture
def fake_state():
    """Save / restore ``_state`` and provide an installer for sessions.

    Mirrors the save+restore convention used elsewhere in the MCP test
    suite (memory rule ``feedback_fixture_state_save_restore_symmetry``).
    """
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")

    def _install(sphere_path: str):
        hyper = HyperSphere.open(sphere_path)
        session = hyper.session("mcp-test")
        _state["navigator"] = session.navigator()
        # The MCP tool reads ``_state["sphere"]._sphere``. ``HyperSphere``
        # exposes that attribute directly.
        _state["sphere"] = hyper
        return session

    yield _install

    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def test_full_field_path_returns_high_auroc(tmp_path, fake_state):
    """Full-field path: AUROC ≥ 0.95 on the separating sphere."""
    out, _pks, _labels = _build_two_class_sphere(
        tmp_path,
        enable_label_aware=True,
        register_block=True,
        n_per_class=120,
        sep_mean=3.0,
        out_dir_name="gds_align_mcp_full",
    )
    fake_state(out)

    body = audit_label_alignment(pattern_id="tx_pattern", top_n=5)
    parsed = json.loads(body)

    assert parsed["label_aware_available"] is True
    assert parsed["pattern_id"] == "tx_pattern"
    assert parsed["auroc"] is not None
    assert parsed["auroc"] >= 0.95, (
        f"MCP-wrapped AUROC={parsed['auroc']:.3f} should exceed 0.95"
    )
    assert parsed["n_pos"] == 120
    assert parsed["n_neg"] == 120
    # top_n caps the dim list; sep_score must come first (highest |direction|).
    assert len(parsed["top_dims"]) <= 5
    assert parsed["top_dims"][0]["dim_label"] == "sep_score"
    # Each row carries the full field set.
    for row in parsed["top_dims"]:
        assert set(row.keys()) == {
            "dim_label", "direction_component", "abs_direction",
            "cohens_d_pos_neg",
        }
    # Strict-JSON: no Infinity / NaN literals.
    assert "Infinity" not in body
    assert "NaN" not in body


def test_fallback_when_no_label_aware_calibration(tmp_path, fake_state):
    """Pattern without ``label_aware_calibration`` → fallback shape."""
    out, _pks, _labels = _build_two_class_sphere(
        tmp_path,
        enable_label_aware=False,
        register_block=False,
        out_dir_name="gds_align_mcp_fallback",
    )
    fake_state(out)

    body = audit_label_alignment(pattern_id="tx_pattern", top_n=5)
    parsed = json.loads(body)

    assert parsed["label_aware_available"] is False
    assert parsed["auroc"] is None
    assert parsed["n_pos"] is None
    assert parsed["n_neg"] is None
    assert parsed["top_dims"] == []
    assert "reason" in parsed


def test_unknown_pattern_returns_error_envelope(tmp_path, fake_state):
    """Unknown ``pattern_id`` is rejected with an error envelope."""
    out, _pks, _labels = _build_two_class_sphere(
        tmp_path,
        enable_label_aware=True,
        register_block=True,
        n_per_class=40,
        out_dir_name="gds_align_mcp_unknown",
    )
    fake_state(out)

    body = audit_label_alignment(pattern_id="missing_pattern", top_n=5)
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["pattern_id"] == "missing_pattern"


def test_top_n_below_one_returns_error(tmp_path, fake_state):
    """``top_n < 1`` is rejected before navigator invocation."""
    out, _pks, _labels = _build_two_class_sphere(
        tmp_path,
        enable_label_aware=True,
        register_block=True,
        n_per_class=40,
        out_dir_name="gds_align_mcp_topn0",
    )
    fake_state(out)

    body = audit_label_alignment(pattern_id="tx_pattern", top_n=0)
    parsed = json.loads(body)
    assert "error" in parsed
    assert parsed["top_n"] == 0


def test_tier_registration():
    """Tool must be registered in ``_TOOL_TIERS`` as ``base`` (memory rule)."""
    assert _TOOL_TIERS.get("audit_label_alignment") == "base"
