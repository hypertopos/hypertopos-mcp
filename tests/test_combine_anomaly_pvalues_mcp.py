# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""End-to-end MCP test for combine_anomaly_pvalues + composite_risk HMP shape.

Drives the full strict-JSON path on the Berka fixture — regression guard
for the M1.6 contract (HMP combiner exposed at the MCP layer, no chi2/df
fields surfacing on composite_risk).
"""
from __future__ import annotations

import json
import math


class TestCombineAnomalyPvaluesMcp:
    def test_returns_valid_strict_json(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import combine_anomaly_pvalues

        payload = combine_anomaly_pvalues(
            pattern_id="account_behavior_pattern",
            sample_size=500,
            top_n=5,
        )
        # strict=True rejects Infinity / NaN literals — _sanitize_for_json
        # must have folded any non-finite navigator outputs to null.
        results = json.loads(payload, parse_constant=lambda c: (_ for _ in ()).throw(
            AssertionError(f"Non-strict JSON literal {c!r} survived sanitisation"),
        ))
        assert isinstance(results, list)
        assert len(results) <= 5
        for entry in results:
            assert "primary_key" in entry
            assert "hmp" in entry
            assert "p_per_detector" in entry
            assert "rank" in entry
            hmp = entry["hmp"]
            # hmp survives sanitisation as a finite float in (0, 1].
            assert hmp is None or (isinstance(hmp, float) and math.isfinite(hmp))
            assert isinstance(entry["p_per_detector"], dict)
            assert entry["p_per_detector"], (
                "every returned entry must have at least one calibrated detector"
            )

    def test_ranking_is_ascending_by_hmp(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import combine_anomaly_pvalues

        payload = combine_anomaly_pvalues(
            pattern_id="account_behavior_pattern",
            sample_size=500,
            top_n=10,
        )
        results = json.loads(payload)
        hmps = [r["hmp"] for r in results if r.get("hmp") is not None]
        assert hmps == sorted(hmps), "results must be ranked ascending by hmp"
        for i, entry in enumerate(results, start=1):
            assert entry["rank"] == i

    def test_detector_subset_passes_through(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import combine_anomaly_pvalues

        payload = combine_anomaly_pvalues(
            pattern_id="account_behavior_pattern",
            detectors=["delta_norm"],
            sample_size=500,
            top_n=3,
        )
        results = json.loads(payload)
        assert len(results) > 0
        # delta_norm is the always-available primary path; with the subset
        # narrowed to {delta_norm}, every entry's per-detector keys must be
        # exactly {delta_norm}.
        for entry in results:
            assert set(entry["p_per_detector"].keys()) == {"delta_norm"}

    def test_unknown_pattern_returns_error_envelope(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import combine_anomaly_pvalues

        payload = combine_anomaly_pvalues(
            pattern_id="nonexistent_pattern_xyz",
            sample_size=500,
            top_n=3,
        )
        envelope = json.loads(payload)
        assert isinstance(envelope, dict)
        assert "error" in envelope


class TestCompositeRiskHmpShape:
    """Guard that composite_risk no longer surfaces Fisher chi2 / df fields."""

    def test_no_chi2_df_in_response(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import composite_risk_batch

        # Pull a couple of arbitrary keys via cross_pattern profile is overkill;
        # we test the shape of the envelope, not specific risk values.
        payload = composite_risk_batch(
            primary_keys=["1", "2", "3"],
            line_id="account_line",
        )
        report = json.loads(payload)
        # Top-level envelope holds counts.
        assert "results" in report
        assert "caught_p010" in report
        assert "caught_p005" in report
        for entry in report["results"]:
            # HMP-only fields permitted.
            assert "chi2" not in entry, (
                "Fisher chi2 must not surface in HMP-era composite_risk"
            )
            assert "df" not in entry, (
                "Fisher df must not surface in HMP-era composite_risk"
            )
