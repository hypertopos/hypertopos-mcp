# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Navigation tools — goto, walk, jump, dive, emerge, anomalies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp.types import ToolAnnotations

from hypertopos_mcp._format import (
    compact_solid,
    drop_positional_deltas,
    json_default,
    project_descriptive,
    sanitize_for_json,
    slim_polygons,
    tabulate_edge_properties,
    tabulate_properties,
)
from hypertopos_mcp.enrichment import (
    enrich_polygons,
    lookup_entity,
    resolve_entity_line_id,
)
from hypertopos_mcp.serializers import _serialize_polygon, _serialize_position, _serialize_solid
from hypertopos_mcp.server import _require_navigator, _state, active_response_format, mcp, timed
from hypertopos_mcp.tools._guards import adaptive_polygon_cap, binary_geometry_note_for_pattern

_STALE_SOLID_DAYS = 180


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def goto(primary_key: str, line_id: str) -> str:
    """Navigate to a specific entity by business key and line. Sets current position.

    Returns: the new navigator position (a serialised Point with line_id,
    primary_key, and entity properties).
    """
    _require_navigator()
    nav = _state["navigator"]
    nav.goto(primary_key, line_id)
    return json.dumps(sanitize_for_json(_serialize_position(nav.position)), indent=2)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def get_position() -> str:
    """Return the current navigator position (Point, Polygon, Solid, or None).

    Returns: the serialised current position object, or a null position when
    the navigator has not been moved.
    """
    _require_navigator()
    return json.dumps(
        sanitize_for_json(_serialize_position(_state["navigator"].position)), indent=2
    )


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def walk_line(line_id: str, direction: str = "+") -> str:
    """Walk one step along a line to the next (+) or previous (-) entity.

    direction: "+" (next) or "-" (previous). Requires position on the given line.
    Returns: new position. For bulk traversal, prefer search_entities or aggregate.
    """
    _require_navigator()
    nav = _state["navigator"]
    from hypertopos.model.objects import Point

    if nav.position is not None and not isinstance(nav.position, Point):
        raise RuntimeError(
            f"walk_line needs a Point position; the current position is "
            f"{type(nav.position).__name__}. Call goto(primary_key, line_id) first."
        )
    walk = nav.π1_walk_line
    walk(line_id, direction)
    return json.dumps(sanitize_for_json(_serialize_position(nav.position)), indent=2)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def jump_polygon(target_line_id: str, edge_index: int = 0) -> str:
    """Jump through the current polygon to a point on another line via an alive edge.

    edge_index: which edge to follow when multiple exist (default 0). Check total_edges_to_target in response.
    Returns: new position on target line.
    """  # noqa: E501
    _require_navigator()
    nav = _state["navigator"]
    pos = nav.position
    from hypertopos.model.objects import Point

    if not isinstance(pos, Point):
        raise RuntimeError(
            f"Current position is {type(pos).__name__}, need Point. Use goto() first."
        )  # noqa: E501

    s = _state["sphere"]._sphere
    line = s.lines.get(pos.line_id)
    if line is None:
        raise RuntimeError(f"Line '{pos.line_id}' not found in sphere.")
    pattern_id = line.pattern_id

    polygon = nav.current_polygon(pattern_id)
    alive_count = polygon.count_alive_edges_to(target_line_id)
    jump = nav.π2_jump_polygon
    try:
        jump(polygon, target_line_id, edge_index=edge_index)
    except ValueError as exc:
        error_msg = str(exc)
        resp: dict[str, Any] = {
            "error": error_msg,
            "pattern_id": pattern_id,
            "target_line_id": target_line_id,
        }
        if "continuous mode" in error_msg:
            resp["continuous_mode"] = True
            _sphere = _state["sphere"]._sphere
            entity_line_id = _sphere.event_line(pattern_id) or _sphere.entity_line(pattern_id)
            resp["hint"] = (
                f"This edge cannot be followed: the '{target_line_id}' relation uses "
                f"continuous mode (edge_max) — counts are stored, not entity keys. "
                f"Use get_centroid_map(group_by_property=...) to group entities by "
                f"a property, or aggregate() to count relations."
            )
            if entity_line_id:
                props = nav.suggest_grouping_properties(pattern_id)
                if not props:
                    _line = _sphere.lines.get(entity_line_id)
                    _skip = {"primary_key", "version", "status", "created_at", "changed_at"}
                    if _line and _line.columns:
                        props = [
                            c.name
                            for c in _line.columns
                            if c.type == "string" and c.name not in _skip
                        ]
                if props:
                    resp["suggested_call"] = (
                        f'get_centroid_map(pattern_id="{pattern_id}", '
                        f'group_by_line="{entity_line_id}", '
                        f'group_by_property="{entity_line_id}:{props[0]}")'
                    )
                    resp["available_properties"] = [f"{entity_line_id}:{p}" for p in props]
        return json.dumps(resp, indent=2)
    result = _serialize_position(nav.position)
    result["total_edges_to_target"] = alive_count
    return json.dumps(sanitize_for_json(result), indent=2)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def dive_solid(
    primary_key: str,
    pattern_id: str,
    timestamp: str | None = None,
    counterfactual_frozen_population: bool = False,
) -> str:
    """Dive into the temporal history of an entity as a Solid.

    timestamp: optional ISO-8601 cutoff — only slices at or before this time.
    counterfactual_frozen_population: when True, every returned slice gains an
        additional ``delta_norm_frozen_pop`` field — the per-slice L2 norm
        recomputed against the FIRST slice's raw shape as the entity-relative
        reference. Answers "is this entity moving, or is the population
        drifting around a stationary entity?" — a stationary entity yields
        ``delta_norm_frozen_pop = 0`` across all slices. Default ``False``
        keeps the existing response shape.
    Returns: base polygon (the entity's current state, as get_polygon), temporal
    slices (its recorded history, oldest first), reputation, and forecast (if >=3
    slices). When the slices carry fewer dims than the pattern, the forecast adds
    forecast_dims and forecast_theta_norm: the prediction is compared with the
    threshold over those same dims. Compact format: as get_solid. After this call
    the position is the Solid; goto(primary_key, line_id) returns to a Point.
    """
    _require_navigator()
    nav = _state["navigator"]
    ts: datetime | None = None
    if timestamp is not None:
        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    dive = nav.π3_dive_solid
    dive(
        primary_key,
        pattern_id,
        timestamp=ts,
        counterfactual_frozen_population=counterfactual_frozen_population,
    )
    solid = nav.position
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns[pattern_id]
    ss = _serialize_solid(solid, pattern=pattern)
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    base = ss.get("base_polygon", {})
    enrich_polygons([base], reader, sphere, entity_line_id)
    ss["theta_norm"] = round(pattern.theta_norm, 4)
    # Add base_polygon_note when temporal history exists
    if solid.slices:
        ss["base_polygon_note"] = (
            "base_polygon is the entity's current state (same as "
            "get_polygon(pattern_id=...)); slices are its recorded history, oldest first."
        )
    # Add forecast when solid has enough slices
    if len(solid.slices) >= 3:
        from hypertopos.engine.topology import local_trajectory_shape

        delta_norms = [float(sl.delta_norm_snapshot) for sl in solid.slices]
        shape = local_trajectory_shape(delta_norms)
        if shape is not None:
            ss["trajectory_shape"] = shape

        forecast_dict = nav.solid_forecast(
            primary_key,
            pattern_id,
            current_delta_norm=float(solid.base_polygon.delta_norm),
        )
        if forecast_dict is not None:
            if forecast_dict.get("stale_warning"):
                ss["stale_forecast_warning"] = forecast_dict.pop("stale_warning")
            ss["forecast"] = forecast_dict
    # Add reputation when temporal history exists
    rep = nav.solid_reputation(primary_key, pattern_id)
    if rep is not None:
        ss["reputation"] = rep
    if active_response_format() == "compact":
        omitted = compact_solid(ss, pattern.dim_labels)
        if omitted:
            ss["omitted"] = {
                **omitted,
                "full_response": "open_sphere(<path>, response_format='full') returns every field",
            }
    return json.dumps(sanitize_for_json(ss), separators=(",", ":"), default=json_default)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def emerge() -> str:
    """Emerge from a Polygon or Solid back to a synthetic Point.

    Returns: synthetic Point (line_id="emerged") with entity_properties from points table.
    To continue navigating, call goto(primary_key, original_line_id).
    """
    _require_navigator()
    nav = _state["navigator"]

    pre_position = nav.position
    em = nav.π4_emerge
    em()

    result = _serialize_position(nav.position)

    entity_properties = None
    if pre_position is not None and hasattr(pre_position, "pattern_id"):
        sphere = _state["sphere"]._sphere
        entity_line_id = resolve_entity_line_id(sphere, pre_position.pattern_id)
        if entity_line_id is not None:
            reader = _state["session"]._reader
            entity_properties = lookup_entity(
                reader,
                sphere,
                entity_line_id,
                pre_position.primary_key,
            )

    result["entity_properties"] = entity_properties
    return json.dumps(sanitize_for_json(result), indent=2, default=json_default)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def find_anomalies(
    pattern_id: str,
    radius: float | None = None,
    top_n: int = 10,
    include_emerging: bool = False,
    offset: int = 0,
    missing_edge_to: str | None = None,
    rank_by_property: str | None = None,
    property_filters: dict[Any, Any] | None = None,
    fdr_alpha: float | None = None,
    fdr_method: str | None = None,
    fdr_resolution: str | None = None,
    fdr_temporal_resolution: str | None = None,
    rank_by: str | None = None,
    select: str = "top_norm",
    metric: str = "L2",
    min_confidence: float = 0.0,
    dimension_weights: dict[Any, Any] | None = None,
    sample_size: int | None = None,
    boundary_aware: bool = False,
    summary: bool | None = None,
    rank_order: str = "desc",
) -> str:
    """Find the most anomalous polygons in a pattern, ranked by the pattern's own score.

    Calibrated patterns (non-empty score_dims in get_sphere_info): the selection is the stored is_anomaly (conformal_p <= 1 - anomaly_percentile/100, the calibrated alarm budget) and the order is conformal_p ascending, then anomaly_score descending (the same order within one calibration; rows added by incremental ingest are scored without the event axis, so only their p-values compare with built rows). ranked_by reports "anomaly_score" for this order. Each polygon carries anomaly_score, conformal_p and score_rank_pct (rank of anomaly_score in the population, 0-100). theta is not the boundary there, so radius, metric="Linf", dimension_weights, rank_by="delta_norm" / "signed_confidence", boundary_aware and min_confidence > 0 raise. Other patterns (event, GMM, discrete) rank by delta_norm against theta and accept every option.

    radius: multiplier on the theta_norm threshold (default None = 1). Higher = looser boundary. Uncalibrated patterns only.
    metric: "L2" (default, pre-computed delta_norm) or "Linf" (max single-dimension |delta|; uncalibrated patterns only).
    include_emerging: append entities trending toward anomaly (requires temporal data). On calibrated patterns emerging is empty with emerging_note: the forecast tracks delta_norm against theta, which does not decide is_anomaly there.
    missing_edge_to: keep only anomalous entities with NO edge to this line.
    rank_by_property: re-rank by a raw entity property instead of the score.
    rank_order: "desc" (default) or "asc" for rank_by_property — "asc" lists the lowest values first; missing values rank last.
    property_filters: filter by entity properties before ranking ({"col": {"gt": X}}, {"col": {"in": ["A", "B"]}}).
    fdr_alpha: level of the cell gate; requires fdr_resolution or fdr_temporal_resolution. There is no entity-level FDR: is_anomaly already is the calibrated alarm budget.
    fdr_method: "storey" (default, LSL null-proportion estimator) or "bh" (Benjamini-Hochberg, pi0=1) for the cell gate.
    fdr_resolution: spatial-hierarchy level name declared on the pattern's fdr_hierarchy. Survivors are gated to those whose cell at this level is enriched in is_anomaly (hypergeometric upper tail per cell, BH/Storey over cells). Requires fdr_alpha. Survivors gain cell_q_spatial and cell_path. Use to localise anomalies to a geographic / segment region instead of a flat entity ranking.
    fdr_temporal_resolution: temporal-hierarchy level name declared on the pattern's fdr_temporal_hierarchy (e.g. hour / day / week). Same semantics on the temporal axis. Requires fdr_alpha. Survivors gain cell_q_temporal and cell_path. When both are set, an entity survives iff its cell clears every named spatial AND temporal level; cell_q_spatial and cell_q_temporal then carry the conservative joint q.
    rank_by: None (default, the pattern's own score: conformal_p then anomaly_score on calibrated patterns, delta_norm otherwise), "delta_norm", or "signed_confidence" (delta_norm_signed × |lda_alignment| × (1 - reliability_penalty) descending, onto the Fisher LDA axis; requires a pattern rebuilt with sphere.yaml label_audit: enabled; incompatible with select="diverse"). Both explicit values: uncalibrated patterns only.
    select: "top_norm" (default, rank by score) or "diverse" (submodular facility location — K most diverse representatives with representativeness counts).
    min_confidence: filter by bootstrap confidence threshold (0.0 = no filter). Requires bregman_calibration=True on the sphere; uncalibrated patterns only.
    dimension_weights: optional {dim_name: float} multipliers on each dim's contribution to delta_norm before ranking (missing dims 1.0, 0.0 silences a dim, {} = None). Uncalibrated patterns only. Only `delta_norm` on the returned polygons is the weighted score; `delta`, `is_anomaly`, `bregman_divergence` and `score_rank_pct` come from storage.
    sample_size: optional cap on geometry rows scanned. When set below the population size, a random sample is drawn before threshold filtering and ranking. Forces the in-process scan path. Default None = scan full population.
    boundary_aware: when True (default False), half the sample_size budget goes to entities within [0.8 * theta_norm, 1.2 * theta_norm], half to the rest. Requires sample_size; uncalibrated patterns only.
    summary: None (default) follows the session response_format — compact drops positional delta vectors (anomaly_dimensions and cluster labels stay) and numeric properties columns, listed in `omitted`; full keeps everything plus top-level dim_labels. False forces full. True drops delta, edges and properties entirely (smallest payload); retains primary_key, delta_norm, anomaly_score, conformal_p, is_anomaly, score_rank_pct, bregman_divergence, cell gate fields, reliability_flags, and anomaly_dimensions. Call get_polygon for full per-polygon detail.
    The edges list (when present) is filtered to actual jumpable entity edges; degenerate dim-line edges with empty point_key are omitted for response size — call get_polygon to retrieve the full edge list including dim lines.
    Returns: anomalous polygons with anomaly_dimensions, clusters, total_found, found (polygons in this page), ranked_by.
    """  # noqa: E501
    _require_navigator()
    nav = _state["navigator"]
    sphere = _state["sphere"]._sphere
    reader = _state["session"]._reader
    pattern = sphere.patterns[pattern_id]
    if missing_edge_to:
        if pattern.pattern_type == "event":
            raise RuntimeError(
                "missing_edge_to is not supported for event patterns — "
                "use missing_edge_to at the aggregate level instead"
            )
        if missing_edge_to not in sphere.lines:
            raise RuntimeError(
                f"Unknown line '{missing_edge_to}' in missing_edge_to. "
                f"Available: {sorted(sphere.lines)}"
            )
    _entity_line_id = sphere.entity_line(pattern_id) or sphere.event_line(pattern_id)
    _entity_line = sphere.lines.get(_entity_line_id) if _entity_line_id else None
    _n_entity_props = len(_entity_line.columns) if (_entity_line and _entity_line.columns) else None
    cap = adaptive_polygon_cap(pattern, n_entity_props=_n_entity_props)
    capped_warning: str | None = None
    if top_n > cap:
        capped_warning = (
            f"top_n={top_n} exceeds adaptive hard cap {cap} (based on {len(pattern.relations)} "
            f"edges/polygon) — truncated to keep response under ~50K chars. "
            f"For anomaly counts use anomaly_summary()."
        )
        top_n = cap
    # Delegate to navigator — handles subprocess fast path and emerging internally
    attract = nav.π5_attract_anomaly
    polygons, total_found, emerging, pi5_meta = attract(
        pattern_id,
        radius=radius,
        top_n=top_n,
        offset=offset,
        missing_edge_to=missing_edge_to,
        include_emerging=include_emerging,
        rank_by_property=rank_by_property,
        rank_order=rank_order,
        property_filters=property_filters,
        fdr_alpha=fdr_alpha,
        fdr_method=fdr_method,
        fdr_resolution=fdr_resolution,
        fdr_temporal_resolution=fdr_temporal_resolution,
        rank_by=rank_by,
        select=select,
        metric=metric,
        min_confidence=min_confidence,
        dimension_weights=dimension_weights,
        sample_size=sample_size,
        boundary_aware=boundary_aware,
    )

    serialized = [_serialize_polygon(p) for p in polygons]
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    enriched = enrich_polygons(serialized, reader, sphere, entity_line_id)
    labels = pattern.dim_labels
    for poly, ep in zip(polygons, enriched, strict=True):
        if ep.get("is_anomaly"):
            # Calibrated patterns: the navigator attaches the evidence split.
            calibrated_dims = getattr(poly, "anomaly_dimensions", None)
            if calibrated_dims is not None:
                ep["anomaly_dimensions"] = calibrated_dims
                continue
            from hypertopos.engine.geometry import GDSEngine

            ep["anomaly_dimensions"] = GDSEngine.anomaly_dimensions(ep["delta"], labels)

    # M4 additive: attach total_impact + classification per entry. Absent on
    # event-type and calibrated patterns; None per-entry when N<2 or the storage
    # backend lacks shape-reconstruction prerequisites.
    enriched = nav._attach_influence_fields_to_anomaly_entries(enriched, pattern_id)

    # F2a — trim degenerate edges (empty point_key = dim-line edges, not jumpable).
    # Full edges available via get_polygon. Applied even in default (non-summary) mode;
    # an empty list is dropped (total_edges / alive_edges still report the counts).
    for ep in enriched:
        if "edges" not in ep:
            continue
        edges = [e for e in ep["edges"] or [] if e.get("point_key")]
        if edges:
            ep["edges"] = edges
        else:
            del ep["edges"]

    # F2b — summary mode: drop heavy fields to keep response < 30% of full payload.
    if summary:
        _SUMMARY_DROP = ("delta", "edges", "total_edges", "alive_edges", "properties")
        for ep in enriched:
            for k in _SUMMARY_DROP:
                ep.pop(k, None)
    slim_polygons(enriched, pattern_id)
    properties_table = tabulate_properties(enriched, "primary_key")
    related_properties = tabulate_edge_properties(enriched)

    clusters = nav.classify_anomalies(polygons, pattern_id)
    if summary:
        for cluster in clusters:
            cluster.pop("delta", None)
    omitted: dict[str, Any] = {}
    mode = "full" if summary is False else active_response_format()
    if summary is None and mode == "compact":
        if drop_positional_deltas(enriched, clusters):
            omitted["delta"] = "polygons and clusters (anomaly_dimensions / labels kept)"
        if properties_table:
            keep = {rank_by_property, *(property_filters or {})} - {None}
            dropped = project_descriptive(properties_table, keep)
            if dropped:
                omitted["properties"] = dropped
        if omitted:
            omitted["full_response"] = (
                f"repeat with summary=false, or get_polygon('{pattern_id}') after goto(<key>)"
            )
    result = {
        "pattern_id": pattern_id,
        **({"radius": radius} if radius is not None else {}),
        "offset": offset,
        "total_found": total_found,
        "found": len(enriched),
        "ranked_by": rank_by_property
        or rank_by
        or ("anomaly_score" if pattern.score_dims else "delta_norm"),
        **(pi5_meta if pi5_meta else {}),
        "polygons": enriched,
        **({"properties": properties_table} if properties_table else {}),
        **({"related_properties": related_properties} if related_properties else {}),
        "clusters": clusters,
        "note": (
            "clusters[].count reflects returned polygons only (top_n). "
            "Call anomaly_summary for population-wide cluster counts."
        ),
    }
    if any("delta" in p for p in enriched):
        result["dim_labels"] = pattern.dim_labels
    if omitted:
        result["omitted"] = omitted
    if capped_warning:
        result["capped_warning"] = capped_warning

    if emerging is not None:
        result["emerging"] = emerging
        if pattern.score_dims:
            result["emerging_note"] = (
                "Not available on a calibrated pattern: the forecast tracks delta_norm "
                "against theta, which does not decide is_anomaly here."
            )

    _bgn = binary_geometry_note_for_pattern(pattern_id)
    if _bgn:
        result["binary_geometry_note"] = _bgn

    # Signal stability warning — only when temporal data is small enough
    # to compute quickly. temporal_quality_summary reads the FULL temporal
    # dataset (4M+ rows for 150K entities) — skip for large populations.
    if pattern.pattern_type == "anchor" and pattern.population_size <= 50_000:
        tq = nav.temporal_quality_summary(pattern_id)
        if tq and tq.get("signal_quality") in ("volatile", "mixed"):
            sq = tq["signal_quality"]
            tr = tq.get("transition_rate", 0)
            result["signal_stability_note"] = (
                f"Temporal signal is '{sq}' — {round(tr * 100)}% of anomalous entities "
                f"change status between snapshots. Rankings are ephemeral; "
                f"re-check after the next calibration cycle."
            )

    return json.dumps(sanitize_for_json(result), indent=2, default=json_default)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def anomaly_summary(pattern_id: str, max_clusters: int = 20) -> str:
    """Statistical overview of anomalies: count, rate, percentiles, clusters, top driving dimensions.

    max_clusters: cap on anomaly clusters returned (default 20). Set 0 for unlimited.
    Returns: anomaly_count, anomaly_rate, delta_norm_percentiles, top_driving_dimensions,
    clusters (compact format drops each cluster's positional delta; its label stays).
    """  # noqa: E501
    _require_navigator()
    nav = _state["navigator"]
    summary = nav.anomaly_summary(pattern_id, max_clusters=max_clusters)

    # top_driving_dimensions is now computed by navigator.anomaly_summary (core)
    if active_response_format() == "compact" and drop_positional_deltas(
        [], summary.get("clusters") or []
    ):
        summary["omitted"] = {
            "delta": "clusters (labels kept)",
            "full_response": "open_sphere(<path>, response_format='full') returns every field",
        }

    _bgn = binary_geometry_note_for_pattern(pattern_id)
    if _bgn:
        summary["binary_geometry_note"] = _bgn

    return json.dumps(sanitize_for_json(summary), indent=2, default=json_default)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def aggregate_anomalies(
    pattern_id: str,
    group_by: str,
    top_n: int = 50,
    sample_size: int | None = None,
    sample_pct: float | None = None,
    include_keys: bool = False,
    keys_per_group: int = 5,
    property_filters: dict[Any, Any] | None = None,
) -> str:
    """Group all anomalous entities by a property column — shows anomaly distribution without pagination.

    group_by: entity line column name (or sub-key like "suppkey" for composite patterns).
    include_keys: include sample entity keys per group (default false).
    sample_size/sample_pct: subsample for large patterns (>500K entities).
    property_filters: narrow the population first, same syntax as find_anomalies ({"col": {"in": ["A", "B"]}}).
    Returns: per-group anomaly_count and mean_delta_norm, sorted by count desc.
    """  # noqa: E501
    _require_navigator()
    nav = _state["navigator"]
    pattern = _state["sphere"]._sphere.patterns[pattern_id]
    actual_sample = sample_size
    if actual_sample is None and sample_pct is not None:
        actual_sample = pattern.effective_sample_size(sample_pct)
    result = nav.aggregate_anomalies(
        pattern_id,
        group_by=group_by,
        top_n=top_n,
        sample_size=actual_sample,
        include_keys=include_keys,
        keys_per_group=keys_per_group,
        property_filters=property_filters,
    )
    return json.dumps(sanitize_for_json(result), indent=2)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@timed
def attract_boundary(
    alias_id: str,
    pattern_id: str,
    direction: str = "both",
    top_n: int = 10,
    select: str = "top_norm",
) -> str:
    """Find entities closest to an alias segment boundary (requires cutting_plane).

    direction: "in" (at risk of leaving), "out" (about to enter), or "both".
    select: "top_norm" (default, rank by score) or "diverse" (submodular facility location — K most diverse representatives with representativeness counts).
    Returns: entities sorted by |signed_distance| ascending (positive = inside segment), each with delta_norm, score_rank_pct and is_anomaly.
    """  # noqa: E501
    _require_navigator()
    nav = _state["navigator"]
    attract = nav.π6_attract_boundary
    pairs = attract(
        alias_id,
        pattern_id,
        direction=direction,
        top_n=top_n,
        select=select,
    )

    results = []
    for polygon, signed_dist in pairs:
        entry = {
            "primary_key": polygon.primary_key,
            "signed_distance": round(signed_dist, 4),
            "is_in_segment": signed_dist >= 0,
            "delta_norm": round(polygon.delta_norm, 4),
            "score_rank_pct": round(float(polygon.score_rank_pct), 2)
            if polygon.score_rank_pct is not None
            else None,  # noqa: E501
            "is_anomaly": polygon.is_anomaly,
        }
        representativeness = getattr(polygon, "representativeness", None)
        if representativeness is not None:
            entry["representativeness"] = int(representativeness)
        results.append(entry)

    return json.dumps(
        sanitize_for_json(
            {
                "alias_id": alias_id,
                "pattern_id": pattern_id,
                "direction": direction,
                "count": len(results),
                "results": results,
            }
        ),
        indent=2,
    )
