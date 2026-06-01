# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Session tools — open/close sphere, inspect schema, search entities."""

from __future__ import annotations

import contextlib
import json
import math
import time

from hypertopos_mcp.server import (
    _call_stats,
    _do_open_sphere,
    _register_phase2_tools,
    _reload_hypertopos_modules,
    _require_sphere,
    _state,
    _unregister_phase2_tools,
    mcp,
    timed,
)


def _sanitize_for_json(obj):
    """Recursively replace non-finite floats (±inf / NaN) with None so strict
    JSON parsers accept the output. line_profile distribution stats and
    recalibrate fit outputs (mean / std / percentiles / theta) can be ±inf /
    NaN on degenerate columns. Module-local copy matching the convention in
    analysis.py / observability.py / detection.py. Navigator outputs are plain
    Python floats, so a ``float`` check suffices.
    """
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_for_json(v) for v in obj)
    return obj


def _suggest_queries(sphere) -> list[str]:
    """Generate suggested first queries from sphere structure.

    When the sphere has a chain anchor pattern (built from `chain_lines:`),
    surface the chain-coherent investigative loop entry point as one of
    the first-class suggestions — `detect_pattern` smart-mode routes the
    natural-language form to `find_chains_with_coherent_anomaly` so
    agents discover the chain-coherent loop without manually reading
    sphere_overview's chain-anchor section.
    """
    queries = []
    chain_pid: str | None = None
    entity_pid: str | None = None
    for pid, p in sphere.patterns.items():
        el = sphere.entity_line(pid)
        if p.pattern_type == "anchor" and p.relations:
            dim = p.relations[0].line_id.replace("_d_", "").replace("_", " ")
            queries.append(
                f"find {el} with anomalous {dim} in {pid}"
            )
        if p.pattern_type == "anchor":
            entity_line = el or ""
            if "chain" in entity_line.lower() or "chain" in pid.lower():
                chain_pid = pid
            elif entity_pid is None:
                entity_pid = pid
    # Chain-coherent investigative loop entry point. D1's keyword fallback
    # routes the "consecutive <entity_line>" + "anomalous" phrasing to
    # find_chains_with_coherent_anomaly with the chain pattern + entity
    # anchor pattern auto-extracted. The first non-chain anchor in
    # `sphere.patterns` iteration order becomes the entity_line reference;
    # this is intentional and depends on Python 3.7+ dict insertion order
    # being preserved.
    if chain_pid and entity_pid:
        chain_entity_line = sphere.entity_line(entity_pid) or "entities"
        queries.append(
            f"find chains where consecutive {chain_entity_line} are "
            f"individually anomalous in {chain_pid}"
        )
    if len(sphere.patterns) > 1:
        queries.append("find entities anomalous in one pattern but normal in another")
    if sphere.aliases:
        alias = next(iter(sphere.aliases))
        queries.append(f"find entities near the {alias} boundary")
    return queries[:6]


@mcp.tool()
@timed
def open_sphere(path: str, force_reload: bool = False) -> str:
    """Open a GDS sphere and create a navigator session. Use a RELATIVE path.

    force_reload: reload all Python modules before opening (dev only, not thread-safe).
    Returns: status, sphere summary, capabilities, available tools.
    """
    if force_reload:
        _reload_hypertopos_modules()
    _unregister_phase2_tools()  # stash all → Phase 1
    _do_open_sphere(path)
    _register_phase2_tools()    # restore gateway only → Phase 2
    s = _state["sphere"]._sphere
    from hypertopos_mcp.server import _sphere_capabilities as _caps

    result = {
        "status": "open",
        "path": path,
        "sphere_id": s.sphere_id,
        "name": s.name,
        "summary": {
            "lines": len(s.lines),
            "patterns": len(s.patterns),
            "aliases": len(s.aliases),
        },
        "hint": (
            "Use detect_pattern(query) to find anomalies — describe what to"
            " look for. Call sphere_overview() ONLY when you need manual"
            " tools for drill-down."
        ),
        "capabilities": _caps,
        "available_tools": [
            t.name for t in mcp._tool_manager.list_tools()
        ],
        "patterns": {
            pid: {
                "type": p.pattern_type,
                "entities": f"{s.line_row_count(s.entity_line(pid)):,}"
                if hasattr(s, "line_row_count")
                else "?",
                "dimensions": [
                    r.line_id.replace("_d_", "").replace("_", " ")
                    for r in p.relations
                ],
            }
            for pid, p in s.patterns.items()
        },
        "suggested_queries": _suggest_queries(s),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
@timed
def close_sphere() -> str:
    """Close the current session and release resources. Returns session_stats."""
    if _state["session"] is None:
        return json.dumps({"status": "no_session"})

    stats = _build_session_stats()

    with contextlib.suppress(Exception):
        _state["session"].close(purge_temporal=True)
    for key in list(_state.keys()):
        _state[key] = None
    _unregister_phase2_tools()
    return json.dumps({"status": "closed", "session_stats": stats})


def _build_session_stats() -> dict:
    """Build session stats dict from _call_stats.

    When a session is open, also surfaces the reader's points-handle cache
    counters (handle reuse vs fresh open) so an agent can see whether warm
    reads are reusing the open Lance dataset handle. These are
    hypertopos-owned counters, not a Lance-provided metric.
    """
    wall_ms = None
    if _call_stats["session_start"] is not None:
        wall_ms = round((time.perf_counter() - _call_stats["session_start"]) * 1000, 1)
    stats = {
        "total_tool_calls": _call_stats["call_count"],
        "total_elapsed_ms": round(_call_stats["total_elapsed_ms"], 1),
        "wall_clock_ms": wall_ms,
        "per_tool": _call_stats["per_tool"],
    }
    session = _state.get("session")
    reader = getattr(session, "_reader", None) if session is not None else None
    cache_stats = getattr(reader, "points_cache_stats", None)
    if callable(cache_stats):
        with contextlib.suppress(Exception):
            stats["points_handle_cache"] = cache_stats()
    return stats


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_session_stats() -> str:
    """Return current session tool call statistics without closing the session.

    Returns: total_tool_calls, total_elapsed_ms, wall_clock_ms, and a per_tool
    breakdown of call counts and elapsed time.
    """
    stats = _build_session_stats()
    return json.dumps(stats, indent=2)


def _get_line_row_count(reader: object, line_id: str, version: int) -> int | None:
    """Return row count for a line's points table, or None on failure."""
    try:
        return reader.read_points(line_id, version, columns=["primary_key"]).num_rows  # type: ignore[union-attr]
    except Exception:
        return None


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_sphere_info() -> str:
    """Return sphere schema: lines, patterns, aliases, active manifest.

    Returns: per-line columns/has_fts_index, per-pattern relations,
    per-alias has_cutting_plane, plus top-level ``label_aware_available``
    (True when the sphere carries a ``label_audit`` block).
    """
    _require_sphere()
    s = _state["sphere"]._sphere
    session = _state.get("session")
    nav = _state.get("navigator")
    reader = session._reader if session is not None else None
    result = {
        "sphere_id": s.sphere_id,
        "name": s.name,
        "path": _state["path"],
        "label_aware_available": s.label_audit is not None,
        "lines": {
            lid: {
                "role": line.line_role,
                "versions": line.versions,
                "pattern_id": line.pattern_id,
                **({} if line.description is None else {"description": line.description}),
                **(
                    {"columns": [{"name": c.name, "type": c.type} for c in line.columns]}
                    if line.columns is not None
                    else {}
                ),
                "has_fts_index": (
                    reader.has_fts_index(lid, line.versions[-1]) if reader is not None else False
                ),
                "total_rows": (
                    _get_line_row_count(reader, lid, line.versions[-1])
                    if reader is not None
                    else None
                ),
            }
            for lid, line in s.lines.items()
        },
        "patterns": {
            pid: {
                "type": pat.pattern_type,
                "delta_dim": pat.delta_dim(),
                "population_size": pat.population_size,
                **({} if pat.description is None else {"description": pat.description}),
                **(
                    {}
                    if pat.last_calibrated_at is None
                    else {"last_calibrated_at": pat.last_calibrated_at.isoformat()}
                ),
                "relations": [
                    {
                        "line_id": r.line_id,
                        "direction": r.direction,
                        "required": r.required,
                        **({} if r.display_name is None else {"display_name": r.display_name}),
                        **(
                            {} if r.interpretation is None else {"interpretation": r.interpretation}
                        ),
                    }
                    for r in pat.relations
                ],
                **({"prop_columns": pat.prop_columns} if pat.prop_columns else {}),
            }
            for pid, pat in s.patterns.items()
        },
        "aliases": {
            aid: {
                "base_pattern_id": alias.base_pattern_id,
                "status": alias.status,
                "has_cutting_plane": alias.filter.cutting_plane is not None,
                **(
                    {"include_relations": alias.filter.include_relations}
                    if alias.filter.include_relations
                    else {}
                ),
                **(
                    {"population_inside": nav.alias_population_count(aid)}
                    if (
                        alias.filter.cutting_plane
                        and nav is not None
                        and alias.base_pattern_id in s.patterns
                    )
                    else {}
                ),
            }
            for aid, alias in s.aliases.items()
        },
    }
    manifest = _state.get("manifest")
    if manifest is not None:
        result["active_manifest"] = {
            "manifest_id": manifest.manifest_id,
            "snapshot_time": manifest.snapshot_time.isoformat(),
            "line_versions": manifest.line_versions,
        }
    else:
        result["active_manifest"] = {"version": "latest"}
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_line_schema(line_id: str) -> str:
    """Return column names and types for a line's points table.

    Returns: columns list with name/type, total_rows.
    Usually unnecessary — get_sphere_info includes columns.
    """
    _require_sphere()
    s = _state["sphere"]._sphere
    line = s.lines.get(line_id)
    if line is None:
        raise RuntimeError(f"Line '{line_id}' not found. Available: {list(s.lines.keys())}")

    version = line.versions[-1]
    reader = _state["session"]._reader
    if line.columns is not None:
        columns = [{"name": c.name, "type": c.type} for c in line.columns]
    else:
        table = reader.read_points(line_id, version)
        columns = [{"name": field.name, "type": str(field.type)} for field in table.schema]
    total_rows = None
    with contextlib.suppress(Exception):
        total_rows = reader.read_points(line_id, version, columns=["primary_key"]).num_rows
    result = {
        "line_id": line_id,
        "version": version,
        "total_rows": total_rows,
        "columns": columns,
    }
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def search_entities(line_id: str, property_name: str, value: str, limit: int = 20) -> str:
    """Search for entities in a line by exact property value match (case-sensitive).

    property_name: column to filter on (use get_line_schema to discover columns).
    value: exact match value. Bool columns accept "true"/"false".
    Returns: total matches, returned count, and entity list with properties.
    """
    _require_sphere()
    from hypertopos_mcp.server import _require_navigator

    _require_navigator()
    nav = _state["navigator"]
    core_result = nav.search_entities(line_id, property_name, value, limit)
    result = {
        "line_id": line_id,
        "property_name": property_name,
        "value": value,
        **core_result,
    }
    if core_result.get("total", 0) == 0:
        result["hint"] = (
            f"No exact match for {property_name}={value!r} (this match is "
            "case-sensitive). Try search_entities_fts(line_id, query) for a "
            "token/substring match, or get_line_schema(line_id) to confirm the "
            "column name and inspect sample values."
        )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def search_entities_fts(line_id: str, query: str, limit: int = 20) -> str:
    """Full-text search across all string properties of a line (BM25 ranked).

    Requires INVERTED index (check has_fts_index in get_sphere_info).
    query: case-insensitive token match across all string columns.
    Returns: ranked entity list. For exact-value match use search_entities instead.
    """
    _require_sphere()
    s = _state["sphere"]._sphere
    line = s.lines.get(line_id)
    if line is None:
        raise RuntimeError(f"Line '{line_id}' not found. Available: {list(s.lines.keys())}")

    if not line.has_fts():
        raise RuntimeError(
            f"No FTS index on line '{line_id}' (fts_columns={line.fts_columns!r}). "
            "Use search_entities for exact match or rebuild with fts_columns parameter."
        )

    version = line.versions[-1]
    storage = _state["session"]._reader

    try:
        table = storage.search_points_fts(line_id, version, query, limit=limit)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    if "_score" in table.schema.names:
        table = table.drop("_score")

    entities = []
    for row in table.to_pylist():
        entities.append(
            {
                "primary_key": row["primary_key"],
                "status": row.get("status", "unknown"),
                "properties": {
                    k: v if isinstance(v, (bool, int, float, str)) else str(v)
                    for k, v in row.items()
                    if k != "primary_key" and k != "status" and v is not None
                },
            }
        )

    result = {
        "line_id": line_id,
        "query": query,
        "returned": len(entities),
        "entities": entities,
    }
    if len(entities) == 0:
        result["hint"] = (
            "No FTS matches. For short codes or IDs (e.g. PM-001, DT-002), "
            "use goto(key, line_id) to look up a known key, or walk_line(line_id) "
            "to enumerate all entities in a small lookup table."
        )
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_line_profile(
    line_id: str,
    property_name: str,
    group_by: str | None = None,
    limit: int = 50,
) -> str:
    """Profile a property column: value distribution, stats, or range.

    group_by: optional column for per-group numeric stats.
    Returns: auto-detected stats based on column type.
    """
    _require_sphere()
    from hypertopos_mcp.server import _require_navigator

    _require_navigator()
    nav = _state["navigator"]
    core_result = nav.line_profile(line_id, property_name, group_by=group_by, limit=limit)
    result = {
        "line_id": line_id,
        "property": property_name,
        **core_result,
    }
    return json.dumps(_sanitize_for_json(result), indent=2, default=str)


@mcp.tool()
@timed
def recalibrate(
    pattern_id: str,
    soft_threshold: float | None = None,
    hard_threshold: float | None = None,
) -> str:
    """Recalibrate mu/sigma/theta for a pattern — full recompute of population statistics.

    soft_threshold/hard_threshold: optionally update drift thresholds (0.0-1.0).
    Returns: recalibration result with updated statistics.
    """
    _require_sphere()
    result = _state["session"].recalibrate(
        pattern_id,
        soft_threshold=soft_threshold,
        hard_threshold=hard_threshold,
    )
    return json.dumps(_sanitize_for_json(result), indent=2)
