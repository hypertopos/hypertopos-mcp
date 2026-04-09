# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Geometry tools — polygon, solid, event polygons."""

from __future__ import annotations

import json

from hypertopos.engine.geometry import GDSEngine

from hypertopos_mcp.enrichment import (
    enrich_polygons,
    resolve_entity_line_id,
)
from hypertopos_mcp.serializers import _serialize_polygon, _serialize_solid
from hypertopos_mcp.server import _require_navigator, _state, mcp, timed
from hypertopos_mcp.tools._guards import adaptive_polygon_cap


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_polygon(pattern_id: str) -> str:
    """Build and return the polygon for the current Point position (call goto() first).

    Returns: delta vector, delta_norm, is_anomaly, alive edges, temporal_hint (anchor only).
    When anomalous: includes anomaly_dimensions, witness set, and repair set.
    """
    _require_navigator()
    nav = _state["navigator"]
    polygon = nav.current_polygon(pattern_id)
    sp = _serialize_polygon(polygon)
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    [enriched] = enrich_polygons([sp], reader, sphere, entity_line_id)
    pattern = sphere.patterns[pattern_id]
    enriched["theta_norm"] = round(pattern.theta_norm, 4)
    if enriched.get("is_anomaly"):
        labels = pattern.dim_labels
        enriched["anomaly_dimensions"] = GDSEngine.anomaly_dimensions(enriched["delta"], labels)
        enriched["witness"] = GDSEngine.witness_set(enriched["delta"], pattern.theta_norm, labels)
        enriched["repair"] = GDSEngine.anti_witness(enriched["delta"], pattern.theta_norm, labels)
    # Temporal hint for anchor patterns
    if pattern.pattern_type == "anchor":
        try:
            hint = nav.temporal_hint(polygon.primary_key, pattern_id)
            if hint is not None:
                enriched["temporal_hint"] = {
                    "num_slices": hint["num_slices"],
                    "last_deformation_timestamp": hint["last_timestamp"],
                }
            else:
                enriched["temporal_hint"] = {
                    "num_slices": 0,
                    "last_deformation_timestamp": None,
                }
        except Exception:
            pass  # temporal data unavailable — skip hint silently
    return json.dumps(enriched, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_solid(
    pattern_id: str, timestamp_from: str | None = None, timestamp_to: str | None = None
) -> str:
    """Build and return the temporal solid for the current Point position (call goto() first).

    timestamp_from/timestamp_to: optional ISO-8601 range [from, to) to limit slices.
    Returns: base polygon, deformation slices, theta_norm, and forecast (if >=3 slices).
    """
    _require_navigator()
    nav = _state["navigator"]
    filters: dict[str, str | list[str]] | None = None
    if timestamp_from is not None or timestamp_to is not None:
        filters = {}
        if timestamp_from is not None:
            filters["timestamp_from"] = timestamp_from
        if timestamp_to is not None:
            filters["timestamp_to"] = timestamp_to
    solid = nav.current_solid(pattern_id, filters=filters)
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns[pattern_id]
    ss = _serialize_solid(solid, pattern=pattern)
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    base = ss.get("base_polygon", {})
    if base:
        [enriched_base] = enrich_polygons([base], reader, sphere, entity_line_id)
        ss["base_polygon"] = enriched_base
    ss["theta_norm"] = round(pattern.theta_norm, 4)
    # Add forecast when solid has enough temporal slices
    if len(solid.slices) >= 3:
        forecast_dict = nav.solid_forecast(
            solid.base_polygon.primary_key,
            pattern_id,
            current_delta_norm=float(solid.base_polygon.delta_norm),
        )
        if forecast_dict is not None:
            if forecast_dict.get("stale_warning"):
                ss["stale_forecast_warning"] = forecast_dict.pop("stale_warning")
            ss["forecast"] = forecast_dict
    return json.dumps(ss, separators=(",", ":"), default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_event_polygons(
    entity_key: str,
    event_pattern_id: str,
    filters: list[dict] | None = None,
    limit: int = 10,
    offset: int = 0,
    geometry_filters: dict | None = None,
    sample: int | None = None,
    sample_pct: float | None = None,
    seed: int | None = None,
) -> str:
    """Find all event polygons that reference a given entity.

    filters: list of {"line": str, "key": str} partition filters.
    geometry_filters: {"is_anomaly": bool, "delta_rank_pct": {"gt": N}, "delta_dim": {...}}.
    sample/sample_pct/seed: random sampling controls.
    Returns: total, returned count, polygon list. For counting/summing, prefer aggregate().
    """
    if sample is not None and sample_pct is not None:
        raise RuntimeError("sample and sample_pct are mutually exclusive.")
    if sample is not None and sample <= 0:
        raise RuntimeError("sample must be a positive integer.")
    if sample_pct is not None and not (0.0 < sample_pct <= 1.0):
        raise RuntimeError("sample_pct must be in range (0.0, 1.0].")
    _require_navigator()
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns[event_pattern_id]
    _entity_line_id = sphere.entity_line(event_pattern_id) or sphere.event_line(event_pattern_id)
    _entity_line = sphere.lines.get(_entity_line_id) if _entity_line_id else None
    _n_entity_props = len(_entity_line.columns) if (_entity_line and _entity_line.columns) else None
    cap = adaptive_polygon_cap(pattern, n_entity_props=_n_entity_props)
    limit_capped_warning: str | None = None
    if limit > cap:
        limit_capped_warning = (
            f"limit={limit} exceeds adaptive hard cap {cap} (based on {len(pattern.relations)} "
            f"edges/polygon) — truncated to keep response under ~50K chars. "
            f"Use offset for pagination or aggregate() for counts."
        )
        limit = cap
    if filters is not None and not isinstance(filters, list):
        raise RuntimeError(
            f"filters must be a list of {{'line': str, 'key': str}} dicts, "
            f"got {type(filters).__name__}. "
            "Example: filters=[{'line': 'company_codes', 'key': 'CC-PL'}]"
        )
    if filters and "is_anomaly" in filters:
        raise RuntimeError(
            "'is_anomaly' is a geometry column, not a partition filter. "
            "Use geometry_filters={'is_anomaly': True/False} instead."
        )
    nav = _state["navigator"]
    use_sampling = sample is not None or sample_pct is not None

    # Delegate sampling to navigator (core handles numpy internally)
    polygons = nav.event_polygons_for(
        entity_key,
        event_pattern_id,
        filters=filters,
        geometry_filters=geometry_filters,
        limit=limit if not use_sampling else None,
        offset=offset if not use_sampling else 0,
        sample_size=sample,
        sample_pct=sample_pct,
        seed=seed,
    )
    total = nav._last_total_post_geometry_filter or len(polygons)

    # total_unfiltered is recorded during event_polygons_for (before geometry
    # filtering) — no second scan needed.
    total_unfiltered: int | None = nav._last_total_pre_geometry_filter

    # When sampling was requested, apply offset/limit on the sampled result
    actually_sampled = False
    n_sampled: int | None = None
    if use_sampling and total > 0:
        n = sample if sample is not None else max(min(int(total * sample_pct), total), 1)
        if n < total:
            actually_sampled = True
            n_sampled = min(n, len(polygons))
        polygons = polygons[offset : offset + limit]
    serialized = [_serialize_polygon(p) for p in polygons]
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    enriched = enrich_polygons(serialized, reader, sphere)

    result = {
        "entity_key": entity_key,
        "event_pattern_id": event_pattern_id,
        "total": total,
        "returned": len(enriched),
        "polygons": enriched,
    }
    if total_unfiltered is not None:
        result["total_unfiltered"] = total_unfiltered
    result["sampled"] = actually_sampled
    if actually_sampled:
        result["sample_size"] = n_sampled
    if limit_capped_warning:
        result["capped_warning"] = limit_capped_warning
    return json.dumps(result, separators=(",", ":"))
