# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Aggregation tools — count, sum, avg, min, max with filtering and enrichment."""

from __future__ import annotations

import json
from typing import Literal

from hypertopos_mcp.server import _require_sphere, _state, mcp, timed


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def aggregate(
    event_pattern_id: str,
    group_by_line: str,
    metric: str = "count",
    filters: list[dict] | None = None,
    group_by_property: str | None = None,
    distinct: bool = False,
    collapse_by_property: bool = False,
    limit: int = 20,
    offset: int = 0,
    pivot_event_field: str | None = None,
    sample_size: int | None = None,
    sample_pct: float | None = None,
    seed: int | None = None,
    geometry_filters: dict | None = None,
    property_filters: dict | None = None,
    include_properties: list[str] | None = None,
    sort: Literal["desc", "asc"] = "desc",
    filter_by_keys: list[str] | None = None,
    event_filters: dict | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    missing_edge_to: str | None = None,
    having: dict | None = None,
    entity_filters: dict | None = None,
    group_by_line_2: str | None = None,
) -> str:
    """Aggregate event polygons grouped by a dimension line. Metrics: count, count_distinct, sum, avg, min, max, median, pct<N>.

    group_by_line: declared relation of the event pattern.
    metric: "count" (default), "sum:<field>", "avg:<field>", "median:<field>", "pct90:<field>", "count_distinct:<line_id>".
    group_by_property: "line_id:property_name" for property-level grouping.
    group_by_line_2: second grouping line for multi-level GROUP BY.
    filters: [{"line": str, "key": str}] partition filters. time_from/time_to: ISO-8601 event date range.
    geometry_filters: {"is_anomaly": bool, "delta_rank_pct": {"gt": N}, "delta_dim": {...}, "alias_inside": "id"}.
    property_filters: filter group_by_line entities ({"prop": {"op": "<", "value": N}}).
    entity_filters: filter pattern's own entity line (same syntax as property_filters).
    event_filters: filter event line columns ({"col": {"gt": N}}). Null checks: {"col": null}.
    having: post-aggregation filter ({"gt": X, "lt": Y}). missing_edge_to: orphan filter.
    distinct/collapse_by_property: collapse to property-value groups. pivot_event_field: pivot by event column.
    sample_size/sample_pct/seed: sampling. filter_by_keys: scope to specific polygon keys.
    sort: "desc" (default) or "asc". include_properties: add entity props to result rows.
    Returns: per-group metric values, total_groups, total_eligible.
    """  # noqa: E501
    _require_sphere()
    nav = _state["navigator"]
    if nav is None:
        raise RuntimeError("Navigator not available. Call open_sphere() first.")

    # Delegate to core aggregation engine
    result = nav.aggregate(
        event_pattern_id=event_pattern_id,
        group_by_line=group_by_line,
        metric=metric,
        filters=filters,
        group_by_property=group_by_property,
        distinct=distinct,
        collapse_by_property=collapse_by_property,
        geometry_filters=geometry_filters,
        property_filters=property_filters,
        entity_filters=entity_filters,
        event_filters=event_filters,
        time_from=time_from,
        time_to=time_to,
        filter_by_keys=filter_by_keys,
        missing_edge_to=missing_edge_to,
        having=having,
        pivot_event_field=pivot_event_field,
        sample_size=sample_size,
        sample_pct=sample_pct,
        seed=seed,
        limit=limit,
        offset=offset,
        sort=sort,
        group_by_line_2=group_by_line_2,
    )

    # --- MCP-only enrichment: entity name ---
    # Add "name" property from group_by_line points table for top-N result keys.
    _top_keys = [r["key"] for r in result.get("results", []) if "key" in r]
    if _top_keys:
        _s = _state["sphere"]._sphere
        _reader = _state["session"]._reader
        _gbl_line = _s.lines.get(group_by_line)
        if _gbl_line is not None:
            from hypertopos_mcp.enrichment import build_batch_lookups

            _name_lookups = build_batch_lookups(
                _reader,
                _s,
                {group_by_line: set(_top_keys)},
            )
            _name_map = _name_lookups.get(group_by_line, {})
            for row in result.get("results", []):
                key = row.get("key")
                if key and key in _name_map:
                    name_val = _name_map[key].get("name")
                    if name_val is not None and "name" not in row:
                        row["name"] = name_val

    # --- MCP-only enrichment: label_2 for group_by_line_2 ---
    if group_by_line_2:
        _keys_2 = [r["key_2"] for r in result.get("results", []) if r.get("key_2")]
        if _keys_2:
            _s2 = _state["sphere"]._sphere
            _reader2 = _state["session"]._reader
            _gbl_line_2 = _s2.lines.get(group_by_line_2)
            if _gbl_line_2 is not None:
                from hypertopos_mcp.enrichment import build_batch_lookups

                _lookups_2 = build_batch_lookups(
                    _reader2,
                    _s2,
                    {group_by_line_2: set(_keys_2)},
                )
                _name_map_2 = _lookups_2.get(group_by_line_2, {})
                for row in result.get("results", []):
                    k2 = row.get("key_2")
                    if k2 and k2 in _name_map_2:
                        name_val_2 = _name_map_2[k2].get("name")
                        if name_val_2 is not None:
                            row["label_2"] = name_val_2

    # --- MCP-only enrichment: include_properties ---
    if include_properties and not distinct:
        s = _state["sphere"]._sphere
        reader = _state["session"]._reader
        top_keys = [r["key"] for r in result.get("results", []) if "key" in r]
        if top_keys:
            gbl_line = s.lines.get(group_by_line)
            if gbl_line is not None:
                from hypertopos_mcp.enrichment import build_batch_lookups

                lookups = build_batch_lookups(
                    reader,
                    s,
                    {group_by_line: set(top_keys)},
                )
                props_map = lookups.get(group_by_line, {})
                # Validate columns exist
                if props_map:
                    sample_props = next(iter(props_map.values()), {})
                    valid_cols = [p for p in include_properties if p in sample_props]
                    missing = [p for p in include_properties if p not in sample_props]
                    if missing:
                        raise RuntimeError(
                            f"Properties {missing} not found in line '{group_by_line}'."
                        )
                    for row in result.get("results", []):
                        key = row.get("key")
                        if key and key in props_map:
                            for col in valid_cols:
                                row[col] = props_map[key].get(col)

        n_groups = result.get("total_groups", 0)
        if n_groups * len(include_properties) > 2000:
            result["include_properties_warning"] = (
                f"include_properties with {n_groups} groups x "
                f"{len(include_properties)} columns produces a large response. "
                f"Consider omitting include_properties and using "
                f"search_entities() for top entries."
            )

    # --- MCP-only enrichment: pivot_labels ---
    if pivot_event_field and pivot_event_field.endswith("_id"):
        s = _state["sphere"]._sphere
        reader = _state["session"]._reader
        _skip = {"key", "name", "value", "count"}
        all_pivot_vals: set[str] = set()
        for row in result.get("results", []):
            all_pivot_vals |= {k for k in row if k not in _skip}
        if all_pivot_vals:
            stem = pivot_event_field[:-3]
            pl_line = s.lines.get(stem) or s.lines.get(stem + "s")
            pl_line_id = stem if s.lines.get(stem) else stem + "s"
            if pl_line is not None:
                pl_table = reader.read_points(pl_line_id, pl_line.versions[-1])
                pl_names = pl_table.schema.names
                pl_str_types = frozenset(
                    {
                        "string",
                        "utf8",
                        "large_string",
                        "large_utf8",
                    }
                )
                pl_skip = {"primary_key", "status", "version"}
                pl_str_cols = [
                    c
                    for c in pl_names
                    if c not in pl_skip and str(pl_table.schema.field(c).type) in pl_str_types
                ]
                pl_name_col = next(
                    (c for c in pl_str_cols if "name" in c),
                    pl_str_cols[0] if pl_str_cols else None,
                )
                if pl_name_col is not None:
                    pl_pk = pl_table["primary_key"].to_pylist()
                    pl_vals = pl_table[pl_name_col].to_pylist()
                    pl_map = dict(zip(pl_pk, pl_vals, strict=True))
                    pivot_labels = {
                        pv: pl_map[pv]
                        for pv in all_pivot_vals
                        if pv in pl_map and pl_map[pv] is not None
                    }
                    if pivot_labels:
                        result["pivot_labels"] = pivot_labels

    return json.dumps(result, separators=(",", ":"))
