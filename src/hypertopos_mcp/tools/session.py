# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Session tools — open/close sphere, inspect schema, search entities."""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import TYPE_CHECKING, Any, Literal

from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

from hypertopos_mcp._format import json_default, sanitize_for_json, tabulate_properties
from hypertopos_mcp.server import (
    RESPONSE_FORMATS,
    _call_stats,
    _do_open_sphere,
    _refresh_state,
    _register_phase2_tools,
    _reload_hypertopos_modules,
    _require_sphere,
    _state,
    _unregister_phase2_tools,
    active_response_format,
    current_session,
    forget_session,
    live_session_keys,
    mcp,
    sphere_capabilities,
    timed,
    visible_tools,
)

if TYPE_CHECKING:
    from hypertopos.model.sphere import Sphere
    from hypertopos.storage.reader import GDSReader


def _suggest_queries(sphere: Sphere) -> list[str]:
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
            queries.append(f"find {el} with anomalous {dim} in {pid}")
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


def _pattern_summary(sphere: Any) -> dict[str, dict[str, Any]]:
    """Per-pattern type, entity count, and relation and dimension-block labels."""
    from hypertopos.model.sphere import relation_dim_labels

    def readable(label: str) -> str:
        # "_d_x" (derived / precomputed) and "t:x" / "g:x" / "s:x" (blocks) -> "x"
        return label.replace("_d_", "").split(":", 1)[-1].replace("_", " ")

    return {
        pid: {
            "type": p.pattern_type,
            "entities": f"{p.population_size:,}",
            "dimensions": [
                readable(label) for label in [*relation_dim_labels(p.relations), *p.dim_block_names]
            ],
        }
        for pid, p in sphere.patterns.items()
    }


@mcp.tool()
@timed
def open_sphere(
    path: str | None = None,
    force_reload: bool = False,
    branch: str | None = None,
    response_format: Literal["compact", "full"] | None = None,
    ctx: Context | None = None,
) -> str:
    """Open a GDS sphere and create a navigator session. Use a RELATIVE path.

    path: the sphere directory; omitted, the server's HYPERTOPOS_SPHERE_PATH.

    branch: open a sphere branch instead of main (see manage_branch); every
      tool then reads the branch.
    response_format: "compact" (default) omits positional delta vectors and numeric
      properties columns and lists what it left out in `omitted`; "full" returns
      everything. Per-call summary=/verbose= still win.
    force_reload: reload all Python modules before opening (dev only, not thread-safe).
    Returns: status, ref (main or the branch), response_format, sphere summary,
    capabilities, available tools.
    """
    if path is None:
        path = os.environ.get("HYPERTOPOS_SPHERE_PATH")
        if not path:
            raise ValueError(
                "open_sphere needs a path: pass path=, or start the server with "
                "HYPERTOPOS_SPHERE_PATH set to the sphere directory."
            )
    if response_format is not None and response_format not in RESPONSE_FORMATS:
        raise ValueError(
            f"response_format must be one of {list(RESPONSE_FORMATS)}, got {response_format!r}"
        )
    if force_reload:
        others = [k for k in live_session_keys() if k != current_session().key]
        if others:
            raise RuntimeError(
                "force_reload reloads the modules every session shares; refused while "
                f"other client sessions have a sphere open: {sorted(others)}"
            )
        _reload_hypertopos_modules()
    _do_open_sphere(path, branch=branch)  # on failure the previous session stays as it was
    _unregister_phase2_tools(ctx)  # stash all → Phase 1
    _state["response_format"] = response_format
    _register_phase2_tools(ctx)  # restore gateway only → Phase 2
    s = _state["sphere"]._sphere
    _caps = sphere_capabilities()

    result = {
        "status": "open",
        "path": path,
        "ref": branch or "main",
        "response_format": active_response_format(),
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
        "available_tools": sorted(visible_tools()),
        "patterns": _pattern_summary(s),
        "suggested_queries": _suggest_queries(s),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
@timed
def close_sphere(ctx: Context | None = None) -> str:
    """Close the current session and release resources. Returns session_stats."""
    if _state["session"] is None:
        return json.dumps({"status": "no_session"})

    stats = _build_session_stats()

    with contextlib.suppress(Exception):
        _state["session"].close(purge_temporal=True)
    for key in list(_state.keys()):
        _state[key] = None
    forget_session(current_session().key)
    _unregister_phase2_tools(ctx)
    return json.dumps({"status": "closed", "session_stats": stats})


def _build_session_stats() -> dict[str, Any]:
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def get_session_stats() -> str:
    """Return current session tool call statistics without closing the session.

    Returns: total_tool_calls, total_elapsed_ms, wall_clock_ms, a per_tool
    breakdown of call counts and elapsed time, and (with an open sphere) a
    snapshot block: stale is true when the sphere changed after the session
    opened (another process ingested or recalibrated); open_sphere(path) reads it.
    """
    stats = _build_session_stats()
    stats["response_format"] = active_response_format()
    stats["session_key"] = current_session().key
    stats["sessions_live"] = len(live_session_keys())
    if _state["session"] is not None:
        status = _state["session"].is_stale()
        stats["snapshot"] = {
            "pinned_at": status.pinned_at.isoformat(),
            "stale": status.stale,
            "metadata_changed": status.metadata_changed,
            "pinned_versions": status.pinned_versions,
            "latest_versions": status.latest_versions,
            "hint": (
                "A newer sphere state exists; call open_sphere(path) to read it."
                if status.stale
                else None
            ),
        }
    return json.dumps(stats, indent=2)


def _get_line_row_count(reader: GDSReader, line_id: str, version: int) -> int | None:
    """Return row count for a line's points table, or None on failure."""
    try:
        reader.read_points_schema(line_id, version)  # raises when the line has no points
        return reader.count_points_rows(line_id, version)
    except Exception:
        return None


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def get_sphere_info() -> str:
    """Return sphere schema: lines, patterns, aliases, active manifest.

    Returns: per-line columns/has_fts_index, per-pattern relations and
    anomaly_percentile (plus score_dims / score_axes on patterns with the
    calibrated score, whose is_anomaly is conformal_p <= 1 - anomaly_percentile/100),
    per-alias has_cutting_plane, plus top-level ``label_aware_available``
    (True when the sphere carries a ``label_audit`` block). Builder-derived
    dimension lines (``_d_*``, key column only) are listed by name in
    ``derived_dimension_lines`` instead of one entry each.
    """
    _require_sphere()
    s = _state["sphere"]._sphere
    derived = [
        lid
        for lid, line in s.lines.items()
        if lid.startswith("_d_")
        and not line.pattern_id
        and {c.name for c in line.columns or []} <= {"primary_key"}
    ]
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
                    {"columns": {c.name: c.type for c in line.columns}}
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
            if lid not in derived
        },
        **({"derived_dimension_lines": derived} if derived else {}),
        "patterns": {
            pid: {
                "type": pat.pattern_type,
                "delta_dim": pat.delta_dim(),
                "population_size": pat.population_size,
                "anomaly_percentile": pat.anomaly_percentile,
                **(
                    {"score_dims": list(pat.score_dims), "score_axes": list(pat.score_axes or ())}
                    if pat.score_dims
                    else {}
                ),
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
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
        schema = reader.read_points_schema(line_id, version)
        columns = [{"name": field.name, "type": str(field.type)} for field in schema]
    total_rows = None
    with contextlib.suppress(Exception):
        total_rows = reader.count_points_rows(line_id, version)
    result = {
        "line_id": line_id,
        "version": version,
        "total_rows": total_rows,
        "columns": columns,
    }
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def search_entities(line_id: str, property_name: str, value: str, top_n: int = 20) -> str:
    """Search for entities in a line by exact property value match (case-sensitive).

    property_name: column to filter on (use get_line_schema to discover columns).
    value: exact match value. Bool columns accept "true"/"false".
    Returns: total matches, returned count, entities (primary_key, status) and their
    properties in one table properties = {columns: [primary_key, ...], rows}.
    """
    _require_sphere()
    from hypertopos_mcp.server import _require_navigator

    _require_navigator()
    nav = _state["navigator"]
    core_result = nav.search_entities(line_id, property_name, value, top_n)
    result = {
        "line_id": line_id,
        "property_name": property_name,
        "value": value,
        **core_result,
    }
    entities = [dict(e) for e in core_result.get("entities") or []]
    result["entities"] = entities
    table = tabulate_properties(entities, "primary_key")
    if table:
        result["properties"] = table
    if core_result.get("total", 0) == 0:
        result["hint"] = (
            f"No exact match for {property_name}={value!r} (this match is "
            "case-sensitive). Try search_entities_fts(line_id, query) for a "
            "token/substring match, or get_line_schema(line_id) to confirm the "
            "column name and inspect sample values."
        )
    return json.dumps(result, indent=2, default=json_default)


# Key / status go on the entity itself; version and change timestamps are storage
# bookkeeping that search_entities does not return either.
_FTS_SKIP_COLUMNS = frozenset({"primary_key", "status", "version", "created_at", "changed_at"})


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def search_entities_fts(line_id: str, query: str, top_n: int = 20) -> str:
    """Full-text search across all string properties of a line (BM25 ranked).

    Requires INVERTED index (check has_fts_index in get_sphere_info).
    query: case-insensitive token match across all string columns.
    Returns: ranked entities (primary_key, status) and their properties in one table
    properties = {columns: [primary_key, ...], rows}. For exact-value match use
    search_entities instead.
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
        table = storage.search_points_fts(line_id, version, query, limit=top_n)
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
                    if k not in _FTS_SKIP_COLUMNS and v is not None
                },
            }
        )

    result: dict[str, Any] = {
        "line_id": line_id,
        "query": query,
        "returned": len(entities),
        "entities": entities,
    }
    table = tabulate_properties(entities, "primary_key")
    if table:
        result["properties"] = table
    if len(entities) == 0:
        result["hint"] = (
            "No FTS matches. For short codes or IDs (e.g. PM-001, DT-002), "
            "use goto(key, line_id) to look up a known key, or walk_line(line_id) "
            "to enumerate all entities in a small lookup table."
        )
    return json.dumps(result, indent=2)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def get_line_profile(
    line_id: str,
    property_name: str,
    group_by: str | None = None,
    top_n: int = 50,
) -> str:
    """Profile a property column: value distribution, stats, or range.

    group_by: optional column for per-group numeric stats.
    Returns: auto-detected stats based on column type.
    """
    _require_sphere()
    from hypertopos_mcp.server import _require_navigator

    _require_navigator()
    nav = _state["navigator"]
    core_result = nav.line_profile(line_id, property_name, group_by=group_by, limit=top_n)
    result = {
        "line_id": line_id,
        "property": property_name,
        **core_result,
    }
    return json.dumps(sanitize_for_json(result), indent=2, default=json_default)


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
@timed
def recalibrate(
    pattern_id: str,
    soft_threshold: float | None = None,
    hard_threshold: float | None = None,
    branch: str | None = None,
    exclude_keys: list[str] | None = None,
) -> str:
    """Recalibrate mu/sigma/theta for a pattern — full recompute of population statistics.

    soft_threshold/hard_threshold: optionally update drift thresholds (0.0-1.0).
    exclude_keys: fit without these entities (every entity is still scored),
      e.g. the hidden influencers from find_calibration_influencers.
    branch: run on that branch (created when absent) and leave main untouched —
      the safe what-if; inspect with compare_branches. On a session opened on a
      branch, writes go to that branch.
    Patterns with group_by_property are recalibrated per group, as a build does
      (and each group's score reference with score_per_group). Patterns with
      gmm_n_components are refused: rebuild the sphere instead.
    Returns: recalibration result with updated statistics, n_excluded, branch.
    """
    _require_sphere()
    result = _state["session"].recalibrate(
        pattern_id,
        soft_threshold=soft_threshold,
        hard_threshold=hard_threshold,
        branch=branch,
        exclude_keys=exclude_keys,
    )
    _refresh_state()
    return json.dumps(sanitize_for_json(result), indent=2)
