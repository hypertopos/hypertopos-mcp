# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Detection tools — single-call anomaly category detection recipes."""

from __future__ import annotations

import json

from hypertopos_mcp.server import _require_navigator, _state, mcp, timed


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def detect_cross_pattern_discrepancy(
    entity_line: str,
    top_n: int = 50,
) -> str:
    """Detect entities anomalous in one pattern but normal in another (cross-pattern split signal).

    entity_line: anchor line to screen. Requires >=2 patterns covering this line.
    Returns: entities with anomalous_pattern, normal_patterns, delta_norm, interpretation.
    """
    _require_navigator()
    nav = _state["navigator"]
    results = nav.detect_cross_pattern_discrepancy(entity_line, top_n=top_n)
    return json.dumps(
        {"entity_line": entity_line, "total_found": len(results), "results": results},
        indent=2,
    )


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def detect_neighbor_contamination(
    pattern_id: str,
    k: int = 10,
    sample_size: int = 20,
    contamination_threshold: float = 0.5,
) -> str:
    """Detect normal entities surrounded by anomalous geometric neighbors (contamination risk).

    k: nearest neighbors per entity (default 10). sample_size: anomalous seeds (default 20).
    contamination_threshold: min fraction of anomalous neighbors to flag (default 0.5).
    Returns: normal entities with contamination_rate, anomalous_neighbor_count.
    """
    _require_navigator()
    nav = _state["navigator"]
    results = nav.detect_neighbor_contamination(
        pattern_id, k=k, sample_size=sample_size, contamination_threshold=contamination_threshold
    )
    return json.dumps(
        {
            "pattern_id": pattern_id,
            "k": k,
            "sample_size": sample_size,
            "contamination_threshold": contamination_threshold,
            "total_found": len(results),
            "results": results,
        },
        indent=2,
    )


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def detect_trajectory_anomaly(
    pattern_id: str,
    displacement_ranks: list[int] | None = None,
    top_n_per_range: int = 5,
) -> str:
    """Detect entities with non-linear temporal trajectories (arch, V-shape, spike-recovery).

    Anchor patterns with temporal data only. top_n_per_range: max results (default 5).
    Returns: entities with trajectory_shape, displacement, path_length, cohort_size/keys.
    """
    _require_navigator()
    nav = _state["navigator"]
    try:
        results = nav.detect_trajectory_anomaly(
            pattern_id,
            displacement_ranks=displacement_ranks,
            top_n_per_range=top_n_per_range,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, indent=2)
    return json.dumps(
        {
            "pattern_id": pattern_id,
            "displacement_ranks": displacement_ranks or [0, 20, 100],
            "top_n_per_range": top_n_per_range,
            "total_found": len(results),
            "results": results,
        },
        indent=2,
        default=str,
    )


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def detect_segment_shift(
    pattern_id: str,
    max_cardinality: int = 50,
    min_shift_ratio: float = 2.0,
    top_n: int = 20,
) -> str:
    """Detect population segments with disproportionate anomaly rates (segment shift).

    max_cardinality: skip columns with more distinct values (default 50).
    min_shift_ratio: min segment/population anomaly rate ratio to report (default 2.0).
    Returns: segments with anomaly_rate, shift_ratio, entity_count, interpretation.
    """
    _require_navigator()
    nav = _state["navigator"]
    try:
        results = nav.detect_segment_shift(
            pattern_id,
            max_cardinality=max_cardinality,
            min_shift_ratio=min_shift_ratio,
            top_n=top_n,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, indent=2)
    return json.dumps(
        {
            "pattern_id": pattern_id,
            "max_cardinality": max_cardinality,
            "min_shift_ratio": min_shift_ratio,
            "total_found": len(results),
            "results": results,
        },
        indent=2,
    )
