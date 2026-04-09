# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Response-size guards shared across MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypertopos.model.sphere import Pattern

_BINARY_GEOMETRY_NOTE = (
    "This pattern uses binary geometry (0/1 edge indicators). "
    "All anomalies share near-identical delta vectors — clustering, similarity search, "
    "and anomaly dimension analysis have limited discriminative value. "
    "Focus on aggregate counts and relation-based navigation instead."
)


def binary_geometry_note_for_pattern(pattern_id: str) -> str | None:
    """Return warning note if pattern uses binary geometry, else None."""
    from hypertopos_mcp.server import _state

    nav = _state["navigator"]
    overview = nav.sphere_overview(pattern_id)
    if overview and overview[0].get("geometry_mode") == "binary":
        return _BINARY_GEOMETRY_NOTE
    return None


def dead_dim_indices(pattern_id: str) -> list[int]:
    """Return dim indices with near-zero delta variance. Delegated to navigator."""
    try:
        from hypertopos_mcp.server import _state

        return _state["navigator"].dead_dim_indices(pattern_id)
    except Exception:
        return []


_CHAR_BUDGET = 50_000
_BASE_POLYGON_OVERHEAD = 250
_PER_EDGE_ENRICHED = 300
_PER_EDGE_MINIMAL = 80
_PER_PROPERTY_CHAR = 35  # ~35 chars per property key:value in JSON
_DEFAULT_ENTITY_PROPS = 15  # conservative default when column count unknown
_MIN_CAP = 5


def adaptive_polygon_cap(
    pattern: Pattern,
    n_entity_props: int | None = None,
) -> int:
    """Return max polygons that fit within the char budget for a given pattern.

    Event patterns have enriched edges (~300 chars each incl. JSON overhead).
    Anchor patterns in continuous mode have minimal edges (~80 chars each).

    n_entity_props: number of entity property columns. When provided for anchor
    patterns, adds ~35 chars/property to the per-polygon estimate. Event patterns
    already include entity properties inline in edges, so this has no effect.
    Defaults to 15 (conservative) when not provided.
    """
    n_edges = len(pattern.relations)
    is_enriched = pattern.pattern_type == "event" or pattern.edge_max is None
    per_edge = _PER_EDGE_ENRICHED if is_enriched else _PER_EDGE_MINIMAL

    # Event edges already include entity properties inline — no extra cost.
    # Anchor continuous-mode edges are minimal; properties come separately.
    if is_enriched:
        est_props = 0
    else:
        _n_props = n_entity_props if n_entity_props is not None else _DEFAULT_ENTITY_PROPS
        est_props = _n_props * _PER_PROPERTY_CHAR

    est_per_polygon = _BASE_POLYGON_OVERHEAD + n_edges * per_edge + est_props
    return max(_CHAR_BUDGET // est_per_polygon, _MIN_CAP)
