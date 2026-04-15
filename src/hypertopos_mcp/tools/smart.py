# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Smart detection — single meta-tool with LLM-planned execution."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from mcp.server.fastmcp import Context

import hypertopos_mcp.server as _srv
from hypertopos_mcp.server import (
    _require_navigator,
    _sample_llm,
    _state,
    mcp,
    timed,
)

# ---------------------------------------------------------------------------
# Step handlers — internal functions, NOT exposed as MCP tools
# ---------------------------------------------------------------------------

def _step_find_anomalies(params: dict) -> dict:
    """Run find_anomalies on a pattern."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    top_n = params.get("top_n", 20)
    polys, total, _, meta = nav.π5_attract_anomaly(pattern_id, top_n=top_n)
    return {
        "total_anomalies": total,
        "returned": len(polys),
        "top_entities": [
            {"key": p.primary_key, "delta_norm": round(p.delta_norm, 3)}
            for p in polys[:10]
        ],
    }


def _step_detect_trajectory(params: dict) -> dict:
    """Run detect_trajectory_anomaly."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    results = nav.detect_trajectory_anomaly(pattern_id, top_n_per_range=params.get("top_n", 20))
    return {"total": len(results), "results": results[:10]}


def _step_detect_segment_shift(params: dict) -> dict:
    """Run detect_segment_shift."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    results = nav.detect_segment_shift(pattern_id, top_n=params.get("top_n", 10))
    return {"total": len(results), "results": results}


def _step_detect_contamination(params: dict) -> dict:
    """Run detect_neighbor_contamination."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    results = nav.detect_neighbor_contamination(
        pattern_id,
        k=params.get("k", 10),
        sample_size=params.get("sample_size", 50),
    )
    return {"total": len(results), "results": results[:10]}


def _step_detect_cross_pattern(params: dict) -> dict:
    """Run detect_cross_pattern_discrepancy."""
    nav = _state["navigator"]
    entity_line = params.get("entity_line", "")
    results = nav.detect_cross_pattern_discrepancy(entity_line, top_n=params.get("top_n", 20))
    return {"total": len(results), "results": results[:10]}


def _step_find_regime_changes(params: dict) -> dict:
    """Run find_regime_changes."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    results = nav.π12_attract_regime_change(pattern_id)
    return {"total": len(results), "results": results[:5]}


def _step_find_hubs(params: dict) -> dict:
    """Run π7_attract_hub on a pattern."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    top_n = params.get("top_n", 10)
    rows = nav.π7_attract_hub(pattern_id, top_n=top_n)
    results = [
        {"primary_key": pk, "alive_edges": cnt, "hub_score": round(score, 3)}
        for pk, cnt, score in rows
    ]
    return {"total": len(results), "results": results[:10]}


def _step_find_clusters(params: dict) -> dict:
    """Run π8_attract_cluster on a pattern."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    clusters = nav.π8_attract_cluster(
        pattern_id,
        n_clusters=params.get("n_clusters", 5),
        top_n=params.get("top_n", 10),
        sample_size=params.get("sample_size", 5000),
    )
    return {"total": len(clusters), "results": clusters[:10]}


def _step_find_drifting_entities(params: dict) -> dict:
    """Run π9_attract_drift on a pattern."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    top_n = params.get("top_n", 10)
    results = nav.π9_attract_drift(pattern_id, top_n=top_n, sample_size=5000)
    return {"total": len(results), "results": results[:10]}


def _step_find_similar_entities(params: dict) -> dict:
    """Run find_similar_entities for a given entity."""
    nav = _state["navigator"]
    primary_key = params.get("primary_key", "")
    pattern_id = params.get("pattern_id", "")
    top_n = params.get("top_n", 5)
    rows = nav.find_similar_entities(primary_key, pattern_id, top_n=top_n)
    results = [
        {"primary_key": pk, "distance": round(dist, 4)}
        for pk, dist in rows
    ]
    out: dict = {"total": len(results), "results": results[:10]}
    if getattr(rows, "degenerate_warning", None):
        out["degenerate_warning"] = rows.degenerate_warning
    return out


def _step_contrast_populations(params: dict) -> dict:
    """Run contrast_populations between two groups."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    group_a = params.get("group_a", {"anomaly": True})
    group_b = params.get("group_b")
    results = nav.contrast_populations(pattern_id, group_a, group_b)
    return {"total": len(results), "results": results[:10]}


def _step_check_anomaly_batch(params: dict) -> dict:
    """Check anomaly status for multiple entities in batch."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    primary_keys = params.get("primary_keys", [])
    if not primary_keys:
        return {"total": 0, "results": []}

    result = nav.check_anomaly_batch(pattern_id, primary_keys)
    result["results"] = result["results"][:10]
    return result


def _step_explain_anomaly(params: dict) -> dict:
    """Run explain_anomaly for a single entity."""
    nav = _state["navigator"]
    primary_key = params.get("primary_key", "")
    pattern_id = params.get("pattern_id", "")
    result = nav.explain_anomaly(primary_key, pattern_id)
    return {"total": 1, "results": [result]}


def _step_cross_pattern_profile(params: dict) -> dict:
    """Run cross_pattern_profile for a single entity."""
    nav = _state["navigator"]
    primary_key = params.get("primary_key", "")
    line_id = params.get("line_id")
    result = nav.cross_pattern_profile(primary_key, line_id=line_id)
    return {"total": 1, "results": [result]}


def _step_passive_scan(params: dict) -> dict:
    """Run passive multi-source anomaly screening."""
    nav = _state["navigator"]
    home_line_id = params.get("home_line_id", params.get("pattern_id", ""))
    threshold = params.get("threshold", 2)
    top_n = params.get("top_n", 100)

    result = nav.passive_scan(home_line_id, threshold=threshold, top_n=top_n)
    return {"total": result["total_flagged"], "results": result["hits"][:10]}


def _step_composite_risk(params: dict) -> dict:
    """Run composite_risk (Fisher's method) for a single entity."""
    nav = _state["navigator"]
    primary_key = params.get("primary_key", "")
    line_id = params.get("line_id")
    result = nav.composite_risk(primary_key, line_id=line_id)
    return {"total": 1, "results": [result]}


def _step_composite_risk_batch(params: dict) -> dict:
    """Run composite_risk_batch for multiple entities."""
    nav = _state["navigator"]
    primary_keys = params.get("primary_keys", [])
    line_id = params.get("line_id")
    if not primary_keys:
        return {"total": 0, "results": []}
    result = nav.composite_risk_batch(primary_keys, line_id=line_id)
    batch_results = result.get("results", [])
    return {"total": len(batch_results), "results": batch_results[:10]}


# ---------------------------------------------------------------------------
# Phase 1B: Aggregation handler
# ---------------------------------------------------------------------------

def _step_aggregate(params: dict) -> dict:
    """Run aggregate on an event pattern."""
    nav = _state["navigator"]
    event_pattern_id = params.get("event_pattern_id", params.get("pattern_id", ""))
    group_by_line = params.get("group_by_line", "")
    result = nav.aggregate(
        event_pattern_id,
        group_by_line,
        metric=params.get("metric", "count"),
        limit=params.get("limit", 10),
        geometry_filters=params.get("geometry_filters"),
        time_from=params.get("time_from"),
        time_to=params.get("time_to"),
    )
    groups = result.get("groups", [])
    return {
        "total_groups": result.get("total_groups", 0),
        "total_eligible": result.get("total_eligible", 0),
        "results": groups[:10],
    }


# ---------------------------------------------------------------------------
# Phase 1C: Observability handlers
# ---------------------------------------------------------------------------

def _step_sphere_overview(params: dict) -> dict:
    """Run sphere_overview for population health snapshot."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id")
    result = nav.sphere_overview(pattern_id)
    return {"total": len(result), "results": result}


def _step_check_alerts(params: dict) -> dict:
    """Run check_alerts for geometric health checks."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id")
    result = nav.check_alerts(pattern_id)
    alerts = result.get("alerts", []) if isinstance(result, dict) else result
    return {"total": len(alerts), "results": alerts[:10]}


def _step_detect_data_quality(params: dict) -> dict:
    """Run detect_data_quality_issues on a pattern."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    findings = nav.detect_data_quality_issues(pattern_id)
    return {"total": len(findings), "results": findings}


def _step_anomaly_summary(params: dict) -> dict:
    """Run anomaly_summary for population anomaly statistics."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    result = nav.anomaly_summary(pattern_id)
    return {
        "total_entities": result.get("total_entities", 0),
        "total_anomalies": result.get("total_anomalies", 0),
        "anomaly_rate": result.get("anomaly_rate", 0),
        "top_driving_dimensions": result.get(
            "top_driving_dimensions", [],
        )[:5],
        "total_clusters": result.get("total_clusters", 0),
    }


def _step_aggregate_anomalies(params: dict) -> dict:
    """Run aggregate_anomalies — group anomalous entities by property."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    group_by = params.get("group_by", "")
    result = nav.aggregate_anomalies(
        pattern_id,
        group_by=group_by,
        top_n=params.get("top_n", 20),
    )
    groups = result.get("groups", [])
    return {
        "total_anomalies": result.get("total_anomalies", 0),
        "anomaly_rate": result.get("anomaly_rate", 0),
        "results": groups[:10],
    }


# ---------------------------------------------------------------------------
# Phase 1D: Temporal handlers
# ---------------------------------------------------------------------------

def _step_compare_time_windows(params: dict) -> dict:
    """Run compare_time_windows between two time periods."""
    nav = _state["navigator"]
    pattern_id = params.get("pattern_id", "")
    result = nav.π11_attract_population_compare(
        pattern_id,
        params.get("window_a_from", ""),
        params.get("window_a_to", ""),
        params.get("window_b_from", ""),
        params.get("window_b_to", ""),
    )
    return {"total": 1, "results": [result]}


def _step_find_drifting_similar(params: dict) -> dict:
    """Run find_drifting_similar — entities with similar trajectories."""
    nav = _state["navigator"]
    primary_key = params.get("primary_key", "")
    pattern_id = params.get("pattern_id", "")
    top_n = params.get("top_n", 5)
    results = nav.find_drifting_similar(primary_key, pattern_id, top_n=top_n)
    return {"total": len(results), "results": results[:10]}


def _step_hub_history(params: dict) -> dict:
    """Run hub_score_history for temporal hub evolution."""
    nav = _state["navigator"]
    primary_key = params.get("primary_key", "")
    pattern_id = params.get("pattern_id", "")
    history = nav.hub_score_history(primary_key, pattern_id)
    return {"total": len(history), "results": history[:10]}


# ---------------------------------------------------------------------------
# Phase 1F: Network/Fraud handlers
# ---------------------------------------------------------------------------

def _step_find_counterparties(params: dict) -> dict:
    """Run find_counterparties — transaction partners with anomaly enrichment."""
    nav = _state["navigator"]
    result = nav.find_counterparties(
        params.get("primary_key", ""),
        params.get("line_id", ""),
        params.get("from_col", ""),
        params.get("to_col", ""),
        pattern_id=params.get("pattern_id"),
        top_n=params.get("top_n", 20),
    )
    total = len(result.get("outgoing", [])) + len(result.get("incoming", []))
    return {"total": total, "results": [result]}


def _step_extract_chains(params: dict) -> dict:
    """Run extract_chains — find transaction chains in event pattern."""
    nav = _state["navigator"]
    result = nav.extract_chains(
        event_pattern_id=params.get("event_pattern_id", ""),
        from_col=params.get("from_col", ""),
        to_col=params.get("to_col", ""),
        top_n=params.get("top_n", 10),
        sample_size=params.get("sample_size", 50000),
    )
    chains = result.get("chains", [])
    return {"total": len(chains), "results": chains[:10]}


def _step_find_chains_for_entity(params: dict) -> dict:
    """Run find_chains_for_entity — chains involving a specific entity."""
    nav = _state["navigator"]
    result = nav.find_chains_for_entity(
        params.get("primary_key", ""),
        params.get("pattern_id", ""),
        top_n=params.get("top_n", 20),
    )
    chains = result.get("chains", [])
    return {"total": len(chains), "results": chains[:10]}


def _step_find_common_relations(params: dict) -> dict:
    """Run find_common_relations — shared edges between two entities."""
    nav = _state["navigator"]
    result = nav.find_common_relations(
        params.get("key_a", ""),
        params.get("key_b", ""),
        params.get("pattern_id", ""),
    )
    common = result.get("common", set())
    return {
        "total": len(common),
        "edges_a": result.get("edges_a", 0),
        "edges_b": result.get("edges_b", 0),
        "results": [
            {"line_id": lid, "point_key": k} for lid, k in list(common)[:10]
        ],
    }


# ---------------------------------------------------------------------------
# Phase 1G: Population Analytics handlers
# ---------------------------------------------------------------------------

def _step_get_centroid_map(params: dict) -> dict:
    """Run centroid_map — group centroids in delta-space."""
    nav = _state["navigator"]
    result = nav.centroid_map(
        params.get("pattern_id", ""),
        params.get("group_by_line", ""),
        group_by_property=params.get("group_by_property"),
        sample_size=params.get("sample_size"),
    )
    centroids = result.get("group_centroids", [])
    return {"total": len(centroids), "results": centroids[:10]}


def _step_attract_boundary(params: dict) -> dict:
    """Run attract_boundary — entities near alias cutting plane."""
    nav = _state["navigator"]
    rows = nav.π6_attract_boundary(
        params.get("alias_id", ""),
        params.get("pattern_id", ""),
        direction=params.get("direction", "both"),
        top_n=params.get("top_n", 10),
    )
    results = [
        {
            "primary_key": poly.primary_key,
            "signed_distance": round(dist, 4),
            "is_anomaly": poly.is_anomaly,
        }
        for poly, dist in rows
    ]
    return {"total": len(results), "results": results[:10]}


# ---------------------------------------------------------------------------
# Smart-exclusive: New detection algorithms (smart-mode exclusive)
# ---------------------------------------------------------------------------

def _step_assess_false_positive(params: dict) -> dict:
    """Assess anomaly stability via theta perturbation."""
    nav = _state["navigator"]
    result = nav.assess_false_positive(
        params.get("primary_key", ""),
        params.get("pattern_id", ""),
        n_perturbations=params.get("n_perturbations", 20),
    )
    return {"total": 1, "results": [result]}


def _step_detect_event_rate_anomaly(params: dict) -> dict:
    """Find entities with high event anomaly rate but normal static geometry."""
    nav = _state["navigator"]
    results = nav.detect_event_rate_anomaly(
        params.get("pattern_id", ""),
        threshold=params.get("threshold", 0.15),
        top_n=params.get("top_n", 20),
    )
    return {"total": len(results), "results": results[:10]}


def _step_explain_anomaly_chain(params: dict) -> dict:
    """Trace multi-hop anomaly root cause chain."""
    nav = _state["navigator"]
    chain = nav.explain_anomaly_chain(
        params.get("primary_key", ""),
        params.get("pattern_id", ""),
        max_hops=params.get("max_hops", 3),
    )
    return {"total": len(chain), "results": chain}


def _step_detect_hub_anomaly_concentration(params: dict) -> dict:
    """Find hubs where most neighbors are anomalous."""
    nav = _state["navigator"]
    results = nav.detect_hub_anomaly_concentration(
        params.get("pattern_id", ""),
        top_n=params.get("top_n", 20),
        min_anomaly_ratio=params.get("min_anomaly_ratio", 0.5),
    )
    return {"total": len(results), "results": results[:10]}


def _step_detect_composite_subgroup_inflation(params: dict) -> dict:
    """Find subgroups with elevated composite risk vs population."""
    nav = _state["navigator"]
    results = nav.detect_composite_subgroup_inflation(
        params.get("entity_line", ""),
        params.get("group_by", ""),
        top_n=params.get("top_n", 10),
    )
    return {"total": len(results), "results": results[:10]}


def _step_detect_collective_drift(params: dict) -> dict:
    """Find clusters of entities that drifted in the same direction."""
    nav = _state["navigator"]
    results = nav.detect_collective_drift(
        params.get("pattern_id", ""),
        top_n=params.get("top_n", 50),
        n_clusters=params.get("n_clusters", 5),
        sample_size=params.get("sample_size", 5000),
    )
    return {"total": len(results), "results": results[:10]}


def _step_detect_temporal_burst(params: dict) -> dict:
    """Find entities with sudden event frequency spikes."""
    nav = _state["navigator"]
    results = nav.detect_temporal_burst(
        params.get("pattern_id", ""),
        top_n=params.get("top_n", 20),
        z_threshold=params.get("z_threshold", 3.0),
    )
    return {"total": len(results), "results": results[:10]}


_STEP_HANDLERS: dict[str, Any] = {
    "find_anomalies": _step_find_anomalies,
    "detect_trajectory_anomaly": _step_detect_trajectory,
    "detect_segment_shift": _step_detect_segment_shift,
    "detect_neighbor_contamination": _step_detect_contamination,
    "detect_cross_pattern_discrepancy": _step_detect_cross_pattern,
    "find_regime_changes": _step_find_regime_changes,
    "find_hubs": _step_find_hubs,
    "find_clusters": _step_find_clusters,
    "find_drifting_entities": _step_find_drifting_entities,
    "find_similar_entities": _step_find_similar_entities,
    "contrast_populations": _step_contrast_populations,
    "check_anomaly_batch": _step_check_anomaly_batch,
    "explain_anomaly": _step_explain_anomaly,
    "cross_pattern_profile": _step_cross_pattern_profile,
    "passive_scan": _step_passive_scan,
    "composite_risk": _step_composite_risk,
    "composite_risk_batch": _step_composite_risk_batch,
    # Phase 1B: Aggregation
    "aggregate": _step_aggregate,
    # Phase 1C: Observability
    "sphere_overview": _step_sphere_overview,
    "check_alerts": _step_check_alerts,
    "detect_data_quality": _step_detect_data_quality,
    "anomaly_summary": _step_anomaly_summary,
    "aggregate_anomalies": _step_aggregate_anomalies,
    # Phase 1D: Temporal
    "compare_time_windows": _step_compare_time_windows,
    "find_drifting_similar": _step_find_drifting_similar,
    "hub_history": _step_hub_history,
    # Phase 1F: Network/Fraud
    "find_counterparties": _step_find_counterparties,
    "extract_chains": _step_extract_chains,
    "find_chains_for_entity": _step_find_chains_for_entity,
    "find_common_relations": _step_find_common_relations,
    # Phase 1G: Population Analytics
    "get_centroid_map": _step_get_centroid_map,
    "attract_boundary": _step_attract_boundary,
    # Smart-exclusive: New detection algorithms (smart-mode exclusive)
    "assess_false_positive": _step_assess_false_positive,
    "detect_event_rate_anomaly": _step_detect_event_rate_anomaly,
    "explain_anomaly_chain": _step_explain_anomaly_chain,
    "detect_hub_anomaly_concentration": _step_detect_hub_anomaly_concentration,
    "detect_composite_subgroup_inflation": _step_detect_composite_subgroup_inflation,
    "detect_collective_drift": _step_detect_collective_drift,
    "detect_temporal_burst": _step_detect_temporal_burst,
}

# Step capability requirements
_STEP_CAPABILITIES: dict[str, str | None] = {
    "find_anomalies": None,
    "detect_trajectory_anomaly": "has_trajectory_index",
    "detect_segment_shift": None,
    "detect_neighbor_contamination": None,
    "detect_cross_pattern_discrepancy": "multi_pattern",
    "find_regime_changes": "has_temporal",
    # Phase 1A: Analysis
    "find_hubs": None,
    "find_clusters": None,
    "find_drifting_entities": "has_temporal",
    "find_similar_entities": None,
    "contrast_populations": None,
    "check_anomaly_batch": None,
    "explain_anomaly": None,
    "cross_pattern_profile": "multi_pattern",
    "passive_scan": "multi_pattern",
    # Phase 1E: Composite Risk
    "composite_risk": "multi_pattern",
    "composite_risk_batch": "multi_pattern",
    # Phase 1B: Aggregation
    "aggregate": None,
    # Phase 1C: Observability
    "sphere_overview": None,
    "check_alerts": None,
    "detect_data_quality": None,
    "anomaly_summary": None,
    "aggregate_anomalies": None,
    # Phase 1D: Temporal
    "compare_time_windows": "has_temporal",
    "find_drifting_similar": "has_trajectory_index",
    "hub_history": "has_temporal",
    # Phase 1F: Network/Fraud
    "find_counterparties": None,
    "extract_chains": None,
    "find_chains_for_entity": None,
    "find_common_relations": None,
    # Phase 1G: Population Analytics
    "get_centroid_map": None,
    "attract_boundary": None,
    # Smart-exclusive: New detection algorithms (smart-mode exclusive)
    "assess_false_positive": None,
    "detect_event_rate_anomaly": None,
    "explain_anomaly_chain": None,
    "detect_hub_anomaly_concentration": None,
    "detect_composite_subgroup_inflation": "multi_pattern",
    "detect_collective_drift": "has_temporal",
    "detect_temporal_burst": None,
}


def _available_steps() -> list[str]:
    """Return step names available given current sphere capabilities."""
    caps = _srv._sphere_capabilities or {}
    available = []
    for name, req in _STEP_CAPABILITIES.items():
        if req is None or caps.get(req, False):
            available.append(name)
    return available


# ---------------------------------------------------------------------------
# Meta-tool
# ---------------------------------------------------------------------------

@mcp.tool(annotations={"readOnlyHint": True})
@timed
async def detect_pattern(query: str, ctx: Context) -> str:
    """Detect patterns in sphere data using natural language.

    Automatically selects detection steps based on sphere capabilities.
    Executes server-side (no round-trips). For manual drill-down, use
    individual tools after sphere_overview().

    query: describe WHAT to find as a full sentence. Include entity type
      and dimension name when possible. The more specific, the better.
      Good: "find zones with anomalously low trip_count in zone_activity"
      Good: "suppliers with high fare_std compared to population"
      Bad: "ghost zones" (too vague — no entity type or dimension)
      Bad: "anomalies" (too broad — specify what kind)

    Returns: plan, results per step, follow_up suggestions, interpretation.
    """
    _require_navigator()
    caps = _srv._sphere_capabilities or {}
    available = _available_steps()

    # Build sphere context for planning
    sphere = _state["sphere"]._sphere
    patterns_info = {
        pid: {
            "type": p.pattern_type,
            "entity_line": sphere.entity_line(pid),
            "dimensions": [
                r.line_id.replace("_d_", "").replace("_", " ")
                for r in p.relations
            ],
            "dimension_ids": [r.line_id for r in p.relations],
        }
        for pid, p in sphere.patterns.items()
    }

    # Phase 1: LLM plans execution (or fallback to keyword matching)
    planning_prompt = (
        f"Sphere capabilities: {json.dumps(caps)}\n"
        f"Patterns: {json.dumps(patterns_info)}\n"
        f"Available steps: {available}\n"
        f"User query: {query}\n\n"
        f"Return a JSON execution plan:\n"
        f'{{"steps": [{{"name": "step_name", "params": {{"pattern_id": "...", "top_n": 20}}}}], '
        f'"rationale": "why these steps"}}\n'
        f"Only use steps from the available list. Match pattern_ids from the patterns list."
    )

    # Enrich prompt with investigation hints from sphere_overview (if available)
    hints = _state.get("investigation_hints")
    if hints:
        planning_prompt += f"\nInvestigation hints from sphere overview: {hints}\n"

    plan = None
    if ctx is not None:
        plan_text = await _sample_llm(
            ctx, planning_prompt,
            system_prompt="You are a GDS query planner. Return only valid JSON, no markdown.",
            max_tokens=300,
            temperature=0.1,
        )
        if plan_text:
            try:
                plan = json.loads(plan_text)
            except json.JSONDecodeError:
                plan = None

    # Fallback: simple keyword matching if sampling unavailable
    if plan is None:
        plan = _fallback_plan(query, available, patterns_info)

    # Phase 2: Execute steps — independent ones in parallel, dependent ones after
    from concurrent.futures import ThreadPoolExecutor, as_completed

    steps = plan.get("steps", [])
    step_results: dict[str, dict] = {}

    # Partition into independent (no depends_on) and dependent steps
    independent: list[dict] = []
    dependent: list[dict] = []
    for step_spec in steps:
        spec = step_spec if isinstance(step_spec, dict) else {"name": step_spec}
        if spec.get("depends_on"):
            dependent.append(spec)
        else:
            independent.append(spec)

    # Execute independent steps in parallel (handlers are sync, Lance releases GIL)
    def _run_step(spec: dict) -> tuple[str, dict]:
        name = spec["name"]
        params = spec.get("params", {})
        handler = _STEP_HANDLERS.get(name)
        if handler is None:
            return name, {"error": f"unknown step: {name}"}
        try:
            return name, handler(params)
        except Exception as exc:
            return name, {"error": str(exc)}

    if independent:
        max_workers = min(len(independent), 3)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_run_step, s) for s in independent]
            for f in as_completed(futures):
                name, result = f.result()
                step_results[name] = result

    # Execute dependent steps sequentially (need prior results)
    for step_spec in dependent:
        step_name = step_spec["name"]
        params = step_spec.get("params", {})
        handler = _STEP_HANDLERS.get(step_name)
        if handler is None:
            continue
        depends_on = step_spec.get("depends_on")
        if depends_on and depends_on in step_results:
            params = _resolve_dependency(params, step_spec, step_results[depends_on])
        try:
            step_results[step_name] = handler(params)
        except Exception as exc:
            step_results[step_name] = {"error": str(exc)}

    # Track coverage for session-level awareness
    explored = _state.get("explored_steps") or {}
    for step_name, result in step_results.items():
        pid = next(
            (s.get("params", {}).get("pattern_id", "")
             for s in plan.get("steps", [])
             if s.get("name") == step_name),
            "",
        )
        explored[f"{pid}/{step_name}"] = bool(
            isinstance(result, dict) and "error" not in result
        )
    _state["explored_steps"] = explored

    # Auto-retry: if all steps returned 0 results, try find_anomalies
    # on the best-matched pattern as fallback
    _total = sum(
        r.get("total", r.get("total_anomalies", 0))
        for r in step_results.values()
        if isinstance(r, dict) and "error" not in r
    )
    if _total == 0 and "find_anomalies" not in step_results:
        # Get pattern from first step in plan
        first_step = plan.get("steps", [{}])[0] if plan.get("steps") else {}
        retry_pattern = first_step.get("params", {}).get("pattern_id", "")
        if retry_pattern:
            try:
                retry_result = _STEP_HANDLERS["find_anomalies"](
                    {"pattern_id": retry_pattern, "top_n": 10},
                )
                step_results["find_anomalies_retry"] = retry_result
            except Exception:
                pass

    # Phase 3a: LLM filters false positives (if sampling available)
    filtered_results = None
    if ctx is not None and step_results:
        # Collect all entity keys from results for filtering
        candidates = []
        for step_name, result in step_results.items():
            for item in result.get("results", result.get("top_entities", [])):
                if isinstance(item, dict):
                    key = item.get("key", item.get("primary_key", item.get("entity_key", "")))
                    if key:
                        candidates.append({"key": key, "source": step_name, **item})

        if candidates:
            filter_text = await _sample_llm(
                ctx,
                f"Query: {query}\n"
                f"Sphere domain: {_state['sphere']._sphere.name}\n"
                f"Candidates ({len(candidates)}):\n"
                f"{json.dumps(candidates[:30], default=str)}\n\n"
                f"Return JSON: {{\"filtered\": [{{\"key\": \"...\", "
                f"\"confidence\": 0.0-1.0, \"reason\": \"...\"}}]}}\n"
                f"Keep only entities that genuinely match the query. "
                f"Exclude noise and normal variance.",
                system_prompt=(
                    "You are a data analyst filtering detection results. "
                    "Return only valid JSON."
                ),
                max_tokens=500,
                temperature=0.0,
            )
            if filter_text:
                with contextlib.suppress(json.JSONDecodeError, AttributeError):
                    filtered_results = json.loads(filter_text).get("filtered")

    # Phase 3b: LLM interpretation
    interpretation = None
    if ctx is not None and step_results:
        interp_text = await _sample_llm(
            ctx,
            f"Query: {query}\nResults: {json.dumps(step_results, default=str)}\n"
            f"Provide a concise interpretation (3-5 sentences).",
            system_prompt="You are a data analyst interpreting geometric detection results.",
            max_tokens=400,
            temperature=0.0,
        )
        interpretation = interp_text

    # Build follow-up suggestions based on sphere structure + what was NOT explored
    executed = set(step_results.keys())
    follow_up: list[str] = []

    # Proactive: temporal cascade — if regime change found, suggest drilling
    regime_result = step_results.get("find_regime_changes", {})
    if isinstance(regime_result, dict) and regime_result.get("total", 0) > 0:
        follow_up.insert(0,
            "Regime change detected! Try: detect_pattern('find entities that "
            "drifted the most') to identify which entities drove the shift"
        )
    ctw_result = step_results.get("compare_time_windows", {})
    if isinstance(ctw_result, dict) and ctw_result.get("total", 0) > 0:
        follow_up.insert(0,
            "Temporal shift detected! Try: detect_pattern('which segments "
            "shifted anomaly rates') to find which groups changed"
        )

    # Proactive: surface profiling alerts as high-priority targets
    inv_hints = _state.get("investigation_hints") or []
    for h in inv_hints:
        if "extreme" in h.lower() or "alert" in h.lower():
            follow_up.append(f"Profiling alert: {h}")
    if caps.get("has_temporal") and not executed & {
        "compare_time_windows", "find_drifting_entities",
    }:
        follow_up.append(
            "Try: detect_pattern('compare first week vs last week')"
            " for temporal window comparison"
        )
    if "find_clusters" not in executed:
        follow_up.append(
            "Try: detect_pattern('what archetypes exist')"
            " for population clustering"
        )
    if "detect_segment_shift" not in executed:
        follow_up.append(
            "Try: detect_pattern('which segments shifted')"
            " for segment anomaly rates"
        )
    # List available dimensions for targeted queries
    dim_examples: list[str] = []
    for pid, info in patterns_info.items():
        for dim in info.get("dimensions", [])[:3]:
            dim_examples.append(f"'{dim}' in {pid}")
    if dim_examples:
        follow_up.append(
            "For dimension-specific detection, mention a dimension name: "
            + ", ".join(dim_examples[:5])
        )

    # Zero-results hint: explain what happened and suggest rephrase
    total_found = sum(
        r.get("total", r.get("total_anomalies", 0))
        for r in step_results.values()
        if isinstance(r, dict) and "error" not in r
    )
    zero_hint = None
    if total_found == 0 and step_results:
        step_names = [s["name"] for s in plan.get("steps", [])]
        pids = [
            s["params"].get("pattern_id", "?")
            for s in plan.get("steps", [])
        ]
        zero_hint = (
            f"0 results. Plan ran {step_names} on {pids}. "
            f"Try rephrasing with a specific dimension name or entity type. "
            f"Available dimensions: "
            + ", ".join(
                f"'{d}' ({pid})"
                for pid, info in patterns_info.items()
                for d in info.get("dimensions", [])[:2]
            )
        )

    # Step summaries: concise per-step digest for agent reasoning
    summaries: dict[str, str] = {}
    for sname, sr in step_results.items():
        if not isinstance(sr, dict) or "error" in sr:
            summaries[sname] = f"error: {sr.get('error', '?')}" if isinstance(sr, dict) else "error"
            continue
        total = sr.get("total", sr.get("total_anomalies", sr.get("total_entities", 0)))
        top_keys = []
        for item in sr.get("results", sr.get("top_entities", []))[:3]:
            if isinstance(item, dict):
                k = item.get("key", item.get("primary_key", item.get("entity_key", "")))
                if k:
                    top_keys.append(str(k))
        summaries[sname] = f"{total} found" + (f", top: {', '.join(top_keys)}" if top_keys else "")

    # Coverage: what has been explored across all detect_pattern calls
    explored = _state.get("explored_steps") or {}
    all_combos = {
        f"{pid}/{step}"
        for pid in patterns_info
        for step in ("find_anomalies", "detect_segment_shift", "find_clusters")
        if patterns_info[pid]["type"] == "anchor"
    }
    not_explored = sorted(all_combos - set(explored.keys()))

    return json.dumps({
        "query": query,
        "capabilities": caps,
        "plan": plan,
        "results": step_results,
        "summaries": summaries,
        "filtered_results": filtered_results,
        "interpretation": interpretation,
        "zero_results_hint": zero_hint,
        "follow_up": follow_up if follow_up else None,
        "coverage": {
            "explored": list(explored.keys()),
            "not_yet_explored": not_explored[:10],
        } if not_explored else None,
    }, indent=2, default=str)


def _resolve_dependency(
    params: dict, step_spec: dict, prior_result: dict,
) -> dict:
    """Inject a value from a prior step's result into current step params.

    Reads ``input_key`` from prior_result (supports dotted path + [N] indexing)
    and writes it into ``param_target`` in params.
    """
    input_key = step_spec.get("input_key", "")
    param_target = step_spec.get("param_target", "")
    if not input_key or not param_target:
        return params

    # Walk the dotted path: "top_entities[0].key" → prior["top_entities"][0]["key"]
    value: Any = prior_result
    for part in input_key.replace("[", ".[").split("."):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            if isinstance(value, list) and idx < len(value):
                value = value[idx]
            else:
                return params  # index out of range — skip injection
        elif isinstance(value, dict):
            value = value.get(part, None)
            if value is None:
                return params
        else:
            return params

    params = {**params, param_target: value}
    return params


# ---------------------------------------------------------------------------
# Investigation Templates — multi-step fallback when sampling unavailable
# ---------------------------------------------------------------------------

_INVESTIGATION_TEMPLATES: dict[str, list[dict]] = {
    "full_scan": [
        {"name": "sphere_overview", "params": {}},
        {"name": "find_anomalies", "params": {"top_n": 10}},
    ],
    "detect_and_explain": [
        {"name": "find_anomalies", "params": {"top_n": 5}},
        {
            "name": "explain_anomaly",
            "params": {},
            "depends_on": "find_anomalies",
            "input_key": "top_entities[0].key",
            "param_target": "primary_key",
        },
    ],
    "segment_investigation": [
        {"name": "detect_segment_shift", "params": {}},
        {"name": "anomaly_summary", "params": {}},
    ],
    "temporal_investigation": [
        {"name": "find_drifting_entities", "params": {"top_n": 10}},
        {"name": "find_regime_changes", "params": {}},
    ],
    "contamination_analysis": [
        {"name": "detect_neighbor_contamination", "params": {}},
        {"name": "find_hubs", "params": {"top_n": 10}},
    ],
    "fraud_network": [
        {"name": "passive_scan", "params": {}},
    ],
    "population_profile": [
        {"name": "anomaly_summary", "params": {}},
        {"name": "find_clusters", "params": {"n_clusters": 5}},
    ],
}


def _select_template(
    query: str, available: list[str],
) -> list[dict] | None:
    """Pick the best investigation template based on query keywords."""
    q = query.lower()
    _template_kw = {
        "detect_and_explain": ("explain why", "root cause"),
        "segment_investigation": ("segment shift", "segment anomal"),
        "temporal_investigation": ("temporal drift", "drift and regime"),
        "contamination_analysis": ("contamination analysis", "surround"),
        "fraud_network": ("fraud ring", "fraud network", "aml"),
        "population_profile": ("population profile", "archetype"),
    }
    for template_name, keywords in _template_kw.items():
        if any(w in q for w in keywords):
            steps = _INVESTIGATION_TEMPLATES[template_name]
            # Check all steps are available
            if all(
                s["name"] in available or s["name"] == "find_anomalies"
                for s in steps
            ):
                return steps
    return None


def _match_patterns(
    query_lower: str,
    anchor_patterns: list[str],
    patterns_info: dict,
) -> list[str]:
    """Return anchor patterns matching the query, best-scored first.

    Scores each pattern by substring matches against its pattern_id and
    entity_line. Returns ALL patterns with score > 0, sorted desc.
    Falls back to [first anchor pattern] if nothing matches.
    """
    if not anchor_patterns:
        return [""]
    scored: list[tuple[int, str]] = []
    for pid in anchor_patterns:
        score = 0
        info = patterns_info.get(pid, {})
        entity_line = info.get("entity_line", "")
        for part in pid.replace("_", " ").split():
            if len(part) > 2 and part in query_lower:
                score += 2
        for part in entity_line.replace("_", " ").split():
            if len(part) > 2 and part in query_lower:
                score += 3
        if score > 0:
            scored.append((score, pid))
    if not scored:
        return [anchor_patterns[0]]
    scored.sort(key=lambda x: -x[0])
    # Cap at 3 patterns to avoid over-scanning
    return [pid for _, pid in scored[:3]]


def _get_temporal_range(
    sphere: object, pattern_id: str,
) -> dict[str, str] | None:
    """Extract temporal date range from temporal Lance dataset.

    Reads unique timestamps, keeps only the densest cluster (mode month),
    returns {"start": ISO, "mid": ISO, "end": ISO} or None.
    """
    try:
        storage = _state["session"]._reader
        batches = storage.read_temporal_batched(pattern_id)
        first_batch = next(batches, None)
        if first_batch is None:
            return None
        from collections import Counter

        import pyarrow as pa
        table = pa.Table.from_batches([first_batch])
        if "timestamp" not in table.schema.names:
            return None
        timestamps = sorted(set(table["timestamp"].to_pylist()))
        if len(timestamps) < 3:
            return None
        # Keep only timestamps in the most common month (filters outlier dates)
        month_counts = Counter(t.strftime("%Y-%m") for t in timestamps)
        mode_month = month_counts.most_common(1)[0][0]
        filtered = [t for t in timestamps if t.strftime("%Y-%m") == mode_month]
        if len(filtered) < 3:
            filtered = timestamps
        first = filtered[0].strftime("%Y-%m-%d")
        last = filtered[-1].strftime("%Y-%m-%d")
        boundary = filtered[-2].strftime("%Y-%m-%d")
        return {"start": first, "mid": boundary, "end": last}
    except Exception:
        return None


def _match_dimension(
    query_lower: str,
    matched_patterns: list[str],
    patterns_info: dict,
) -> dict[str, str] | None:
    """Match query words to pattern dimension names.

    Returns {"pattern_id": ..., "dimension": ..., "dimension_id": ...}
    if a dimension name is found in the query, else None.
    """
    import re
    query_words = set(re.findall(r"\w+", query_lower))
    best: dict[str, str] | None = None
    best_score = 0
    for pid in matched_patterns:
        info = patterns_info.get(pid, {})
        dims = info.get("dimensions", [])
        dim_ids = info.get("dimension_ids", [])
        for dim_name, dim_id in zip(dims, dim_ids, strict=False):
            dim_words = set(dim_name.split())
            # Count exact word overlap (not substring)
            overlap = query_words & dim_words
            score = sum(len(w) for w in overlap if len(w) > 3)
            if score > best_score:
                best_score = score
                best = {
                    "pattern_id": pid,
                    "dimension": dim_name,
                    "dimension_id": dim_id,
                }
    return best


def _fallback_plan(
    query: str,
    available: list[str],
    patterns_info: dict,
) -> dict:
    """Keyword-based plan with investigation templates when sampling unavailable."""
    query_lower = query.lower()
    steps = []

    # Pick best-matching anchor pattern(s) from query
    anchor_patterns = [
        pid for pid, info in patterns_info.items() if info["type"] == "anchor"
    ]
    matched = _match_patterns(query_lower, anchor_patterns, patterns_info)
    default_pattern = matched[0]

    # Use investigation hints to boost step selection
    hints = _state.get("investigation_hints") or []
    hint_steps: set[str] = set()
    for h in hints:
        if "detect_trajectory_anomaly" in h:
            hint_steps.add("detect_trajectory_anomaly")
        if "find_drifting_entities" in h:
            hint_steps.add("find_drifting_entities")
        if "find_regime_changes" in h:
            hint_steps.add("find_regime_changes")
        if "detect_cross_pattern_discrepancy" in h:
            hint_steps.add("detect_cross_pattern_discrepancy")
        if "detect_segment_shift" in h:
            hint_steps.add("detect_segment_shift")
        if "detect_neighbor_contamination" in h:
            hint_steps.add("detect_neighbor_contamination")

    # Dimension-aware param enrichment: match query words to dimension names
    matched_dim = _match_dimension(query_lower, matched, patterns_info)

    # Try investigation templates (multi-step chains) — don't early-return,
    # continue to keyword matching so compound queries get both template + keywords
    template_steps = _select_template(query, available)
    if template_steps:
        for ts in template_steps:
            ts_params = {**ts.get("params", {}), "pattern_id": default_pattern}
            steps.append({**ts, "params": ts_params})

    _kw = {
        # Detection
        (
            "trajectory", "trajectories", "arch", "v-shape", "non-monotonic",
        ): "detect_trajectory_anomaly",
        ("segment shift", "shifted", "region shift", "category shift"): "detect_segment_shift",
        ("neighbor", "contamination", "surround"): "detect_neighbor_contamination",
        (
            "cross-pattern", "cross pattern", "discrepancy", "disagreement",
            "one pattern but normal", "normal in another",
        ): "detect_cross_pattern_discrepancy",
        ("regime", "changepoint", "structural"): "find_regime_changes",
        # Analysis
        ("hub score", "most connected", "connectivity"): "find_hubs",
        ("cluster", "archetype", "k-means"): "find_clusters",
        ("drift", "changed", "moved", "evolution", "recently"): "find_drifting_entities",
        ("similar", "nearest", "closest"): "find_similar_entities",
        ("compare", "contrast", "discriminat"): "contrast_populations",
        ("explain", "why anomal", "root cause", "driver"): "explain_anomaly",
        ("composite risk", "multi-source", "risk screening"): "passive_scan",
        # Observability
        ("overview", "health", "population status"): "sphere_overview",
        ("alert", "health check", "warning"): "check_alerts",
        ("data quality", "sparse", "ghost"): "detect_data_quality",
        ("anomaly rate", "anomaly distribution"): "anomaly_summary",
        (
            "anomalies by", "group anomal", "subgroup", "grouped by",
            "anomaly rate per", "misuse", "per category", "per type",
        ): "aggregate_anomalies",
        # Aggregation
        (
            "aggregate", "count by", "count event", "sum by", "average by",
            "weekday", "weekend", "day of week", "hourly",
        ): "aggregate",
        # Temporal
        (
            "time window", "period", "year over year", "this year", "last year",
        ): "compare_time_windows",
        ("hub evolution", "hub over time", "hub history", "hub score evol"): "hub_history",
        # Network/Fraud
        ("counterpart", "trading partner", "transact"): "find_counterparties",
        ("chain", "flow", "launder", "layering"): "extract_chains",
        ("common relation", "shared relation", "in common"): "find_common_relations",
        # Population Analytics
        ("centroid", "group geometry", "mean delta"): "get_centroid_map",
        ("boundary", "cutting plane"): "attract_boundary",
        # Smart-exclusive: New detection algorithms
        ("false positive", "borderline", "stability"): "assess_false_positive",
        ("event rate", "event anomaly", "high rate normal"): "detect_event_rate_anomaly",
        ("anomaly chain", "root cause chain", "multi-hop"): "explain_anomaly_chain",
        ("hub anomal", "hub concentrat", "neighbors anomalous"): "detect_hub_anomaly_concentration",
        ("subgroup inflat", "composite subgroup"): "detect_composite_subgroup_inflation",
        ("collective drift", "coordinated", "drifted together"): "detect_collective_drift",
        ("burst", "frequency spike", "event spike"): "detect_temporal_burst",
    }
    # Tools that require entity-specific params (primary_key, from_col, etc.)
    # that keyword fallback cannot provide — skip them to avoid crashes with
    # empty-string params. attract_boundary is intentionally NOT here: it
    # needs alias_id, which the post-loop alias-special-case below supplies
    # automatically by picking the first sphere alias. Listing it here would
    # never fire on alias-equipped spheres anyway, only on alias-less ones
    # where the special-case is silent.
    _NEEDS_ENTITY_CONTEXT = {
        "find_counterparties", "extract_chains", "find_common_relations",
        "get_centroid_map", "assess_false_positive",
        "explain_anomaly_chain", "detect_composite_subgroup_inflation",
        "hub_history",
    }
    # Steps that only work on event patterns (temporal event streams)
    _REQUIRES_EVENT_PATTERN = {
        "detect_temporal_burst", "detect_event_rate_anomaly",
        "aggregate_anomalies", "aggregate",
    }
    # Steps that only work on anchor patterns (entity geometry + temporal)
    _REQUIRES_ANCHOR_PATTERN = {
        "detect_trajectory_anomaly", "find_drifting_entities",
        "find_regime_changes", "detect_segment_shift",
        "detect_neighbor_contamination", "detect_collective_drift",
        "find_similar_entities", "find_clusters", "find_hubs",
        "contrast_populations", "compare_time_windows",
    }
    for keywords, step_name in _kw.items():
        if step_name not in available:
            continue
        if not any(w in query_lower for w in keywords):
            continue
        if step_name in _NEEDS_ENTITY_CONTEXT:
            continue
        if step_name == "detect_cross_pattern_discrepancy":
            # Cross-pattern needs entity_line — use all matched patterns' lines
            seen_lines: set[str] = set()
            for pid in matched:
                el = patterns_info.get(pid, {}).get("entity_line", "")
                if el and el not in seen_lines:
                    seen_lines.add(el)
                    steps.append({"name": step_name, "params": {"entity_line": el}})
        elif step_name == "attract_boundary":
            # attract_boundary needs alias_id — pick the first sphere alias as a
            # representative starting point. If the sphere has no aliases the
            # tool is unusable in keyword fallback, so skip silently.
            _sphere = _state["sphere"]._sphere if _state.get("sphere") else None
            if _sphere and _sphere.aliases:
                for aid, alias in _sphere.aliases.items():
                    steps.append({
                        "name": "attract_boundary",
                        "params": {
                            "alias_id": aid,
                            "pattern_id": alias.base_pattern_id,
                        },
                    })
                    break  # first alias only
        else:
            # Run on all matched patterns (multi-pattern scan)
            for pid in matched:
                ptype = patterns_info.get(pid, {}).get("type", "")
                if step_name in _REQUIRES_EVENT_PATTERN and ptype != "event":
                    continue
                if step_name in _REQUIRES_ANCHOR_PATTERN and ptype != "anchor":
                    continue
                params: dict[str, Any] = {"pattern_id": pid}
                # Dimension-aware: if query matches a dimension, rank by it
                if matched_dim and matched_dim["pattern_id"] == pid:
                    dim_id = matched_dim["dimension_id"]
                    if step_name == "find_anomalies":
                        params["rank_by_property"] = dim_id
                steps.append({"name": step_name, "params": params})

    # Default: dimension-aware scan → hints → blind anomaly scan
    if not steps:
        if matched_dim:
            # Dimension matched but no keyword → targeted anomaly scan
            steps.append({
                "name": "find_anomalies",
                "params": {
                    "pattern_id": matched_dim["pattern_id"],
                    "top_n": 20,
                    "rank_by_property": matched_dim["dimension_id"],
                },
            })
        else:
            hint_added = False
            dp_type = patterns_info.get(default_pattern, {}).get("type", "")
            for hs in hint_steps:
                if hs not in available:
                    continue
                if hs in _REQUIRES_EVENT_PATTERN and dp_type != "event":
                    continue
                if hs in _REQUIRES_ANCHOR_PATTERN and dp_type != "anchor":
                    continue
                if hs == "detect_cross_pattern_discrepancy":
                    el = patterns_info.get(default_pattern, {}).get(
                        "entity_line", "",
                    )
                    steps.append({"name": hs, "params": {"entity_line": el}})
                else:
                    steps.append({
                        "name": hs,
                        "params": {"pattern_id": default_pattern},
                    })
                hint_added = True
            if not hint_added:
                steps.append({
                    "name": "find_anomalies",
                    "params": {"pattern_id": default_pattern, "top_n": 20},
                })

    # Capability-aware bonus: auto-add steps for available capabilities
    # that aren't already covered
    step_names = {s["name"] for s in steps}
    caps = _srv._sphere_capabilities or {}

    # Temporal: regime changes + auto time window comparison
    if caps.get("has_temporal") and not step_names & {
        "find_regime_changes", "find_drifting_entities",
        "compare_time_windows", "detect_collective_drift",
    }:
        steps.append({
            "name": "find_regime_changes",
            "params": {"pattern_id": default_pattern},
        })
        # Auto compare_time_windows: first week vs last week from temporal data
        _sph = _state["sphere"]._sphere if _state.get("sphere") else None
        if _sph and "compare_time_windows" in available:
            _temporal_range = _get_temporal_range(_sph, default_pattern)
            if _temporal_range:
                mid = _temporal_range["mid"]
                steps.append({
                    "name": "compare_time_windows",
                    "params": {
                        "pattern_id": default_pattern,
                        "window_a_from": _temporal_range["start"],
                        "window_a_to": mid,
                        "window_b_from": mid,
                        "window_b_to": _temporal_range["end"],
                    },
                })

    # Cross-pattern: discrepancy detection — only when query explicitly mentions it
    # (this step is expensive: runs full passive_scan + per-entity cross_pattern_profile)
    _cross_kw = {"cross-pattern", "discrepancy", "multi-pattern", "inconsistent", "contradicting"}
    if (
        caps.get("multi_pattern")
        and any(kw in query_lower for kw in _cross_kw)
        and not step_names & {"detect_cross_pattern_discrepancy", "passive_scan"}
    ):
        el = patterns_info.get(default_pattern, {}).get("entity_line", "")
        steps.append({
            "name": "detect_cross_pattern_discrepancy",
            "params": {"entity_line": el},
        })

    # Alias boundary fallback: if sphere has aliases and the keyword loop did
    # not already add attract_boundary (no boundary/cutting-plane keywords in
    # the query), add one as a default exploratory step. The keyword path
    # above handles the explicit-keyword case.
    _sphere = _state["sphere"]._sphere if _state.get("sphere") else None
    if (
        _sphere and _sphere.aliases
        and "attract_boundary" not in step_names
        and "attract_boundary" in available
    ):
        for aid, alias in _sphere.aliases.items():
            steps.append({
                "name": "attract_boundary",
                "params": {
                    "alias_id": aid,
                    "pattern_id": alias.base_pattern_id,
                },
            })
            break  # first alias only

    rationale = (
        "keyword-based fallback with investigation hints"
        if hint_steps
        else "keyword-based fallback (sampling unavailable)"
    )
    return {"steps": steps, "rationale": rationale}
