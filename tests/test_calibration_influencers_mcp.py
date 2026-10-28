# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""End-to-end MCP test for find_calibration_influencers + find_group_influence.

Drives full asdict serialisation path on the rebuilt Berka fixture (sphere
format 2.4) — regression guard for missing local imports per
feedback_mcp_serializer_imports_local.md.
"""

from __future__ import annotations

import json

import pytest

# Scored by delta_norm: the influencer classes are θ flips, refused on calibrated patterns.
_PID = "account_bank_pairs_pattern"


class TestFindCalibrationInfluencersMcp:
    def test_returns_valid_json_classify_all(self, open_berka_sphere):
        from hypertopos_mcp.server import _state
        from hypertopos_mcp.tools.analysis import find_calibration_influencers

        payload = find_calibration_influencers(
            pattern_id=_PID,
            top_n=5,
            classify="all",
        )
        report = json.loads(payload)
        expected = _state["sphere"]._sphere.patterns[_PID].population_size
        assert report["pattern_id"] == _PID
        assert report["population_size"] == expected
        assert sum(report["cell_counts"].values()) == expected
        assert len(report["entries"]) <= 5
        for entry in report["entries"]:
            assert "total_impact" in entry
            assert entry["classification"] in {
                "hidden",
                "distorter",
                "standard_anomaly",
                "normal",
            }
            assert "cascading_flip_count" not in entry  # verbose=False: field omitted

        # Patent-defensive end-to-end check: at least ONE cell beyond "normal"
        # must populate on real data. If the entire population classifies as
        # "normal", math regressed to noise under shape reconstruction
        # (delta * max(sigma_diag, 1e-2) + mu) — the unit-test pure-math guard
        # passes but the orchestrator's real-data path produces no signal.
        non_normal = sum(count for cell, count in report["cell_counts"].items() if cell != "normal")
        assert non_normal > 0, (
            "All Berka entities classified 'normal' — math collapsed "
            "under sigma_floor=1e-2 shape reconstruction. Hidden-influencer "
            "guard test passes in pure-math but orchestrator end-to-end fails."
        )

    def test_returns_valid_json_classify_hidden(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_calibration_influencers

        payload = find_calibration_influencers(
            pattern_id=_PID,
            top_n=5,
            classify="hidden",
        )
        report = json.loads(payload)
        for entry in report["entries"]:
            assert entry["classification"] == "hidden"

    def test_verbose_attaches_cascading(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import find_calibration_influencers

        payload = find_calibration_influencers(
            pattern_id=_PID,
            top_n=2,
            classify="all",
            verbose=True,
        )
        report = json.loads(payload)
        for entry in report["entries"]:
            assert entry["cascading_flip_count"] is not None
            assert isinstance(entry["cascading_flip_count"], int)
            assert entry["cascading_flip_count"] >= 0


class TestFindGroupInfluenceMcp:
    def test_returns_valid_json(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import (
            find_calibration_influencers,
            find_group_influence,
        )

        ranked = json.loads(
            find_calibration_influencers(
                pattern_id=_PID,
                top_n=3,
                classify="all",
            )
        )
        members = [e["entity_key"] for e in ranked["entries"][:3]]
        if len(members) < 2:
            pytest.skip("Need at least 2 entities to form a group")

        payload = find_group_influence(
            pattern_id=_PID,
            groups=[members[:2]],
        )
        body = json.loads(payload)
        assert "elapsed_ms" in body
        reports = body["results"]
        assert isinstance(reports, list)
        assert len(reports) == 1
        r = reports[0]
        assert r["member_count"] == 2
        assert r["members"] == members[:2]
        assert isinstance(r["reinforcing_factor"], float)


def test_calibrated_pattern_is_refused(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_calibration_influencers

    with pytest.raises(ValueError, match="calibrated score"):
        find_calibration_influencers(pattern_id="account_behavior_pattern", top_n=3)
