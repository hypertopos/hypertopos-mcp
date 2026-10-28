# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for MCP tools that previously had no test coverage.

Each test calls the tool function directly (bypassing the MCP tier gate),
parses the JSON response, and asserts structural invariants sufficient to
catch a broken serializer or wrong response shape.

The five edge/motif/chain tools are tested in two modes:
  - Error-path on the Berka synth sphere (no edge table, no chain line)
    → pytest.raises(ToolError, match=<stable message fragment>)
  - Happy-path on a minimal sphere that HAS an edge table and chain line
    → assert documented top-level keys, no "error" key
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Pattern and entity constants derived from sphere.yaml / generate.py
_ANCHOR_PATTERN = "account_behavior_pattern"
_ENTITY_KEYS = ["1", "2", "3"]
_LINE_ID = "accounts"


# ---------------------------------------------------------------------------
# Mini sphere with edge table + chain line (for happy-path tests)
# ---------------------------------------------------------------------------


def _build_mini_edge_sphere(out_root: Path) -> None:
    """Build a minimal sphere with an edge table and a chain line.

    Extends the chain-sphere design from test_chain_anchor_aggregations_mcp.py
    by adding an account_pattern anchor so that score_edge / motif tools can
    resolve a graph companion via _resolve_edge_pattern_for_anchor.
    """
    from hypertopos.builder import GDSBuilder, RelationSpec
    from hypertopos.builder.builder import EdgeTableConfig

    b = GDSBuilder("mini_edge_sphere", str(out_root))
    b.add_line(
        "accounts",
        [
            {"acct_id": "A", "region": "North"},
            {"acct_id": "B", "region": "South"},
            {"acct_id": "C", "region": "North"},
            {"acct_id": "D", "region": "East"},
            {"acct_id": "E", "region": "West"},
        ],
        key_col="acct_id",
        source_id="t",
    )
    b.add_line(
        "transactions",
        [
            {"tx_id": "e1", "from_acct": "A", "to_acct": "B", "ts": 1000.0, "amount": 10000.0},
            {"tx_id": "e2", "from_acct": "B", "to_acct": "C", "ts": 2000.0, "amount": 5000.0},
            {"tx_id": "e3", "from_acct": "C", "to_acct": "D", "ts": 3000.0, "amount": 2500.0},
            {"tx_id": "e4", "from_acct": "A", "to_acct": "E", "ts": 4000.0, "amount": 8000.0},
            {"tx_id": "e5", "from_acct": "D", "to_acct": "E", "ts": 5000.0, "amount": 1200.0},
            {"tx_id": "e6", "from_acct": "E", "to_acct": "A", "ts": 6000.0, "amount": 600.0},
        ],
        key_col="tx_id",
        source_id="t",
    )
    b.add_pattern(
        "tx_pattern",
        pattern_type="event",
        entity_line="transactions",
        relations=[
            RelationSpec("accounts", fk_col="from_acct", direction="in", required=True),
        ],
        edge_table=EdgeTableConfig(
            from_col="from_acct",
            to_col="to_acct",
            timestamp_col="ts",
            amount_col="amount",
        ),
    )
    # Anchor pattern on accounts — gives score_edge / motif tools a graph companion.
    # tracked_properties provides the required geometry dimension.
    b.add_pattern(
        "account_pattern",
        pattern_type="anchor",
        entity_line="accounts",
        relations=[],
        tracked_properties=["region"],
    )
    # Chain line + anchor pattern — gives chain_investigation_summary a valid target
    chains = [
        {
            "chain_id": "ch1",
            "keys": ["A", "B", "C"],
            "event_keys": ["e1", "e2"],
            "hop_count": 2,
            "is_cyclic": 0.0,
        },
        {
            "chain_id": "ch2",
            "keys": ["A", "E", "A"],
            "event_keys": ["e4", "e6"],
            "hop_count": 2,
            "is_cyclic": 1.0,
        },
        {
            "chain_id": "ch3",
            "keys": ["D", "E"],
            "event_keys": ["e5"],
            "hop_count": 1,
            "is_cyclic": 0.0,
        },
    ]
    b.add_chain_line("tx_chains", chains=chains, features=["hop_count", "is_cyclic"])
    b.add_pattern(
        "tx_chains_pattern",
        pattern_type="anchor",
        entity_line="tx_chains",
        relations=[],
    )
    b.build()


@pytest.fixture(scope="module")
def _mini_sphere_path(tmp_path_factory):
    """Build the mini sphere once per module."""
    out = tmp_path_factory.mktemp("gds_mini") / "s"
    _build_mini_edge_sphere(out)
    return out


@pytest.fixture
def mini_edge_sphere(_mini_sphere_path):
    """Open the pre-built mini sphere via MCP; close it (and restore Berka) after."""
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import close_sphere, open_sphere

    open_sphere(str(_mini_sphere_path))
    try:
        yield str(_mini_sphere_path)
    finally:
        try:
            close_sphere()
        except Exception:
            for k in list(_state.keys()):
                _state[k] = None


# ---------------------------------------------------------------------------
# calibration_influencer_history
# ---------------------------------------------------------------------------


class TestCalibrationInfluencerHistory:
    def test_returns_valid_json_for_known_entity(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import calibration_influencer_history

        raw = calibration_influencer_history(
            primary_key=_ENTITY_KEYS[0],
            pattern_id=_ANCHOR_PATTERN,
        )
        result = json.loads(raw)
        assert "history" in result, f"missing 'history' key: {list(result)}"
        assert isinstance(result["history"], list)
        assert "n_epochs" in result
        assert "elapsed_ms" in result
        # When cache is empty a populate hint should be present
        if result["n_epochs"] == 0:
            assert "hint" in result


# ---------------------------------------------------------------------------
# chain_investigation_summary — error-path + happy-path
# ---------------------------------------------------------------------------


class TestChainInvestigationSummary:
    def test_non_chain_line_raises_tool_error(self, open_berka_sphere):
        """account_behavior_pattern has no chain_keys column → ToolError."""
        from mcp.server.mcpserver.exceptions import ToolError

        from hypertopos_mcp.tools.analysis import chain_investigation_summary

        with pytest.raises(ToolError, match="chain_keys"):
            chain_investigation_summary(
                chain_pattern_id=_ANCHOR_PATTERN,
                anchor_pattern_id=_ANCHOR_PATTERN,
                max_runs=10,
            )

    def test_happy_path_returns_summary_fields(self, mini_edge_sphere):
        """Valid chain sphere returns the documented top-level summary keys."""
        from hypertopos_mcp.tools.analysis import chain_investigation_summary

        raw = chain_investigation_summary(
            chain_pattern_id="tx_chains_pattern",
            anchor_pattern_id="account_pattern",
            max_runs=50,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "chain_pattern_id" in result
        assert result["chain_pattern_id"] == "tx_chains_pattern"
        assert "n_chains_total" in result
        assert "coherent_run_rate" in result
        assert "elapsed_ms" in result


# ---------------------------------------------------------------------------
# find_topological_anomalies
# ---------------------------------------------------------------------------


class TestFindTopologicalAnomalies:
    def test_small_sphere_returns_json_error(self, open_berka_sphere):
        """121 accounts < 1000 required by PH; documented to return JSON error."""
        pytest.importorskip("ripser")  # optional [topology] extra, absent in CI
        from hypertopos_mcp.tools.analysis import find_topological_anomalies

        raw = find_topological_anomalies(
            pattern_id=_ANCHOR_PATTERN,
            top_n=5,
            sample_size=5000,
        )
        result = json.loads(raw)
        assert "error" in result, f"expected 'error' key, got: {list(result)}"
        assert "pattern_id" in result
        assert result["pattern_id"] == _ANCHOR_PATTERN


# ---------------------------------------------------------------------------
# check_anomaly_batch
# ---------------------------------------------------------------------------


class TestCheckAnomalyBatch:
    def test_returns_batch_result_for_known_keys(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import check_anomaly_batch

        raw = check_anomaly_batch(
            primary_keys=_ENTITY_KEYS,
            pattern_id=_ANCHOR_PATTERN,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert result["pattern_id"] == _ANCHOR_PATTERN
        assert result["total_requested"] == len(_ENTITY_KEYS)
        assert "total_found" in result
        assert "anomalous_count" in result
        assert "recall_if_all_bad" in result
        assert isinstance(result["results"], list)
        for item in result["results"]:
            assert "primary_key" in item
            assert "is_anomaly" in item
            assert "score_rank_pct" in item

    def test_hard_cap_returns_error(self, open_berka_sphere):
        from hypertopos_mcp.tools.analysis import check_anomaly_batch

        raw = check_anomaly_batch(
            primary_keys=[str(i) for i in range(501)], pattern_id=_ANCHOR_PATTERN
        )
        result = json.loads(raw)
        assert "error" in result


# ---------------------------------------------------------------------------
# score_edge — error-path + happy-path
# ---------------------------------------------------------------------------


class TestScoreEdge:
    def test_no_edge_table_raises_tool_error(self, open_berka_sphere):
        """Berka sphere has no edge table → ToolError naming missing capability."""
        from mcp.server.mcpserver.exceptions import ToolError

        from hypertopos_mcp.tools.analysis import score_edge

        with pytest.raises(ToolError, match="no graph companion"):
            score_edge(
                from_key=_ENTITY_KEYS[0],
                to_key=_ENTITY_KEYS[1],
                pattern_id=_ANCHOR_PATTERN,
                include_ranking=False,
            )

    def test_happy_path_returns_score_fields(self, mini_edge_sphere):
        from hypertopos_mcp.tools.analysis import score_edge

        raw = score_edge(
            from_key="A",
            to_key="B",
            pattern_id="account_pattern",
            include_ranking=False,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "score" in result
        assert "delta_distance" in result
        assert "pair_tx_count" in result
        assert "ranking_note" in result  # omitted when include_ranking=False


# ---------------------------------------------------------------------------
# find_high_potential_edges — error-path + happy-path
# ---------------------------------------------------------------------------


class TestFindHighPotentialEdges:
    def test_no_edge_table_raises_tool_error(self, open_berka_sphere):
        from mcp.server.mcpserver.exceptions import ToolError

        from hypertopos_mcp.tools.analysis import find_high_potential_edges

        with pytest.raises(ToolError, match="no graph companion"):
            find_high_potential_edges(
                pattern_id=_ANCHOR_PATTERN,
                top_n=3,
            )

    def test_happy_path_returns_edge_list(self, mini_edge_sphere):
        from hypertopos_mcp.tools.analysis import find_high_potential_edges

        raw = find_high_potential_edges(
            pattern_id="account_pattern",
            top_n=3,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "pattern_id" in result
        assert result["pattern_id"] == "account_pattern"
        assert "count" in result
        assert isinstance(result["results"], list)
        for edge in result["results"]:
            assert "from_key" in edge
            assert "to_key" in edge
            assert "score" in edge


# ---------------------------------------------------------------------------
# score_motif — error-path + happy-path
# ---------------------------------------------------------------------------


class TestScoreMotif:
    def test_no_edge_table_raises_tool_error(self, open_berka_sphere):
        from mcp.server.mcpserver.exceptions import ToolError

        from hypertopos_mcp.tools.analysis import score_motif

        with pytest.raises(ToolError, match="no event pattern companion"):
            score_motif(
                primary_key=_ENTITY_KEYS[0],
                motif_type="fan_out",
                pattern_id=_ANCHOR_PATTERN,
            )

    def test_happy_path_returns_found_or_not_found(self, mini_edge_sphere):
        from hypertopos_mcp.tools.analysis import score_motif

        raw = score_motif(
            primary_key="A",
            motif_type="fan_out",
            pattern_id="account_pattern",
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "found" in result
        assert "motif_type" in result
        assert result["motif_type"] == "fan_out"
        if result["found"]:
            assert "score" in result
            assert "edges_total_count" in result
            assert "edges_truncated" in result


# ---------------------------------------------------------------------------
# find_high_potential_motifs — error-path + happy-path
# ---------------------------------------------------------------------------


class TestFindHighPotentialMotifs:
    def test_no_edge_table_raises_tool_error(self, open_berka_sphere):
        from mcp.server.mcpserver.exceptions import ToolError

        from hypertopos_mcp.tools.analysis import find_high_potential_motifs

        with pytest.raises(ToolError, match="no event pattern companion"):
            find_high_potential_motifs(
                pattern_id=_ANCHOR_PATTERN,
                motif_type="fan_out",
                top_n=3,
            )

    def test_happy_path_returns_ranked_list(self, mini_edge_sphere):
        from hypertopos_mcp.tools.analysis import find_high_potential_motifs

        raw = find_high_potential_motifs(
            pattern_id="account_pattern",
            motif_type="fan_out",
            top_n=3,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "pattern_id" in result
        assert result["pattern_id"] == "account_pattern"
        assert "count" in result
        assert isinstance(result["results"], list)


# ---------------------------------------------------------------------------
# classify_trajectory
# ---------------------------------------------------------------------------


class TestClassifyTrajectory:
    def test_returns_category_fields(self, open_berka_sphere):
        from hypertopos_mcp.tools.detection import classify_trajectory

        raw = classify_trajectory(
            primary_key=_ENTITY_KEYS[0],
            pattern_id=_ANCHOR_PATTERN,
            sample_size=5000,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "primary_key" in result
        assert result["primary_key"] == _ENTITY_KEYS[0]
        assert "pattern_id" in result
        assert "category" in result
        assert result["category"] in {"outlier", "lagging", "leading", "typical", "unknown"}
        assert "dtw_distance" in result
        assert "category_evidence" in result


# ---------------------------------------------------------------------------
# detect_data_quality_issues
# ---------------------------------------------------------------------------


class TestDetectDataQualityIssues:
    def test_returns_findings_list(self, open_berka_sphere):
        from hypertopos_mcp.tools.observability import detect_data_quality_issues

        raw = detect_data_quality_issues(
            pattern_id=_ANCHOR_PATTERN,
            sample_size=5000,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "pattern_id" in result
        assert result["pattern_id"] == _ANCHOR_PATTERN
        assert "findings" in result
        assert isinstance(result["findings"], list)


# ---------------------------------------------------------------------------
# line_geometry_stats
# ---------------------------------------------------------------------------
# account_behavior_pattern's relations are derived dimension lines (_d_*).
_DIM_LINE_ID = "_d_tx_count"


class TestLineGeometryStats:
    def test_returns_coverage_and_distribution(self, open_berka_sphere):
        from hypertopos_mcp.tools.observability import line_geometry_stats

        raw = line_geometry_stats(
            line_id=_DIM_LINE_ID,
            pattern_id=_ANCHOR_PATTERN,
            sample_size=5000,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "coverage_pct" in result
        assert "edge_distribution" in result


# ---------------------------------------------------------------------------
# get_line_profile
# ---------------------------------------------------------------------------


class TestGetLineProfile:
    def test_returns_profile_for_categorical_column(self, open_berka_sphere):
        from hypertopos_mcp.tools.session import get_line_profile

        raw = get_line_profile(
            line_id=_LINE_ID,
            property_name="frequency",
            top_n=10,
        )
        result = json.loads(raw)
        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "line_id" in result
        assert result["line_id"] == _LINE_ID
        assert "property" in result
        assert result["property"] == "frequency"
