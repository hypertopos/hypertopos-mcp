# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Navigation tools — goto, walk, jump, dive, emerge, anomalies."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from hypertopos_mcp.enrichment import (
    enrich_polygons,
    lookup_entity,
    resolve_entity_line_id,
)
from hypertopos_mcp.serializers import _serialize_polygon, _serialize_position, _serialize_solid
from hypertopos_mcp.server import _require_navigator, _state, mcp, timed
from hypertopos_mcp.tools._guards import adaptive_polygon_cap, binary_geometry_note_for_pattern

_STALE_SOLID_DAYS = 180


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def goto(primary_key: str, line_id: str) -> str:
    """Navigate to a specific entity by business key and line. Sets current position."""
    _require_navigator()
    nav = _state["navigator"]
    nav.goto(primary_key, line_id)
    return json.dumps(_serialize_position(nav.position), indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_position() -> str:
    """Return the current navigator position (Point, Polygon, Solid, or None)."""
    _require_navigator()
    return json.dumps(_serialize_position(_state["navigator"].position), indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def walk_line(line_id: str, direction: str = "+") -> str:
    """Walk one step along a line to the next (+) or previous (-) entity.

    direction: "+" (next) or "-" (previous). Requires position on the given line.
    Returns: new position. For bulk traversal, prefer search_entities or aggregate.
    """
    _require_navigator()
    nav = _state["navigator"]
    walk = nav.π1_walk_line
    walk(line_id, direction)
    return json.dumps(_serialize_position(nav.position), indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def jump_polygon(target_line_id: str, edge_index: int = 0) -> str:
    """Jump through the current polygon to a point on another line via an alive edge.

    edge_index: which edge to follow when multiple exist (default 0). Check total_edges_to_target in response.
    Returns: new position on target line.
    """
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
        resp: dict = {
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
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def dive_solid(primary_key: str, pattern_id: str, timestamp: str | None = None) -> str:
    """Dive into the temporal history of an entity as a Solid.

    timestamp: optional ISO-8601 cutoff — only slices at or before this time.
    Returns: base polygon, temporal slices, reputation, and forecast (if >=3 slices).
    """
    _require_navigator()
    nav = _state["navigator"]
    ts: datetime | None = None
    if timestamp is not None:
        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    dive = nav.π3_dive_solid
    dive(primary_key, pattern_id, timestamp=ts)
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
            "Initial entity state before recorded deformations. "
            "delta_norm here reflects the entity at first observation. "
            "For the current state, call get_polygon(pattern_id=...)."
        )
    # Add forecast when solid has enough slices
    if len(solid.slices) >= 3:
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
    return json.dumps(ss, separators=(",", ":"), default=str)


@mcp.tool(annotations={"readOnlyHint": True})
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
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_anomalies(
    pattern_id: str,
    radius: float = 1,
    top_n: int = 10,
    include_emerging: bool = False,
    offset: int = 0,
    missing_edge_to: str | None = None,
    rank_by_property: str | None = None,
    property_filters: dict | None = None,
    fdr_alpha: float | None = None,
    fdr_method: str = "bh",
    p_value_method: str = "rank",
    select: str = "top_norm",
    metric: str = "L2",
    min_confidence: float = 0.0,
) -> str:
    """Find the most anomalous polygons in a pattern, ranked by delta_norm.

    metric: "L2" (default, pre-computed delta_norm), "Linf" (max single-dimension |delta|), or "bregman" (distribution-aware Bregman divergence).

    radius: multiplier on theta_norm threshold (default 1). Higher = looser boundary.
    include_emerging: append entities trending toward anomaly (requires temporal data).
    missing_edge_to: keep only anomalous entities with NO edge to this line.
    rank_by_property: re-rank by a raw entity property instead of delta_norm.
    property_filters: filter by entity properties before ranking ({"col": {"gt": X}}).
    fdr_alpha: apply Benjamini-Hochberg FDR control at this level. Returns only entities with q_value <= alpha. Default None = legacy behavior.
    fdr_method: "bh" (default, Benjamini-Hochberg assumes pi0=1) or "storey" (LSL estimator of true null proportion — shrinks q-values by pi0, typically recovers 10-15% more discoveries when combined with p_value_method="chi2" on spheres that have a genuine null mass).
    p_value_method: "rank" (default, uniform by construction) or "chi2" (upper-tail chi-squared survival on ||delta||², df=dimensionality). Pair with fdr_method="storey" for power recovery; with rank, Storey collapses to BH.
    select: "top_norm" (default, rank by score) or "diverse" (submodular facility location — K most diverse representatives with representativeness counts).
    min_confidence: filter by bootstrap confidence threshold (0.0 = no filter). Requires bregman_calibration=True on the sphere.
    Returns: anomalous polygons with anomaly_dimensions, clusters, total_found.
    """
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
    r = radius if radius > 0 else None
    polygons, total_found, emerging, pi5_meta = attract(
        pattern_id,
        radius=r,
        top_n=top_n,
        offset=offset,
        missing_edge_to=missing_edge_to,
        include_emerging=include_emerging,
        rank_by_property=rank_by_property,
        property_filters=property_filters,
        fdr_alpha=fdr_alpha,
        fdr_method=fdr_method,
        p_value_method=p_value_method,
        select=select,
        metric=metric,
        min_confidence=min_confidence,
    )

    serialized = [_serialize_polygon(p) for p in polygons]
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    enriched = enrich_polygons(serialized, reader, sphere, entity_line_id)
    labels = pattern.dim_labels
    for ep in enriched:
        if ep.get("is_anomaly"):
            from hypertopos.engine.geometry import GDSEngine

            ep["anomaly_dimensions"] = GDSEngine.anomaly_dimensions(ep["delta"], labels)

    # M4 additive: attach total_impact + classification per entry. Resolves
    # to None per-entry when pattern is event-type, N<2, or storage backend
    # lacks shape-reconstruction prerequisites.
    enriched = nav._attach_influence_fields_to_anomaly_entries(enriched, pattern_id)

    clusters = nav.classify_anomalies(polygons, pattern_id)
    result = {
        "pattern_id": pattern_id,
        "radius": radius,
        "offset": offset,
        "total_found": total_found,
        "found": len(enriched),
        "ranked_by": rank_by_property or "delta_norm",
        **(pi5_meta if pi5_meta else {}),
        "polygons": enriched,
        "clusters": clusters,
        "note": (
            "clusters[].count reflects returned polygons only (top_n). "
            "Call anomaly_summary for population-wide cluster counts."
        ),
    }
    if capped_warning:
        result["capped_warning"] = capped_warning

    if emerging is not None:
        result["emerging"] = emerging

    _bgn = binary_geometry_note_for_pattern(pattern_id)
    if _bgn:
        result["binary_geometry_note"] = _bgn

    # Signal stability warning — only when temporal data is small enough
    # to compute quickly. temporal_quality_summary reads the FULL temporal
    # dataset (4M+ rows for 150K entities) — skip for large populations.
    if pattern.pattern_type == "anchor" and pattern.population_size <= 50_000:
        tq = nav.temporal_quality_summary(pattern_id)
        if tq and tq.get("signal_quality") not in ("persistent", "no_anomalies", None):
            sq = tq["signal_quality"]
            tr = tq.get("transition_rate", 0)
            result["signal_stability_note"] = (
                f"Temporal signal is '{sq}' — {round(tr * 100)}% of anomalous entities "
                f"change status between snapshots. Rankings are ephemeral; "
                f"re-check after the next calibration cycle."
            )

    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def anomaly_summary(pattern_id: str, max_clusters: int = 20) -> str:
    """Statistical overview of anomalies: count, rate, percentiles, clusters, top driving dimensions.

    max_clusters: cap on anomaly clusters returned (default 20). Set 0 for unlimited.
    Returns: anomaly_count, anomaly_rate, delta_norm_percentiles, top_driving_dimensions.
    """
    _require_navigator()
    nav = _state["navigator"]
    summary = nav.anomaly_summary(pattern_id, max_clusters=max_clusters)

    # top_driving_dimensions is now computed by navigator.anomaly_summary (core)

    _bgn = binary_geometry_note_for_pattern(pattern_id)
    if _bgn:
        summary["binary_geometry_note"] = _bgn

    return json.dumps(summary, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def aggregate_anomalies(
    pattern_id: str,
    group_by: str,
    top_n: int = 50,
    sample_size: int | None = None,
    sample_pct: float | None = None,
    include_keys: bool = False,
    keys_per_group: int = 5,
    property_filters: dict | None = None,
) -> str:
    """Group all anomalous entities by a property column — shows anomaly distribution without pagination.

    group_by: entity line column name (or sub-key like "suppkey" for composite patterns).
    include_keys: include sample entity keys per group (default false).
    sample_size/sample_pct: subsample for large patterns (>500K entities).
    Returns: per-group anomaly_count and mean_delta_norm, sorted by count desc.
    """
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
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def attract_boundary(
    alias_id: str,
    pattern_id: str,
    direction: str = "both",
    top_n: int = 10,
    fdr_alpha: float | None = None,
    fdr_method: str = "bh",
    p_value_method: str = "rank",
    select: str = "top_norm",
) -> str:
    """Find entities closest to an alias segment boundary (requires cutting_plane).

    direction: "in" (at risk of leaving), "out" (about to enter), or "both".
    fdr_alpha: apply Benjamini-Hochberg FDR control at this level. Returns only entities with q_value <= alpha. Default None = legacy behavior.
    fdr_method: "bh" (default, Benjamini-Hochberg assumes pi0=1) or "storey" (LSL null-proportion estimator; recovers 10-15% more discoveries when combined with p_value_method="chi2" on spheres with genuine null mass).
    p_value_method: "rank" (default) or "chi2" (parametric null, required for Storey to shrink q-values).
    select: "top_norm" (default, rank by score) or "diverse" (submodular facility location — K most diverse representatives with representativeness counts).
    Returns: entities sorted by |signed_distance| ascending. Positive = inside segment.
    """
    _require_navigator()
    nav = _state["navigator"]
    attract = nav.π6_attract_boundary
    pairs = attract(
        alias_id, pattern_id, direction=direction, top_n=top_n,
        fdr_alpha=fdr_alpha, fdr_method=fdr_method,
        p_value_method=p_value_method, select=select,
    )

    results = []
    for polygon, signed_dist in pairs:
        entry = {
            "primary_key": polygon.primary_key,
            "signed_distance": round(signed_dist, 4),
            "is_in_segment": signed_dist >= 0,
            "delta_norm": round(polygon.delta_norm, 4),
            "delta_rank_pct": round(float(polygon.delta_rank_pct), 2)
            if polygon.delta_rank_pct is not None
            else None,  # noqa: E501
            "is_anomaly": polygon.is_anomaly,
        }
        q_value = getattr(polygon, "q_value", None)
        if q_value is not None:
            entry["q_value"] = round(float(q_value), 6)
        representativeness = getattr(polygon, "representativeness", None)
        if representativeness is not None:
            entry["representativeness"] = int(representativeness)
        results.append(entry)

    return json.dumps(
        {
            "alias_id": alias_id,
            "pattern_id": pattern_id,
            "direction": direction,
            "count": len(results),
            "results": results,
        },
        indent=2,
    )


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_neighborhood(
    primary_key: str,
    pattern_id: str,
    max_hops: int = 2,
    max_entities: int = 100,
) -> str:
    """BFS traversal through jumpable polygon edges to discover an entity's neighborhood.

    Only works for binary FK mode (explicit point_key). For continuous mode, use find_counterparties.
    max_hops: edge-hops from center (default 2). max_entities: cap on returned neighbors (default 100).
    Returns: reachable entities with hop distance, anomaly status, delta_rank_pct.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.find_neighborhood(
        primary_key,
        pattern_id,
        max_hops=max_hops,
        max_entities=max_entities,
    )
    return json.dumps(result, indent=2)
