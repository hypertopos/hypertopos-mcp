# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tests for pivot_labels enrichment in aggregate with pivot_event_field."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pyarrow as pa


def _make_batch_side(read_points_side):
    """Return a read_points_batch mock that delegates to read_points_side."""
    import pyarrow.compute as _pc

    def _batch(line_id, version, primary_keys):
        table = read_points_side(line_id, version)
        mask = _pc.is_in(table["primary_key"], pa.array(primary_keys, type=pa.string()))
        return table.filter(mask)

    return _batch


def _make_core_aggregate_result():
    """Return core aggregate result for pivot_event_field='season_id'."""
    return {
        "event_pattern_id": "gl_entry_pattern",
        "group_by_line": "company_codes",
        "pivot_event_field": "season_id",
        "metric": "count",
        "total_groups": 2,
        "offset": 0,
        "results": [
            {"key": "CC-PL", "name": "HyperCorp PL", "SE-001": 2, "SE-002": 1},
            {"key": "CC-DE", "name": "HyperCorp DE", "SE-001": 1},
        ],
        "sampled": False,
    }


def _setup_state_with_nav(
    core_result,
    lines=None,
    extra_read_points_side=None,
):
    """Set up _state with a navigator mock that returns core_result."""
    from hypertopos_mcp.server import _state

    nav = MagicMock()
    nav.aggregate.return_value = core_result

    mock_seasons_line = MagicMock()
    mock_seasons_line.line_role = "anchor"
    mock_seasons_line.versions = [1]

    mock_cc_line = MagicMock()
    mock_cc_line.line_role = "anchor"
    mock_cc_line.versions = [1]

    mock_gl_line = MagicMock()
    mock_gl_line.line_id = "gl_entries"
    mock_gl_line.line_role = "event"
    mock_gl_line.entity_type = "gl_entries"
    mock_gl_line.versions = [1]

    all_lines = {
        "gl_entries": mock_gl_line,
        "company_codes": mock_cc_line,
    }
    if lines:
        all_lines.update(lines)

    sphere_meta = MagicMock()
    sphere_meta.patterns = {"gl_entry_pattern": MagicMock(version=1)}
    sphere_meta.lines = all_lines
    sphere_meta.aliases = {}

    sphere = MagicMock()
    sphere._sphere = sphere_meta

    seasons_points = pa.table(
        {
            "primary_key": pa.array(["SE-001", "SE-002", "SE-003"]),
            "name": pa.array(["Summer 2018", "Winter 2019", "Spring 2020"]),
        }
    )

    cc_points = pa.table(
        {
            "primary_key": pa.array(["CC-PL", "CC-DE"]),
            "name": pa.array(["HyperCorp PL", "HyperCorp DE"]),
        }
    )

    def default_read_points_side(line_id, version):
        if line_id == "seasons":
            return seasons_points
        if line_id == "company_codes":
            return cc_points
        return pa.table({"primary_key": pa.array([], type=pa.string())})

    side = extra_read_points_side or default_read_points_side

    reader = MagicMock()
    reader.read_points.side_effect = side

    session = MagicMock()
    session._reader = reader

    _state["sphere"] = sphere
    _state["session"] = session
    _state["navigator"] = nav


class TestPivotLabels:
    """pivot_labels enrichment when pivot_event_field ends with _id."""

    def teardown_method(self):
        from hypertopos_mcp.server import _state

        for k in list(_state.keys()):
            _state[k] = None

    def test_pivot_labels_present_for_id_field(self) -> None:
        """pivot_event_field ending with _id adds pivot_labels dict."""
        lines = {"seasons": MagicMock(line_role="anchor", versions=[1])}
        _setup_state_with_nav(_make_core_aggregate_result(), lines=lines)

        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "gl_entry_pattern",
                "company_codes",
                pivot_event_field="season_id",
            )
        )
        assert "pivot_labels" in result
        assert result["pivot_labels"] == {
            "SE-001": "Summer 2018",
            "SE-002": "Winter 2019",
        }

    def test_pivot_labels_absent_for_non_id_field(self) -> None:
        """pivot_event_field NOT ending with _id does not add pivot_labels."""
        core = _make_core_aggregate_result()
        core["pivot_event_field"] = "fiscal_year"
        _setup_state_with_nav(core)

        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "gl_entry_pattern",
                "company_codes",
                pivot_event_field="fiscal_year",
            )
        )
        assert "pivot_labels" not in result

    def test_pivot_labels_absent_when_line_not_found(self) -> None:
        """pivot_event_field ending with _id but no matching line: no pivot_labels."""
        core = _make_core_aggregate_result()
        # Use "region_id" — no "regions" line exists
        core["pivot_event_field"] = "region_id"
        core["results"] = [
            {"key": "CC-PL", "R-001": 2, "R-002": 1},
        ]
        _setup_state_with_nav(core)

        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "gl_entry_pattern",
                "company_codes",
                pivot_event_field="region_id",
            )
        )
        assert "pivot_labels" not in result

    def test_pivot_labels_only_includes_used_values(self) -> None:
        """pivot_labels only includes IDs that actually appear in the pivot results."""
        lines = {"seasons": MagicMock(line_role="anchor", versions=[1])}
        _setup_state_with_nav(_make_core_aggregate_result(), lines=lines)

        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "gl_entry_pattern",
                "company_codes",
                pivot_event_field="season_id",
            )
        )
        labels = result["pivot_labels"]
        # SE-003 exists in the seasons line but is not used in any pivot result
        assert "SE-003" not in labels
        # Only SE-001 and SE-002 are used
        assert set(labels.keys()) == {"SE-001", "SE-002"}

    def test_pivot_labels_skips_null_names(self) -> None:
        """pivot_labels excludes entries where the name column is null."""
        lines = {"seasons": MagicMock(line_role="anchor", versions=[1])}

        seasons_with_null = pa.table(
            {
                "primary_key": pa.array(["SE-001", "SE-002"]),
                "name": pa.array([None, "Winter 2019"]),
            }
        )

        def rp_side(line_id, version):
            if line_id == "seasons":
                return seasons_with_null
            return pa.table(
                {
                    "primary_key": pa.array(["CC-PL", "CC-DE"]),
                    "name": pa.array(["HyperCorp PL", "HyperCorp DE"]),
                }
            )

        _setup_state_with_nav(
            _make_core_aggregate_result(),
            lines=lines,
            extra_read_points_side=rp_side,
        )

        from hypertopos_mcp.tools.aggregation import aggregate

        result = json.loads(
            aggregate(
                "gl_entry_pattern",
                "company_codes",
                pivot_event_field="season_id",
            )
        )
        labels = result.get("pivot_labels", {})
        # SE-001 has null name — excluded
        assert "SE-001" not in labels
        # SE-002 has a valid name
        assert labels.get("SE-002") == "Winter 2019"
