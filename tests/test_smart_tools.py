# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for smart detection meta-tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from hypertopos_mcp.server import _state
from hypertopos_mcp.tools.smart import _available_steps, _fallback_plan, detect_pattern


class TestFallbackPlan:
    """Test keyword-based fallback when sampling is unavailable."""

    def test_trajectory_keywords(self):
        plan = _fallback_plan(
            "find arch trajectories",
            ["detect_trajectory_anomaly", "find_anomalies"],
            {"pat_a": {"type": "anchor", "entity_line": "suppliers"}},
        )
        assert any(s["name"] == "detect_trajectory_anomaly" for s in plan["steps"])

    def test_segment_keywords(self):
        plan = _fallback_plan(
            "which segments have shifted anomaly rates",
            ["detect_segment_shift", "find_anomalies"],
            {"pat_a": {"type": "anchor", "entity_line": "suppliers"}},
        )
        assert any(s["name"] == "detect_segment_shift" for s in plan["steps"])

    def test_default_to_anomalies(self):
        plan = _fallback_plan(
            "show me something interesting",
            ["find_anomalies"],
            {"pat_a": {"type": "anchor", "entity_line": "suppliers"}},
        )
        assert plan["steps"][0]["name"] == "find_anomalies"

    def test_no_unavailable_steps(self):
        plan = _fallback_plan(
            "find arch trajectories",
            ["find_anomalies"],  # trajectory not available
            {"pat_a": {"type": "anchor", "entity_line": "suppliers"}},
        )
        assert not any(s["name"] == "detect_trajectory_anomaly" for s in plan["steps"])

    def test_entity_context_tools_excluded(self):
        """Tools needing entity-specific params (primary_key, from_col, etc.)
        must not appear in keyword fallback.

        Note: `attract_boundary` is intentionally NOT in this set — it needs
        only `alias_id`, which the keyword path supplies automatically by
        picking the first sphere alias when one exists. See the
        `elif step_name == "attract_boundary"` branch in `_fallback_plan`.
        """
        entity_tools = {
            "find_counterparties", "extract_chains", "find_common_relations",
            "get_centroid_map", "assess_false_positive",
            "trace_root_cause", "detect_composite_subgroup_inflation",
            "hub_history",
        }
        # query triggers every keyword bucket; all entity tools marked available
        query = (
            "counterpart chain common relation centroid "
            "false positive root cause subgroup inflat hub history"
        )
        plan = _fallback_plan(
            query,
            list(entity_tools) + ["find_anomalies"],
            {"pat_a": {"type": "anchor", "entity_line": "accounts"}},
        )
        step_names = {s["name"] for s in plan["steps"]}
        assert not step_names & entity_tools, (
            f"entity-context tools should be excluded: {step_names & entity_tools}"
        )


class TestAvailableSteps:
    def test_all_available_with_full_caps(self):
        with patch("hypertopos_mcp.server._sphere_capabilities", {
            "has_temporal": True,
            "multi_pattern": True,
            "has_trajectory_index": True,
        }):
            steps = _available_steps()
            assert "detect_trajectory_anomaly" in steps
            assert "detect_cross_pattern_discrepancy" in steps

    def test_temporal_hidden_without_capability(self):
        with patch("hypertopos_mcp.server._sphere_capabilities", {
            "has_temporal": False,
            "multi_pattern": False,
        }):
            steps = _available_steps()
            assert "detect_trajectory_anomaly" not in steps
            assert "find_regime_changes" not in steps
            assert "find_anomalies" in steps  # always available


class TestDetectPatternFallback:
    """Test detect_pattern without sampling (ctx=None)."""

    def setup_method(self):
        self.nav = MagicMock()
        _state["navigator"] = self.nav
        _state["sphere"] = MagicMock()

        # Mock sphere structure
        fake_pattern = MagicMock()
        fake_pattern.pattern_type = "anchor"
        fake_sphere = MagicMock()
        fake_sphere.patterns = {"pat_a": fake_pattern}
        fake_sphere.entity_line.return_value = "suppliers"
        _state["sphere"]._sphere = fake_sphere

    def teardown_method(self):
        _state["navigator"] = None
        _state["sphere"] = None

    @pytest.mark.asyncio
    async def test_fallback_plan_used_when_no_ctx(self):
        with patch("hypertopos_mcp.server._sphere_capabilities", {"has_trajectory_index": True}):
            self.nav.detect_trajectory_anomaly.return_value = [
                {"entity_key": "S001", "trajectory_shape": "arch"}
            ]
            result = json.loads(await detect_pattern("find arch trajectories", ctx=None))
            assert result["plan"]["rationale"] == "keyword-based fallback (sampling unavailable)"
            assert "detect_trajectory_anomaly" in result["results"]

    @pytest.mark.asyncio
    async def test_step_error_captured(self):
        with patch("hypertopos_mcp.server._sphere_capabilities", {}):
            self.nav.π5_attract_anomaly.side_effect = RuntimeError("boom")
            result = json.loads(await detect_pattern("show anomalies", ctx=None))
            assert "error" in result["results"].get("find_anomalies", {})
