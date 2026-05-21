# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP smoke for ``find_anomalies(rank_by="signed_confidence")``.

Builds a synthetic 2-class sphere with a ``label_audit`` block, opens it
through the MCP session machinery, and verifies that the response
carries the new ``signed_confidence_score`` / ``lda_alignment`` /
``reliability_penalty`` per polygon. A second test confirms that a
pattern without label-aware calibration raises ``GDSNavigationError``
through the navigator (not a silent fallback to ``delta_norm``).
"""
from __future__ import annotations

import json
from pathlib import Path

import hypertopos_mcp.tools.navigation  # noqa: F401 — register tools
import numpy as np
import pyarrow as pa
import pytest
from hypertopos.builder import GDSBuilder, RelationSpec
from hypertopos.cli.schema import LabelAuditConfig
from hypertopos.navigation.navigator import GDSNavigationError
from hypertopos_mcp.server import _do_open_sphere, _state
from hypertopos_mcp.tools.navigation import find_anomalies


def _build_two_class_sphere(
    tmp_path: Path,
    *,
    enable_label_audit: bool,
    out_dir_name: str,
    n_per_class: int = 90,
    n_anti_aligned: int = 30,
    sep_mean: float = 3.5,
    noise_outlier_mean: float = 8.0,
    seed: int = 9876,
) -> str:
    """Construct a small 2-class event-pattern sphere for MCP smoke tests."""
    rng = np.random.RandomState(seed)
    n_pos = n_per_class
    n_neg = n_per_class + (n_anti_aligned if enable_label_audit else 0)
    n = n_pos + n_neg

    sep_pos = rng.normal(sep_mean, 1.0, n_pos).astype(np.float32)
    if enable_label_audit:
        sep_neg = rng.normal(0.0, 1.0, n_per_class).astype(np.float32)
        sep_anti = rng.normal(0.0, 1.0, n_anti_aligned).astype(np.float32)
        sep_score = np.concatenate([sep_pos, sep_neg, sep_anti])
        noise_pos = rng.normal(0.0, 1.0, n_pos).astype(np.float32)
        noise_neg = rng.normal(0.0, 1.0, n_per_class).astype(np.float32)
        noise_anti = rng.normal(
            noise_outlier_mean, 1.0, n_anti_aligned,
        ).astype(np.float32)
        noise_score = np.concatenate([noise_pos, noise_neg, noise_anti])
        labels = ["anom"] * n_pos + ["norm"] * n_per_class + ["norm"] * n_anti_aligned
    else:
        sep_neg = rng.normal(0.0, 1.0, n_neg).astype(np.float32)
        sep_score = np.concatenate([sep_pos, sep_neg])
        noise_score = rng.normal(0.0, 1.0, n).astype(np.float32)
        labels = ["anom"] * n_pos + ["norm"] * n_neg

    pks = [f"T-{i:04d}" for i in range(n)]
    tx_columns = {
        "tx_id": pks,
        "account_id": ["A-shared"] * n,
        "sep_score": sep_score,
        "noise_score": noise_score,
    }
    if enable_label_audit:
        tx_columns["label"] = labels
    tx = pa.table(tx_columns)
    accounts = pa.table({"account_id": ["A-shared"]})

    out_path = tmp_path / out_dir_name
    b = GDSBuilder("signed_conf_mcp_smoke", str(out_path))
    b.add_line(
        "accounts", accounts, key_col="account_id", source_id="test",
    )
    b.add_line(
        "tx", tx, key_col="tx_id", source_id="test", role="event",
    )
    b.add_pattern(
        "tx_pattern",
        pattern_type="event",
        entity_line="tx",
        relations=[
            RelationSpec(
                line_id="accounts", fk_col="account_id",
                direction="in", required=True,
            ),
        ],
        anomaly_percentile=80.0,
    )
    b.add_event_dimension("tx_pattern", column="sep_score", edge_max="auto")
    b.add_event_dimension("tx_pattern", column="noise_score", edge_max="auto")
    if enable_label_audit:
        b._label_aware_calibration = True
        b._label_audit_block = LabelAuditConfig(
            label_column="label",
            label_positive_value="anom",
            patterns=["tx_pattern"],
        )
    return b.build()


@pytest.fixture
def signed_conf_sphere(tmp_path):
    """Build + open a label-audit-enabled sphere for the smoke test."""
    saved = {k: _state.get(k) for k in (
        "sphere", "session", "navigator", "engine", "manifest", "path",
    )}
    out = _build_two_class_sphere(
        tmp_path,
        enable_label_audit=True,
        out_dir_name="gds_mcp_signed_conf_on",
    )
    _do_open_sphere(out)
    yield out
    if _state.get("session") is not None:
        try:
            _state["session"].close()
        except Exception:
            pass
    _state.update(saved)


@pytest.fixture
def no_label_audit_sphere(tmp_path):
    """Build + open a sphere WITHOUT a label_audit block."""
    saved = {k: _state.get(k) for k in (
        "sphere", "session", "navigator", "engine", "manifest", "path",
    )}
    out = _build_two_class_sphere(
        tmp_path,
        enable_label_audit=False,
        out_dir_name="gds_mcp_signed_conf_off",
    )
    _do_open_sphere(out)
    yield out
    if _state.get("session") is not None:
        try:
            _state["session"].close()
        except Exception:
            pass
    _state.update(saved)


def test_signed_confidence_mcp_smoke_returns_triad_fields(signed_conf_sphere):
    """Every polygon carries the new signed-confidence triad fields."""
    body = find_anomalies(
        pattern_id="tx_pattern",
        top_n=10,
        rank_by="signed_confidence",
    )
    parsed = json.loads(body)
    assert parsed["ranked_by"] == "signed_confidence"
    polys = parsed["polygons"]
    assert len(polys) > 0
    for p in polys:
        assert "signed_confidence_score" in p, p
        assert "lda_alignment" in p, p
        assert "reliability_penalty" in p, p
        # Numeric bounds match the formula domain.
        assert -1.0 <= float(p["lda_alignment"]) <= 1.0
        assert 0.0 <= float(p["reliability_penalty"]) <= 1.0
    # Descending sort on the signed score.
    scores = [float(p["signed_confidence_score"]) for p in polys]
    assert scores == sorted(scores, reverse=True), (
        f"polygons not sorted by signed_confidence_score desc: {scores}"
    )


def test_signed_confidence_mcp_fallback_raises_navigation_error(
    no_label_audit_sphere,
):
    """Pattern without label-aware calibration → ``GDSNavigationError``.

    Confirms the navigator raises the structured exception and that it
    surfaces with the explicit text the brief documents (so agents know
    to rebuild with ``label_audit:`` enabled, not switch to a different
    rank_by silently).
    """
    with pytest.raises(GDSNavigationError) as excinfo:
        find_anomalies(
            pattern_id="tx_pattern",
            top_n=5,
            rank_by="signed_confidence",
        )
    msg = str(excinfo.value)
    assert "signed_confidence ranking requires" in msg
    assert "label_audit:" in msg
    assert "rank_by='delta_norm'" in msg
