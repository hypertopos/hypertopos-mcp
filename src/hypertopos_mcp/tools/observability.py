# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Observability tools — population health, batch comparison, data quality."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from hypertopos_mcp.server import (
    _register_manual_tools,
    _require_navigator,
    _state,
    mcp,
    timed,
)


def _sanitize_for_json(obj: Any) -> Any:
    """Replace non-finite floats (``±inf`` / ``NaN``) with ``None`` recursively.

    Mirrors the helper in ``tools.analysis`` — kept local here to avoid a
    cross-tool import. ``json.dumps`` emits ``Infinity`` / ``NaN`` literals
    for non-finite floats which strict parsers reject.
    """
    if isinstance(obj, (float, np.floating)) and not math.isfinite(float(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_for_json(v) for v in obj)
    return obj


@mcp.tool()
@timed
def sphere_overview(pattern_id: str | None = None, detail: str = "summary") -> str:
    """Population-level health summary of one or all patterns in the sphere.

    detail: "summary" (instant O(1)) or "full" (+ temporal_quality, forecast, calibration).
    Returns: per-pattern entity count, anomaly_rate, calibration_health,
    geometry_mode, profiling_alerts.

    **Performance note:** detail="full" runs event-rate-divergence scans that
    cross-reference event anomalies per anchor entity.  On large spheres
    (>100 K entities or >1 M events) this can take **minutes**.  Use
    detail="summary" for interactive exploration; reserve "full" for
    deep-dive diagnostics.
    """
    _require_navigator()
    result = _state["navigator"].sphere_overview(pattern_id)

    # Continuous mode note + profiling alerts — O(1) dict lookup, always included
    sphere = _state["sphere"]._sphere
    for entry in result:
        pid = entry["pattern_id"]
        pattern = sphere.patterns.get(pid)
        if pattern is None:
            continue
        if pattern.edge_max is not None:
            entry["continuous_mode_note"] = (
                "This pattern uses continuous edge encoding — "
                "centroid_map(group_by_line) and contrast_populations(edge spec) "
                "are unavailable. Use group_by_property instead."
            )
        if pattern.dim_percentiles:
            dim_columns = (
                {ed.column for ed in pattern.event_dimensions}
                | set(pattern.prop_columns)
                | {r.line_id[3:] for r in pattern.relations
                   if r.line_id.startswith("_d_")}
            )
            alerts = []
            for dim, stats in pattern.dim_percentiles.items():
                if dim not in dim_columns:
                    continue
                if stats["p99"] > 0:
                    ratio = stats["max"] / stats["p99"]
                    if ratio > 1.5:
                        severity = "extreme" if ratio > 3.0 else "moderate"
                        alerts.append({
                            "pattern_id": pid,
                            "entity_line": pattern.entity_line_id,
                            "dimension": dim,
                            "p25": stats["p25"],
                            "p50": stats["p50"],
                            "p75": stats["p75"],
                            "p99": stats["p99"],
                            "max": stats["max"],
                            "ratio": round(ratio, 1),
                            "alert": (
                                f"{severity} cluster — use "
                                f"find_anomalies(pattern_id='{pid}', "
                                f"rank_by_property='{dim}') to surface"
                            ),
                        })
            if alerts:
                entry["profiling_alerts"] = sorted(
                    alerts, key=lambda a: a["ratio"], reverse=True,
                )

    if detail == "full":
        # Heavy enrichments — forecast, calibration, temporal
        reader = _state["session"]._reader
        for entry in result:
            pid = entry["pattern_id"]
            forecast_table = reader.read_population_forecast(pid)
            if forecast_table is not None and forecast_table.num_rows > 0:
                trends = []
                for i in range(forecast_table.num_rows):
                    trends.append(
                        {
                            "metric": forecast_table["metric"][i].as_py(),
                            "current_value": round(
                                float(forecast_table["current_value"][i].as_py()),
                                4,
                            ),
                            "forecast_value": round(
                                float(forecast_table["forecast_value"][i].as_py()),
                                4,
                            ),
                            "direction": forecast_table["direction"][i].as_py(),
                            "reliability": forecast_table["reliability"][i].as_py(),
                        }
                    )
                # Patch anomaly_rate trend with the live value to avoid stale snapshot mismatch
                for t in trends:
                    if t["metric"] == "anomaly_rate":
                        t["current_value"] = round(float(entry["anomaly_rate"]), 4)
                entry["trends"] = trends

            # Calibration staleness
            tracker = reader.read_calibration_tracker(pid)
            if tracker is not None:
                entry["calibration_stale"] = tracker.is_stale
                entry["calibration_drift_pct"] = round(tracker.drift_pct, 4)
                entry["calibration_blocked"] = tracker.is_blocked

            # Temporal quality
            if entry.get("pattern_type") == "anchor":
                temporal_quality = _state["navigator"].temporal_quality_summary(pid)
                if temporal_quality:
                    entry["temporal_quality"] = temporal_quality

        # Event rate divergence — cross-pattern signal for anchor patterns
        divergence_alerts = _state["navigator"]._compute_event_rate_divergence()
        if divergence_alerts:
            # Group alerts by anchor pattern_id for inline placement
            alerts_by_pattern: dict[str, list[dict]] = {}
            for alert in divergence_alerts:
                alerts_by_pattern.setdefault(alert["pattern_id"], []).append(alert)
            for entry in result:
                pid = entry["pattern_id"]
                if pid in alerts_by_pattern:
                    pat_alerts = alerts_by_pattern[pid]
                    entry["event_rate_divergence_alerts"] = pat_alerts
                    event_pid = pat_alerts[0]["event_pattern_id"]
                    anchor_line = sphere.entity_line(pid) or pid
                    entry["suggested_next_step"] = (
                        f"Run aggregate(event_pattern_id='{event_pid}', "
                        f"group_by_line='{anchor_line}', "
                        f"metric='count', time_from=<period_start>, time_to=<period_end>) "
                        f"on two time windows to confirm WHEN the event burst happened."
                    )

    # Investigation hints — O(1) metadata-only, per pattern (detail="full" only)
    if detail == "full":
        all_hints: list[str] = []
        for entry in result:
            pid = entry["pattern_id"]
            hints: list[str] = []

            # Temporal capabilities
            if entry.get("has_temporal"):
                hints.append(
                    "trajectory_anomaly: temporal data available"
                    " — use detect_trajectory_anomaly"
                )
                hints.append(
                    "drift: temporal data — use find_drifting_entities"
                )
                hints.append(
                    "regime_changes: temporal — use find_regime_changes"
                )

            # Cross-pattern: multiple patterns on same entity line
            entity_line = sphere.entity_line(pid)
            if entity_line:
                patterns_on_line = [
                    p for p in sphere.patterns
                    if sphere.entity_line(p) == entity_line
                ]
                if len(patterns_on_line) >= 2:
                    hints.append(
                        f"cross_pattern: {len(patterns_on_line)} patterns"
                        f" on {entity_line}"
                        " — use detect_cross_pattern_discrepancy"
                    )

            # Segment shift: categorical properties available
            pattern = sphere.patterns.get(pid)
            if pattern and pattern.prop_columns:
                hints.append(
                    f"segment_shift: {len(pattern.prop_columns)}"
                    " categorical properties"
                    " — use detect_segment_shift"
                )

            # Neighbor contamination: always available via ANN index
            hints.append(
                "neighbor_contamination: ANN index available"
                " — use detect_neighbor_contamination"
            )

            # Anomaly scan: always available
            hints.append("anomaly_scan: use find_anomalies for top anomalies")

            if hints:
                entry["investigation_hints"] = hints
                all_hints.extend(hints)

        # Cache for detect_pattern planning
        _state["investigation_hints"] = all_hints

    # Phase 3: unlock full manual toolset after first sphere_overview
    if not _state.get("manual_mode"):
        _register_manual_tools()

    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def compare_time_windows(
    pattern_id: str,
    window_a_from: str,
    window_a_to: str,
    window_b_from: str,
    window_b_to: str,
) -> str:
    """Compare population geometry between two time windows — detects centroid shift.

    Windows are ISO-8601 half-open ranges [from, to).
    Returns: centroid_shift (L2 distance) and top_changed_dimensions by |diff|.
    """
    _require_navigator()
    result = _state["navigator"].π11_attract_population_compare(
        pattern_id,
        window_a_from,
        window_a_to,
        window_b_from,
        window_b_to,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def detect_data_quality_issues(
    pattern_id: str,
    sample_size: int | None = None,
    sample_pct: float | None = None,
) -> str:
    """Scan for geometric integrity issues in a pattern's geometry.

    Checks: coverage gaps, degenerate polygons, high/zero anomaly rates, theta calibration, delta-norm integrity.
    Does NOT detect domain-level outliers (unusual values, truncated dates) — use find_anomalies for those.
    sample_size/sample_pct: subsample for large patterns (>500K entities).
    Returns: findings[] sorted by severity (HIGH first). Empty = no structural issues.
    """
    _require_navigator()
    _effective_sample = sample_size
    if sample_pct is not None and _effective_sample is None:
        sphere = _state["sphere"]._sphere
        pattern = sphere.patterns.get(pattern_id)
        if pattern:
            _effective_sample = pattern.effective_sample_size(sample_pct)
    findings = _state["navigator"].detect_data_quality_issues(
        pattern_id,
        sample_size=_effective_sample,
    )
    return json.dumps({"pattern_id": pattern_id, "findings": findings}, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_regime_changes(
    pattern_id: str,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
    n_regimes: int = 3,
) -> str:
    """Detect when population geometry shifted significantly (changepoint detection, anchor only).

    timestamp_from/timestamp_to: optional ISO-8601 bounds. n_regimes: max changepoints (default 3).
    Returns: changepoints with timestamp, magnitude, top_changed_dimensions.
    """
    _require_navigator()
    result = _state["navigator"].π12_attract_regime_change(
        pattern_id,
        timestamp_from=timestamp_from,
        timestamp_to=timestamp_to,
        n_regimes=n_regimes,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def line_geometry_stats(
    line_id: str,
    pattern_id: str,
    sample_size: int | None = None,
    sample_pct: float | None = None,
) -> str:
    """Return geometric statistics for one relation line within a pattern.

    sample_size/sample_pct: subsample for large patterns.
    Returns: coverage_pct, edge_distribution, mean_delta_contribution, required flag.
    """
    _require_navigator()
    _effective_sample = sample_size
    if sample_pct is not None and _effective_sample is None:
        sphere = _state["sphere"]._sphere
        pattern = sphere.patterns.get(pattern_id)
        if pattern:
            _effective_sample = pattern.effective_sample_size(sample_pct)
    result = _state["navigator"].line_geometry_stats(
        line_id,
        pattern_id,
        sample_size=_effective_sample,
    )
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def check_alerts(pattern_id: str | None = None) -> str:
    """Evaluate geometric health checks and return alerts (6 built-in checks).

    Use without pattern_id to scan all patterns. Returns alerts sorted by severity (HIGH first).
    """
    _require_navigator()
    result = _state["navigator"].check_alerts(pattern_id)
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def audit_pattern_dims(pattern_id: str, top_k: int = 10) -> str:
    """Per-dim calibration audit of a pattern with recommended action.

    For each dim of the pattern reports the raw population moments (``mu``,
    ``sigma``) plus — when label-aware calibration is available — the
    positive / negative class moments, the Cohen's d separation between
    classes, and the per-dim component of the Fisher LDA direction vector.
    A categorical ``recommended_action`` flags dims that look like they
    should be split, dropped, or investigated further.

    Decision tree (applied in order, first match wins):

    - ``cohens_d_pos_neg < 0.1`` → ``"drop_low_separation"`` — pos/neg
      classes overlap too much for the dim to separate them.
    - ``cohens_d_pos_neg >= 0.1 AND |direction_component| < 0.05`` →
      ``"investigate_drift"`` — raw class stats separate, but the Fisher
      axis says this dim does not carry the label-discriminating signal
      (likely confounded by a stronger dim or a drift artefact).
    - ``cohens_d_pos_neg >= 0.5 AND sigma > 2 * max(sigma_pos, sigma_neg)``
      → ``"split"`` — population sigma is much wider than either class
      sigma, a strong hint the dim is multi-modal.
    - otherwise → ``"keep"``.

    Rows are sorted by ``|cohens_d_pos_neg|`` descending so the most
    label-separating dims surface first; ``top_k`` caps the result.

    When the pattern has no label-aware calibration available (sphere
    built without the YAML ``label_audit:`` block), the tool returns a
    fallback shape — one row per dim with only ``mu`` + ``sigma`` +
    ``recommended_action: "keep"`` — plus a top-level ``reason`` field
    explaining the missing data.

    Tier: ``base`` (manual-mode diagnostic).

    Example invocation::

        audit_pattern_dims(pattern_id="account_pattern", top_k=5)

    Example response (full-field path, abbreviated)::

        {
          "pattern_id": "account_pattern",
          "label_aware_available": true,
          "n_dims_total": 12,
          "n_dims_returned": 5,
          "dims": [
            {
              "dim_label": "risk_score",
              "mu": 0.42,
              "sigma": 0.31,
              "mu_pos": 0.78,
              "sigma_pos": 0.18,
              "mu_neg": 0.21,
              "sigma_neg": 0.15,
              "cohens_d_pos_neg": 3.42,
              "direction_component": 0.62,
              "recommended_action": "keep"
            }
          ]
        }
    """
    _require_navigator()
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns.get(pattern_id)
    if pattern is None:
        return json.dumps(
            {
                "error": f"unknown pattern_id '{pattern_id}'",
                "pattern_id": pattern_id,
            },
            indent=2,
        )
    if top_k < 1:
        return json.dumps(
            {
                "error": "top_k must be >= 1",
                "pattern_id": pattern_id,
                "top_k": top_k,
            },
            indent=2,
        )

    dim_labels = pattern.dim_labels
    mu_vec = np.asarray(pattern.mu, dtype=float)
    sigma_vec = np.asarray(pattern.sigma_diag, dtype=float)
    n_dims = len(dim_labels)

    # ``label_aware_calibration`` is set by the builder hook on patterns
    # listed in the sphere.yaml ``label_audit:`` block. Expected shape:
    # ``{dim_label: obj}`` where ``obj`` exposes ``mu_pos``, ``sigma_pos``,
    # ``mu_neg``, ``sigma_neg``, ``direction`` attributes (matches
    # ``engine.calibration_label_aware.DimCalibration``). The attribute is
    # absent today on patterns built without the block — fallback path.
    lac = getattr(pattern, "label_aware_calibration", None)

    if not lac:
        rows: list[dict[str, Any]] = []
        for i, label in enumerate(dim_labels):
            rows.append({
                "dim_label": label,
                "mu": float(mu_vec[i]) if i < len(mu_vec) else None,
                "sigma": float(sigma_vec[i]) if i < len(sigma_vec) else None,
                "recommended_action": "keep",
            })
        rows = rows[:top_k]
        result = {
            "pattern_id": pattern_id,
            "label_aware_available": False,
            "reason": (
                "no label-aware calibration available — pattern was built "
                "without the 'label_audit:' block in sphere.yaml; only raw "
                "mu/sigma reported"
            ),
            "n_dims_total": n_dims,
            "n_dims_returned": len(rows),
            "dims": rows,
        }
        return json.dumps(_sanitize_for_json(result), indent=2)

    rows = []
    for i, label in enumerate(dim_labels):
        dim_cal = lac.get(label) if isinstance(lac, dict) else None
        if dim_cal is None:
            # Dim listed in pattern.dim_labels but missing from calibration
            # — keep the row, surface only raw stats, mark "keep".
            rows.append({
                "dim_label": label,
                "mu": float(mu_vec[i]) if i < len(mu_vec) else None,
                "sigma": float(sigma_vec[i]) if i < len(sigma_vec) else None,
                "mu_pos": None,
                "sigma_pos": None,
                "mu_neg": None,
                "sigma_neg": None,
                "cohens_d_pos_neg": None,
                "direction_component": None,
                "recommended_action": "keep",
            })
            continue

        mu_pos = float(dim_cal.mu_pos)
        sigma_pos = float(dim_cal.sigma_pos)
        mu_neg = float(dim_cal.mu_neg)
        sigma_neg = float(dim_cal.sigma_neg)
        direction = float(dim_cal.direction)

        # Cohen's d with pooled std. Zero-denominator clamp keeps the
        # action tree deterministic — sigma_pos = sigma_neg = 0 means
        # "no spread anywhere" → falls into ``drop_low_separation``.
        denom_sq = (sigma_pos * sigma_pos + sigma_neg * sigma_neg) / 2.0
        if denom_sq <= 0.0:
            cohens_d = 0.0
        else:
            cohens_d = abs(mu_pos - mu_neg) / math.sqrt(denom_sq)

        sigma_i = float(sigma_vec[i]) if i < len(sigma_vec) else 0.0
        max_class_sigma = max(sigma_pos, sigma_neg)

        if cohens_d < 0.1:
            action = "drop_low_separation"
        elif abs(direction) < 0.05:
            action = "investigate_drift"
        elif cohens_d >= 0.5 and sigma_i > 2.0 * max_class_sigma:
            action = "split"
        else:
            action = "keep"

        rows.append({
            "dim_label": label,
            "mu": float(mu_vec[i]) if i < len(mu_vec) else None,
            "sigma": sigma_i,
            "mu_pos": mu_pos,
            "sigma_pos": sigma_pos,
            "mu_neg": mu_neg,
            "sigma_neg": sigma_neg,
            "cohens_d_pos_neg": cohens_d,
            "direction_component": direction,
            "recommended_action": action,
        })

    # Sort by |cohens_d| desc; rows without a Cohen's d (dim missing from
    # calibration) sort to the end via -1.0 sentinel.
    rows.sort(
        key=lambda r: (
            abs(r["cohens_d_pos_neg"])
            if r["cohens_d_pos_neg"] is not None
            else -1.0
        ),
        reverse=True,
    )
    rows = rows[:top_k]

    result = {
        "pattern_id": pattern_id,
        "label_aware_available": True,
        "n_dims_total": n_dims,
        "n_dims_returned": len(rows),
        "dims": rows,
    }
    return json.dumps(_sanitize_for_json(result), indent=2)
