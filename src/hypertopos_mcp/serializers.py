# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Serialization helpers — convert model objects to JSON-safe dicts."""

from __future__ import annotations

from typing import Any


def _serialize_point(pt: Any) -> dict:
    from datetime import date, datetime

    def _safe(v: Any) -> Any:
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        return v

    return {
        "type": "Point",
        "primary_key": pt.primary_key,
        "line_id": pt.line_id,
        "status": pt.status,
        "properties": {k: _safe(v) for k, v in pt.properties.items()},
    }


def _serialize_edge(e: Any) -> dict:
    return {
        "line_id": e.line_id,
        "point_key": e.point_key,
        "status": e.status,
        "direction": e.direction,
        "is_jumpable": e.is_jumpable,
    }


def _serialize_polygon(poly: Any) -> dict:
    alive = poly.alive_edges()
    result = {
        "type": "Polygon",
        "primary_key": poly.primary_key,
        "pattern_id": poly.pattern_id,
        "delta": [round(float(x), 4) for x in poly.delta],
        "delta_norm": round(float(poly.delta_norm), 4),
        **(
            {"delta_rank_pct": round(float(poly.delta_rank_pct), 2)}
            if poly.delta_rank_pct is not None
            else {}
        ),
        "is_anomaly": bool(poly.is_anomaly),
        **({"bregman_divergence": round(float(poly.bregman_divergence), 4)}
           if getattr(poly, "bregman_divergence", None) is not None else {}),
        **({"anomaly_confidence": round(float(poly.anomaly_confidence), 4)}
           if getattr(poly, "anomaly_confidence", None) not in (None, 0.0) else {}),
        "total_edges": len(poly.edges),
        "alive_edges": len(alive),
        "edges": [_serialize_edge(e) for e in alive],
    }
    # FDR q-value (set by navigator when fdr_alpha is used)
    q_value = getattr(poly, "q_value", None)
    if q_value is not None:
        result["q_value"] = round(float(q_value), 6)
    # Per-dim FDR (set by navigator when fdr_axis in {"per_dim", "both"})
    min_q_per_dim = getattr(poly, "min_q_per_dim", None)
    if min_q_per_dim is not None:
        result["min_q_per_dim"] = round(float(min_q_per_dim), 6)
        result["dominant_q_dim_idx"] = int(poly.dominant_q_dim_idx)
        result["q_values_per_dim"] = [
            round(float(q), 6) for q in poly.q_values_per_dim
        ]
    # Representativeness count (set by navigator when select="diverse")
    representativeness = getattr(poly, "representativeness", None)
    if representativeness is not None:
        result["representativeness"] = int(representativeness)
    # Multi-resolution FDR cell fields (set by navigator when cell_axis is active)
    cell_q_spatial = getattr(poly, "cell_q_spatial", None)
    if cell_q_spatial is not None:
        result["cell_q_spatial"] = round(float(cell_q_spatial), 6)
    cell_q_temporal = getattr(poly, "cell_q_temporal", None)
    if cell_q_temporal is not None:
        result["cell_q_temporal"] = round(float(cell_q_temporal), 6)
    cell_path = getattr(poly, "cell_path", None)
    if cell_path is not None:
        result["cell_path"] = [list(pair) for pair in cell_path]
    # Reliability flags (set by navigator on every π5_attract_anomaly call)
    reliability_flags = getattr(poly, "reliability_flags", None)
    if reliability_flags is not None:
        result["reliability_flags"] = reliability_flags
    # Signed-confidence triad (set by navigator when
    # rank_by="signed_confidence"). Sanitises ±inf/NaN to null per the
    # strict-JSON convention.
    signed_confidence_score = getattr(poly, "signed_confidence_score", None)
    if signed_confidence_score is not None:
        import math as _math
        result["signed_confidence_score"] = (
            None if not _math.isfinite(float(signed_confidence_score))
            else round(float(signed_confidence_score), 6)
        )
        lda_alignment = getattr(poly, "lda_alignment", None)
        if lda_alignment is not None:
            result["lda_alignment"] = (
                None if not _math.isfinite(float(lda_alignment))
                else round(float(lda_alignment), 6)
            )
        reliability_penalty = getattr(poly, "reliability_penalty", None)
        if reliability_penalty is not None:
            result["reliability_penalty"] = (
                None if not _math.isfinite(float(reliability_penalty))
                else round(float(reliability_penalty), 6)
            )
    return result


def _serialize_slice(sl: Any, pattern: Any | None = None) -> dict:
    result: dict = {
        "slice_index": sl.slice_index,
        "timestamp": sl.timestamp.isoformat(),
        "deformation_type": sl.deformation_type,
        "delta_norm_snapshot": round(float(sl.delta_norm_snapshot), 4),
        "changed_property": sl.changed_property,
        "changed_line_id": sl.changed_line_id,
    }
    if pattern is not None and pattern.prop_columns:
        result["delta_snapshot"] = sl.delta_relations(pattern)
        result["prop_column_states"] = sl.prop_column_states(pattern)
    else:
        result["delta_snapshot"] = [round(float(x), 4) for x in sl.delta_snapshot]
    frozen = getattr(sl, "delta_norm_frozen_pop", None)
    if frozen is not None:
        import math as _math
        result["delta_norm_frozen_pop"] = (
            None if not _math.isfinite(float(frozen))
            else round(float(frozen), 4)
        )
    return result


def _serialize_solid(solid: Any, pattern: Any | None = None) -> dict:
    return {
        "type": "Solid",
        "primary_key": solid.primary_key,
        "pattern_id": solid.pattern_id,
        "base_polygon": _serialize_polygon(solid.base_polygon),
        "num_slices": len(solid.slices),
        "slices": [_serialize_slice(s, pattern=pattern) for s in solid.slices],
    }


def _serialize_position(pos: Any) -> dict:
    if pos is None:
        return {"type": "None", "message": "Navigator has no position. Use goto() first."}
    from hypertopos.model.objects import Point, Polygon, Solid

    if isinstance(pos, Point):
        return _serialize_point(pos)
    if isinstance(pos, Polygon):
        return _serialize_polygon(pos)
    if isinstance(pos, Solid):
        return _serialize_solid(pos)
    return {"type": type(pos).__name__, "repr": str(pos)}
