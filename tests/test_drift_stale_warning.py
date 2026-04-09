# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Test that find_drifting_entities exposes slice_window_days and stale forecast warning."""

from __future__ import annotations

import json
from datetime import UTC, datetime


class TestDriftSliceWindowDays:
    """Drift results should include slice_window_days."""

    def test_drift_results_have_slice_window_days(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_drifting_entities

        result = json.loads(
            find_drifting_entities(
                pattern_id="account_behavior_pattern",
                top_n=3,
                sample_size=5000,
            )
        )
        assert result.get("count", 0) > 0, "No drift results"
        for entry in result["results"]:
            assert "slice_window_days" in entry, (
                f"Missing slice_window_days for {entry['primary_key']}"
            )
            assert isinstance(entry["slice_window_days"], int)
            assert entry["slice_window_days"] >= 0

    def test_slice_window_days_consistent_with_timestamps(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_drifting_entities

        result = json.loads(
            find_drifting_entities(
                pattern_id="account_behavior_pattern",
                top_n=3,
                sample_size=5000,
            )
        )
        for entry in result["results"]:
            first = datetime.fromisoformat(entry["first_timestamp"])
            last = datetime.fromisoformat(entry["last_timestamp"])
            expected_days = (last - first).days
            assert entry["slice_window_days"] == expected_days, (
                f"{entry['primary_key']}: expected {expected_days}, got {entry['slice_window_days']}"  # noqa: E501
            )


class TestDriftStaleForecastWarning:
    """Stale forecasts should be flagged."""

    def test_stale_forecast_forced_low_reliability(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_drifting_entities

        result = json.loads(
            find_drifting_entities(
                pattern_id="account_behavior_pattern",
                top_n=3,
                sample_size=5000,
                forecast_horizon=3,
            )
        )
        now = datetime.now(UTC)
        for entry in result["results"]:
            if "drift_forecast" not in entry:
                continue
            last = datetime.fromisoformat(entry["last_timestamp"])
            days_ago = (now - last).days
            if days_ago > 180:
                assert entry["drift_forecast"]["reliability"] == "low", (
                    f"{entry['primary_key']}: last_timestamp {days_ago} days ago "
                    f"but reliability={entry['drift_forecast']['reliability']}"
                )
                assert "stale_warning" in entry["drift_forecast"], (
                    f"{entry['primary_key']}: missing stale_warning"
                )

