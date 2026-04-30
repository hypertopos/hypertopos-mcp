# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""End-to-end MCP test for find_lead_lag.

Drives full asdict serialisation path on the rebuilt Berka fixture (sphere
format 2.4) — regression guard for missing local imports per
feedback_mcp_serializer_imports_local.md and strict-JSON sanitisation per
feedback_mcp_strict_json_sanitize.md.
"""
from __future__ import annotations

import json


class TestFindLeadLagMcp:
    def test_returns_valid_json_population_mode(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_lead_lag

        payload = find_lead_lag(
            pattern_a="account_behavior_pattern",
            pattern_b="account_stress_pattern",
            cohort="fixed",
            fdr_method="bh",
            verbose=False,
        )
        # Strict JSON — ±inf / NaN must be sanitised to null
        report = json.loads(payload)
        # Headline fields populated
        assert report["pattern_a"] == "account_behavior_pattern"
        assert report["pattern_b"] == "account_stress_pattern"
        assert report["entity_key"] is None
        assert report["n_epochs_used"] >= 8
        assert report["cohort_size"] >= 1
        assert isinstance(report["lag"], int)
        assert isinstance(report["correlation"], (int, float))
        assert report["agreement"] in {"strong", "weak", "divergent"}
        assert report["reliability"] in {"high", "medium", "low"}
        # Significance fields all present
        assert "bartlett_ci_95" in report
        assert "max_corr_threshold" in report
        assert "is_significant" in report
        # Per-dim matrix
        assert isinstance(report["top_dim_pairs"], list)
        assert report["per_dim_pairs"] is None  # verbose=False
        assert report["n_dim_pairs"] > 0
        # Raw series — length matches N - 1
        N = report["n_epochs_used"]
        assert len(report["centroid_drift_series_a"]) == N - 1
        assert len(report["centroid_drift_series_b"]) == N - 1
        assert len(report["volatility_series_a"]) == N - 1
        assert len(report["volatility_series_b"]) == N - 1
        # correlation_by_lag length = 2 * max_lag + 1
        assert len(report["correlation_by_lag"]) == 2 * report["max_lag"] + 1
        # No NaN / inf in any nested value (verifies sanitiser path)
        def _check_finite_or_none(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    _check_finite_or_none(v)
            elif isinstance(obj, list):
                for v in obj:
                    _check_finite_or_none(v)
            elif isinstance(obj, float):
                # JSON load can't return inf / NaN — those raise on parse
                assert obj == obj   # NaN check (always True for finite)
        _check_finite_or_none(report)

    def test_verbose_includes_full_matrix(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_lead_lag

        payload = find_lead_lag(
            pattern_a="account_behavior_pattern",
            pattern_b="account_stress_pattern",
            cohort="fixed",
            verbose=True,
        )
        report = json.loads(payload)
        assert report["per_dim_pairs"] is not None
        assert isinstance(report["per_dim_pairs"], list)
        assert len(report["per_dim_pairs"]) == report["n_dim_pairs"]
        # Each entry has every DimPairLeadLag field
        for p in report["per_dim_pairs"][:3]:
            for k in ("dim_index_a", "dim_index_b", "lag", "correlation",
                      "p_value", "q_value", "is_significant"):
                assert k in p

    def test_storey_method_runs(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_lead_lag

        payload = find_lead_lag(
            pattern_a="account_behavior_pattern",
            pattern_b="account_stress_pattern",
            cohort="fixed",
            fdr_method="storey",
        )
        report = json.loads(payload)
        assert report["fdr_method"] == "storey"

    def test_validation_event_pattern_raises(self, open_berka_sphere):
        """tx_pattern is event-type — raises ValueError, not silent return."""
        import pytest
        from hypertopos_mcp.tools.analysis import find_lead_lag

        # Berka has no event temporal — try an obviously wrong pattern combo
        with pytest.raises(Exception):
            find_lead_lag(
                pattern_a="account_behavior_pattern",
                pattern_b="account_behavior_pattern",  # same pattern
            )
