# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Analysis tools — compare entities, find similar, find common relations."""

from __future__ import annotations

import json

from hypertopos.navigation.navigator import GDSNavigationError

from hypertopos_mcp.enrichment import (
    build_batch_lookups,
    build_entity_lookups,
    resolve_entity_line_id,
)
from hypertopos_mcp.server import _require_navigator, _state, mcp, timed
from hypertopos_mcp.tools._guards import binary_geometry_note_for_pattern, dead_dim_indices


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def compare_entities(
    key_a: str,
    key_b: str,
    pattern_id: str,
    mode: str = "intraclass",
) -> str:
    """Compare two entities geometrically (intraclass=Euclidean, temporal=DTW).

    Returns: distance (lower = more similar) and interpretation.
    """
    _require_navigator()
    nav = _state["navigator"]

    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    if entity_line_id:
        lookups = build_batch_lookups(reader, sphere, {entity_line_id: {key_a, key_b}})
    else:
        lookups = {}

    def _entity_label(key: str) -> dict:
        props = lookups.get(entity_line_id, {}).get(key, {}) if entity_line_id else {}
        d: dict = {"primary_key": key}
        if props:
            d["properties"] = {k: v for k, v in props.items() if v is not None}
        return d

    if mode == "intraclass":
        geo = nav.compare_entities_intraclass(key_a, key_b, pattern_id)
        dist = geo["distance"]
        result = {
            "mode": "intraclass",
            "entity_a": _entity_label(key_a),
            "entity_b": _entity_label(key_b),
            "key_a": key_a,
            "key_b": key_b,
            "pattern_id": pattern_id,
            "distance": round(float(dist), 4),
            "polygon_a": {
                "delta_norm": round(geo["delta_norm_a"], 4),
                "delta_rank_pct": round(geo["delta_rank_pct_a"], 2)
                if geo["delta_rank_pct_a"] is not None
                else None,
                "is_anomaly": geo["is_anomaly_a"],
            },  # noqa: E501
            "polygon_b": {
                "delta_norm": round(geo["delta_norm_b"], 4),
                "delta_rank_pct": round(geo["delta_rank_pct_b"], 2)
                if geo["delta_rank_pct_b"] is not None
                else None,
                "is_anomaly": geo["is_anomaly_b"],
            },  # noqa: E501
            "interpretation": geo["interpretation"],
        }
    elif mode == "temporal":
        temporal = nav.compare_entities_temporal(key_a, key_b, pattern_id)
        result = {
            "mode": "temporal",
            "entity_a": _entity_label(key_a),
            "entity_b": _entity_label(key_b),
            "key_a": key_a,
            "key_b": key_b,
            "pattern_id": pattern_id,
            "distance": temporal["distance"],
            "slices_a": temporal["slices_a"],
            "slices_b": temporal["slices_b"],
            "interpretation": temporal["interpretation"],
        }
    else:
        raise ValueError(f"Unknown mode '{mode}'. Use 'intraclass' or 'temporal'.")

    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_similar_entities(
    primary_key: str,
    pattern_id: str,
    top_n: int = 5,
    filter_expr: str | None = None,
    missing_edge_to: str | None = None,
    dim_mask: list[str] | None = None,
    metric: str = "L2",
) -> str:
    """Find top-N entities geometrically most similar to the given entity.

    filter_expr: Lance SQL predicate (e.g. "is_anomaly = true", "delta_rank_pct > 95").
    missing_edge_to: keep only similar entities with NO edge to this line.
    dim_mask: compute distance only on named dimensions (e.g. ["_d_amount_out_std", "_d_sum_in"]).
    metric: "L2" (Euclidean, default) or "cosine" (shape similarity ignoring magnitude).
    Returns: reference entity metadata + similar entities with distance. Hard cap 50.
    """
    _require_navigator()
    if missing_edge_to:
        sphere = _state["sphere"]._sphere
        pattern = sphere.patterns[pattern_id]
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
    if top_n > _MAX_DRIFT_SIMILAR_TOP_N:
        top_n = _MAX_DRIFT_SIMILAR_TOP_N
    navigator = _state["navigator"]

    # Vectorized similarity search via navigator
    similar_pairs = navigator.find_similar_entities(
        primary_key,
        pattern_id,
        top_n=top_n,
        filter_expr=filter_expr,
        missing_edge_to=missing_edge_to,
        dim_mask=dim_mask,
        metric=metric,
    )

    # Reference metadata from stored geometry (not recomputed — correct for continuous mode)
    ref_meta = navigator.get_entity_geometry_meta(primary_key, pattern_id)

    # Enrich with entity properties
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    if entity_line_id:
        needed_keys = {primary_key} | {bk for bk, _ in similar_pairs}
        lookups = build_batch_lookups(reader, sphere, {entity_line_id: needed_keys})
    else:
        lookups = {}
    entity_lookup = lookups.get(entity_line_id, {}) if entity_line_id else {}

    def _props(key: str) -> dict:
        p = entity_lookup.get(key, {})
        return {k: v for k, v in p.items() if v is not None} if p else {}

    similar_list = [
        {
            "primary_key": bk,
            **({} if not _props(bk) else {"properties": _props(bk)}),
            "distance": round(float(dist), 4),
        }
        for bk, dist in similar_pairs
    ]
    pct = ref_meta["delta_rank_pct"]
    result: dict = {
        "reference": {
            "primary_key": primary_key,
            **({} if not _props(primary_key) else {"properties": _props(primary_key)}),
            "delta_norm": round(float(ref_meta["delta_norm"]), 4),
            "delta_rank_pct": round(float(pct), 2) if pct is not None else None,
            "is_anomaly": ref_meta["is_anomaly"],
        },
        "similar": similar_list,
    }
    if missing_edge_to and len(similar_list) < top_n:
        result["partial_results_warning"] = (
            f"Only {len(similar_list)} of {top_n} requested — "
            f"missing_edge_to='{missing_edge_to}' filtered most candidates."
        )
    if missing_edge_to:
        result["missing_edge_to"] = missing_edge_to
        result["missing_edge_to_note"] = (
            "Filters by geometric edges, not property values."
        )
    if not similar_list and filter_expr:
        note = f"No entities matching filter_expr='{filter_expr}' found near this entity. "
        if not ref_meta["is_anomaly"] and "is_anomaly" in filter_expr.lower():
            note += (
                f"Reference entity is not anomalous (delta_rank_pct={pct:.1f}) "
                "— anomalous entities form a separate geometric cluster. "
            )
        note += "Try a broader filter or call find_anomalies() for global anomaly discovery."
        result["filter_note"] = note
    if getattr(similar_pairs, "degenerate_warning", None):
        result["degenerate_warning"] = similar_pairs.degenerate_warning
        result["population_diversity_note"] = (
            "Many entities share an identical delta vector — this pattern may lack sufficient "
            "structural diversity for ANN search to be meaningful."
        )
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def search_entities_hybrid(
    primary_key: str,
    pattern_id: str,
    query: str,
    alpha: float = 0.7,
    top_n: int = 10,
    filter_expr: str | None = None,
) -> str:
    """Hybrid search fusing ANN vector similarity with BM25 text relevance.

    alpha: weight of vector score (0.0=pure text, 1.0=pure vector, default 0.7).
    query: BM25 text query across all string attributes. Requires INVERTED index.
    filter_expr: optional Lance SQL predicate (e.g. "is_anomaly = true").
    Returns: ranked results with vector_score, text_score, final_score per entity.
    """
    _require_navigator()
    navigator = _state["navigator"]

    alpha = max(0.0, min(1.0, alpha))

    sphere = _state["sphere"]._sphere
    # Anchor line for FTS (and properties enrichment); event line as fallback for FTS only.
    anchor_line_id = resolve_entity_line_id(sphere, pattern_id)
    fts_line_id = anchor_line_id or sphere.event_line(pattern_id)
    if fts_line_id is None:
        raise RuntimeError(f"Cannot resolve line for pattern {pattern_id!r}")

    try:
        _hybrid = navigator.search_hybrid(
            primary_key,
            pattern_id,
            fts_line_id,
            query,
            alpha=alpha,
            top_n=top_n,
            filter_expr=filter_expr,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    raw_results = _hybrid["results"]
    _ann_active = _hybrid["ann_active"]
    _fts_candidates = _hybrid["fts_candidates"]

    reader = _state["session"]._reader
    # Only enrich with properties for anchor lines — event lines can be 1M+ rows.
    prop_line_id = anchor_line_id
    if raw_results and prop_line_id:
        needed_keys = {r["primary_key"] for r in raw_results}
        lookups = build_batch_lookups(reader, sphere, {prop_line_id: needed_keys})
        entity_props = lookups.get(prop_line_id, {})
    else:
        entity_props = {}

    def _props(key: str) -> dict:
        p = entity_props.get(key, {})
        return {k: v for k, v in p.items() if v is not None} if p else {}

    results = [
        {
            "primary_key": r["primary_key"],
            **({} if not _props(r["primary_key"]) else {"properties": _props(r["primary_key"])}),
            "vector_score": r["vector_score"],
            "text_score": r["text_score"],
            "final_score": r["final_score"],
        }
        for r in raw_results
    ]

    output: dict = {
        "primary_key": primary_key,
        "pattern_id": pattern_id,
        "query": query,
        "alpha": alpha,
        "returned": len(results),
        "fts_candidates": _fts_candidates,
        "results": results,
    }

    if results and not _ann_active:
        output["degradation_warning"] = (
            "ANN vector index returned no candidates — vector component is "
            "non-functional. Scores reflect BM25 text ranking only "
            f"(formula: {1 - alpha:.1f} * text_score). Check that the "
            "Lance IVF_FLAT index was built for this pattern "
            "(GDSWriter.build_index_if_needed)."
        )

    return json.dumps(output, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_common_relations(key_a: str, key_b: str, pattern_id: str) -> str:
    """Find shared alive edges between two entities in a pattern.

    Returns: common relations (line_id + point_key), edge counts, interpretation.
    """
    _require_navigator()
    nav = _state["navigator"]
    rel = nav.find_common_relations(key_a, key_b, pattern_id)
    common = rel["common"]

    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    keys_by_line: dict[str, set[str]] = {}
    if entity_line_id:
        keys_by_line[entity_line_id] = {key_a, key_b}
    for line_id, point_key in common:
        if point_key:  # skip continuous-mode edges (point_key == "")
            keys_by_line.setdefault(line_id, set()).add(point_key)
    lookups = build_batch_lookups(reader, sphere, keys_by_line)
    entity_lookup = lookups.get(entity_line_id, {}) if entity_line_id else {}

    def _entity_label(key: str) -> dict:
        props = entity_lookup.get(key, {})
        d: dict = {"primary_key": key}
        if props:
            d["properties"] = {k: v for k, v in props.items() if v is not None}
        return d

    enriched_common = []
    for line_id, point_key in sorted(common):
        entry: dict = {"line_id": line_id, "point_key": point_key}
        edge_props = lookups.get(line_id, {}).get(point_key, {})
        for k, v in edge_props.items():
            if v is not None:
                entry[k] = v
        enriched_common.append(entry)

    continuous_lines = {line_id for line_id, point_key in common if point_key == ""}
    if len(common) == 0:
        interpretation = "no shared relations"
    elif len(continuous_lines) == len(common):
        interpretation = (
            f"share {len(common)} relation type(s) in continuous mode — "
            "no specific entity keys stored; cannot jump_polygon through these edges"
        )
    elif len(continuous_lines) > 0:
        unjumpable = ", ".join(sorted(continuous_lines))
        interpretation = (
            f"share {len(common)} relation(s); {len(continuous_lines)} in continuous mode "
            f"(unjumpable via jump_polygon): {unjumpable}"
        )
    else:
        interpretation = f"share {len(common)} relation(s)"

    result = {
        "entity_a": _entity_label(key_a),
        "entity_b": _entity_label(key_b),
        "key_a": key_a,
        "key_b": key_b,
        "pattern_id": pattern_id,
        "common_count": len(common),
        "common_relations": enriched_common,
        "edges_a": rel["edges_a"],
        "edges_b": rel["edges_b"],
        "interpretation": interpretation,
    }
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_counterparties(
    primary_key: str,
    line_id: str,
    from_col: str,
    to_col: str,
    pattern_id: str | None = None,
    top_n: int = 20,
    use_edge_table: bool = True,
    timestamp_cutoff: float | None = None,
) -> str:
    """Find transaction counterparties of an entity with optional anomaly enrichment.

    from_col/to_col: columns with source/destination entity keys in the event line.
    When pattern_id is given and edge table exists, uses fast BTREE lookup with
    amount_sum/amount_max per counterparty. Set use_edge_table=False to force points scan.
    timestamp_cutoff (Unix seconds): edge-table fast path only — only edges with
    timestamp <= cutoff are considered. Used for as-of graph reconstruction.
    Points-scan fallback silently ignores it.
    Returns: outgoing targets, incoming sources, each with tx_count and optional anomaly status.
    """
    _require_navigator()
    nav = _state["navigator"]

    result = nav.find_counterparties(
        primary_key,
        line_id,
        from_col,
        to_col,
        pattern_id=pattern_id,
        top_n=top_n,
        use_edge_table=use_edge_table,
        timestamp_cutoff=timestamp_cutoff,
    )

    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def entity_flow(
    primary_key: str,
    pattern_id: str,
    top_n: int = 20,
    timestamp_cutoff: float | None = None,
) -> str:
    """Net flow analysis per counterparty via edge table.

    Computes outgoing/incoming totals and per-counterparty net flow.
    Requires event pattern with edge table.
    timestamp_cutoff (Unix seconds): only edges with timestamp <= cutoff
    are considered. Use for as-of flow reconstruction.
    Returns: outgoing_total, incoming_total, net_flow, flow_direction, counterparties sorted by |net_flow|.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.entity_flow(
        primary_key, pattern_id, top_n=top_n,
        timestamp_cutoff=timestamp_cutoff,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def contagion_score(
    primary_key: str,
    pattern_id: str,
    timestamp_cutoff: float | None = None,
) -> str:
    """Score how many of an entity's counterparties are anomalous.

    Requires event pattern with edge table. Score = anomalous/total counterparties (0.0–1.0).
    timestamp_cutoff (Unix seconds): only edges with timestamp <= cutoff are considered.
    Used for as-of graph reconstruction — reproduces contagion state at a prior point in time.
    Returns: score, total_counterparties, anomalous_counterparties, interpretation.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.contagion_score(
        primary_key, pattern_id, timestamp_cutoff=timestamp_cutoff,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def contagion_score_batch(
    primary_keys: list[str],
    pattern_id: str,
    max_keys: int = 200,
    timestamp_cutoff: float | None = None,
) -> str:
    """Contagion score for multiple entities in one call.

    timestamp_cutoff (Unix seconds): forwarded to each per-entity contagion_score.
    Returns per-entity scores plus summary (mean, max, high_contagion_count).
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.contagion_score_batch(
        primary_keys, pattern_id, max_keys=max_keys,
        timestamp_cutoff=timestamp_cutoff,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def degree_velocity(
    primary_key: str,
    pattern_id: str,
    n_buckets: int = 4,
    timestamp_cutoff: float | None = None,
) -> str:
    """Temporal connection velocity — how entity's degree changes over time.

    Buckets edges by timestamp, counts unique counterparties per bucket.
    Velocity = last_bucket_degree / first_bucket_degree.
    Requires event pattern with edge table.
    timestamp_cutoff (Unix seconds): only edges with timestamp <= cutoff are considered;
    the last bucket endpoint is naturally <= cutoff.
    Returns: buckets with out/in degree, velocity_out, velocity_in, interpretation.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.degree_velocity(
        primary_key, pattern_id, n_buckets=n_buckets,
        timestamp_cutoff=timestamp_cutoff,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def investigation_coverage(
    primary_key: str,
    pattern_id: str,
    explored_keys: list[str] | None = None,
) -> str:
    """Agent guidance: how much of an entity's edge neighborhood has been explored.

    Pass explored_keys (list of entity PKs already investigated) to see coverage
    and which unexplored counterparties are anomalous. Helps agents decide where to look next.
    Requires event pattern with edge table.
    """
    _require_navigator()
    nav = _state["navigator"]
    explored_set = set(explored_keys) if explored_keys else set()
    result = nav.investigation_coverage(primary_key, pattern_id, explored_set)
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def propagate_influence(
    seed_keys: list[str],
    pattern_id: str,
    max_depth: int = 3,
    decay: float = 0.7,
    min_threshold: float = 0.001,
    timestamp_cutoff: float | None = None,
) -> str:
    """BFS influence propagation from seed entities with geometric decay.

    At each hop: influence = parent_score * decay * geometric_coherence.
    Use to trace anomaly spread or identify at-risk entities near known bad actors.
    Requires event pattern with edge table.
    timestamp_cutoff (Unix seconds): BFS only follows edges with timestamp <= cutoff.
    Used to reconstruct what influence propagation would have surfaced on a prior date.
    Returns: affected_entities sorted by influence_score, summary with counts.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.propagate_influence(
        seed_keys, pattern_id,
        max_depth=max_depth, decay=decay, min_threshold=min_threshold,
        timestamp_cutoff=timestamp_cutoff,
    )
    # Cap output to top 100
    if len(result["affected_entities"]) > 100:
        result["affected_entities"] = result["affected_entities"][:100]
        result["summary"]["truncated_to"] = 100
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def cluster_bridges(
    pattern_id: str,
    n_clusters: int = 5,
    top_n_bridges: int = 10,
    sample_size: int | None = None,
) -> str:
    """Find entities bridging geometric clusters via edge table.

    Runs π8 clustering then cross-references with edge table to identify entities
    connecting different clusters. Useful for finding structural intermediaries.
    Requires event pattern with edge table.
    sample_size: cap k-means input. Set to 50000 for faster results on large populations.
    Default None = full population (accurate but slower on 500K+ entities).
    Returns: clusters with anomaly rates, bridges with entity details, summary.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.cluster_bridges(
        pattern_id, n_clusters=n_clusters, top_n_bridges=top_n_bridges,
        sample_size=sample_size,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def anomalous_edges(
    from_key: str,
    to_key: str,
    pattern_id: str,
    top_n: int = 10,
) -> str:
    """Find edges between two entities enriched with event-level anomaly scores.

    Unlike path/chain tools which score entities (anchor geometry), this scores
    individual transactions (event geometry). Use to inspect which specific
    transactions between two entities are anomalous.
    Requires event pattern with edge table.
    Returns: edges sorted by delta_norm desc, summary with anomalous count.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.anomalous_edges(from_key, to_key, pattern_id, top_n=top_n)
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_witness_cohort(
    primary_key: str,
    pattern_id: str,
    top_n: int = 10,
    candidate_pool: int = 100,
    min_witness_overlap: float = 0.0,
    min_score: float = 0.0,
    weight_delta: float = 0.40,
    weight_witness: float = 0.30,
    weight_trajectory: float = 0.20,
    weight_anomaly: float = 0.10,
    use_trajectory: bool | None = None,
    bidirectional_check: bool = True,
    edge_pattern_id: str | None = None,
) -> str:
    """Rank entities that share an anchor entity's witness signature.

    Investigative peer ranking — NOT a forecast of future edges. Surfaces
    existing peers sharing the target's anomaly signature, not future
    connections.

    Combines four signals into a composite score in [0, 1]:
    - delta similarity: exp(-distance / theta_norm), absolute and pool-independent
    - witness overlap: Jaccard on witness dimension labels
    - trajectory alignment: cosine on trajectory vectors (optional, [0, 1])
    - anomaly bonus: graded by delta_rank_pct / 100

    Excludes entities already connected via the resolved event pattern's edge
    table — this is the function's main contribution over plain ANN, removing
    legitimate counterparties so the cohort is denser in unknown peers.
    Auto-resolves the edge pattern from the anchor's entity line; pass
    edge_pattern_id explicitly to override when multiple event patterns match.

    Use case: surface non-obvious peers that share the target's anomaly
    signature with explainable per-component scores. Best for fraud cohort
    expansion (find more accounts like this known launderer), not for
    predicting which entities will transact in the future.

    Requires anchor pattern + at least one event pattern with edge table
    covering its entity line. Returns members sorted by score desc, with
    explanation per member and weights_used summary for reproducibility.
    """
    _require_navigator()
    nav = _state["navigator"]
    from hypertopos.navigation.navigator import (
        WitnessCohortConfig,
        WitnessCohortWeights,
    )
    config = WitnessCohortConfig(
        candidate_pool=candidate_pool,
        min_witness_overlap=min_witness_overlap,
        min_score=min_score,
        weights=WitnessCohortWeights(
            delta=weight_delta,
            witness=weight_witness,
            trajectory=weight_trajectory,
            anomaly=weight_anomaly,
        ),
        use_trajectory=use_trajectory,
        bidirectional_check=bidirectional_check,
    )
    result = nav.find_witness_cohort(
        primary_key,
        pattern_id,
        top_n=top_n,
        config=config,
        edge_pattern_id=edge_pattern_id,
    )
    import dataclasses
    return json.dumps(dataclasses.asdict(result), indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_chains_for_entity(
    primary_key: str,
    pattern_id: str,
    top_n: int = 20,
) -> str:
    """Find transaction chains involving a specific entity (requires chain pattern with chain_keys column).

    Returns: chains enriched with is_anomaly, delta_norm, delta_rank_pct.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.find_chains_for_entity(
        primary_key,
        pattern_id,
        top_n=top_n,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_geometric_path(
    from_key: str,
    to_key: str,
    pattern_id: str,
    max_depth: int = 5,
    beam_width: int = 50,
    scoring: str = "geometric",
) -> str:
    """Find paths between two entities scored by geometric coherence.

    Uses bidirectional BFS on the edge table, then scores discovered
    paths by delta-vector coherence. beam_width controls how many
    top-scored paths are returned.

    Scoring modes:
    - geometric: witness overlap + delta alignment + anomaly preservation
    - amount: geometric score modulated by log(transaction amount) — higher = coherent path through high-value transactions
    - anomaly: prefer paths through anomalous entities
    - shortest: plain BFS (no geometric scoring)

    Requires pattern with edge table (event pattern with from/to structure).
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.find_geometric_path(
        from_key, to_key, pattern_id,
        max_depth=max_depth, beam_width=beam_width, scoring=scoring,
    )
    # Cap output: return top 20 paths to avoid token explosion
    total = len(result.get("paths", []))
    if total > 20:
        result["paths"] = result["paths"][:20]
        result["summary"]["paths_truncated_to"] = 20
        result["warning"] = (
            f"Found {total} paths, showing top 20 by score. "
            "Use smaller beam_width or max_depth to reduce results."
        )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def discover_chains(
    primary_key: str,
    pattern_id: str,
    time_window_hours: int = 168,
    max_hops: int = 10,
    min_hops: int = 2,
    max_chains: int = 20,
    direction: str = "forward",
) -> str:
    """Discover transaction chains from entity via runtime temporal BFS.

    Does NOT require pre-built chain lines — works on any event pattern
    with an edge table. For pre-built chain lookups, use find_chains_for_entity.

    Chains are scored by geometric coherence — highest-scored first.
    Use direction="both" for full neighborhood chain analysis.

    Note: total_amount is sum of hop amounts, not tracked money flow.
    See "Chain Interpretation" in concepts docs for details.

    Requires pattern with edge table (event pattern with from/to structure).
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.discover_chains(
        primary_key, pattern_id,
        time_window_hours=time_window_hours, max_hops=max_hops,
        min_hops=min_hops, max_chains=max_chains, direction=direction,
    )
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def edge_stats(
    pattern_id: str,
) -> str:
    """Show edge table statistics for a pattern.

    Returns: row_count, unique_from, unique_to, timestamp_range,
    amount_range, avg_degree. Useful for understanding graph density
    before running path finding or chain discovery.

    Returns null if pattern has no edge table.
    """
    _require_navigator()
    nav = _state["navigator"]
    stats = nav._storage.edge_table_stats(pattern_id)
    if stats is None:
        return json.dumps({
            "pattern_id": pattern_id,
            "has_edge_table": False,
            "hint": "This pattern has no edge table. Rebuild the sphere with edge table support.",
        })
    return json.dumps({
        "pattern_id": pattern_id,
        "has_edge_table": True,
        **stats,
    }, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_novel_entities(
    pattern_id: str,
    top_n: int = 10,
    sample_size: int = 5000,
) -> str:
    """Find entities whose geometry deviates most from their neighbors' expected position.

    High novelty = entity doesn't behave like its neighborhood.
    Requires a pattern with an edge table.
    Returns: list of {primary_key, novelty_score, n_neighbors} sorted by novelty_score descending.
    """
    _require_navigator()
    nav = _state["navigator"]
    try:
        result = nav.find_novel_entities(pattern_id, top_n=top_n, sample_size=sample_size)
    except GDSNavigationError as exc:
        return json.dumps({"error": str(exc), "pattern_id": pattern_id})
    return json.dumps(result, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def contrast_populations(
    pattern_id: str,
    group_a: dict,
    group_b: dict | None = None,
) -> str:
    """Find which dimensions discriminate most between two entity groups (Cohen's d).

    group_a/group_b specs: {"anomaly": bool}, {"keys": [...]}, {"alias": "id", "side": "in"}, or {"edge": {"line_id": ..., "key": ...}}.
    When group_b omitted, complement of group_a is used.
    Returns: dimensions sorted by |effect_size| descending.
    """
    _require_navigator()
    navigator = _state["navigator"]
    try:
        result = navigator.contrast_populations(pattern_id, group_a, group_b)
    except GDSNavigationError as exc:
        return json.dumps(
            {
                "error": str(exc),
                "pattern_id": pattern_id,
                "hint": (
                    "Use group_a={'anomaly': true} or "
                    "group_a={'keys': [...]} for continuous-mode patterns."
                ),
            }
        )
    resp = {
        "pattern_id": pattern_id,
        "group_a_spec": group_a,
        "group_b_spec": group_b,
        "dimensions": result,
        "dead_dimensions": dead_dim_indices(pattern_id),
    }
    return json.dumps(resp, indent=2)


_MAX_HUB_TOP_N = 25  # hard cap — hub polygons ≈1.9K chars each; 25 ≈ 47K total


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_hubs(
    pattern_id: str,
    top_n: int = 10,
    line_id_filter: str | None = None,
    fdr_alpha: float | None = None,
    fdr_method: str = "bh",
    select: str = "top_norm",
) -> str:
    """Find entities with highest geometric connectivity (hub score).

    line_id_filter: rank by a single relation line instead of all.
    fdr_alpha: apply Benjamini-Hochberg FDR control at this level. Returns only entities with q_value <= alpha. Default None = legacy behavior.
    fdr_method: "bh" only in this version. "storey" reserved for future.
    select: "top_norm" (default, rank by score) or "diverse" (submodular facility location — K most diverse representatives with representativeness counts).
    Returns: top_n entities by hub_score desc, mode (continuous/binary), score_stats. Hard cap 25.
    """
    _require_navigator()
    capped_warning: str | None = None
    if top_n > _MAX_HUB_TOP_N:
        capped_warning = (
            f"top_n={top_n} exceeds hard cap {_MAX_HUB_TOP_N} — truncated to avoid "
            "token overflow (~1.9K chars/hub). For hub counts use top_n=1."
        )
        top_n = _MAX_HUB_TOP_N
    navigator = _state["navigator"]

    results, score_stats = navigator.π7_attract_hub_and_stats(
        pattern_id,
        top_n=top_n,
        line_id_filter=line_id_filter,
        fdr_alpha=fdr_alpha,
        fdr_method=fdr_method,
        select=select,
    )

    # Enrich with entity properties
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns[pattern_id]
    max_hub_score = pattern.max_hub_score
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    if entity_line_id:
        top_keys = {bk for bk, _, _, _ in results}
        entity_lookup = build_batch_lookups(reader, sphere, {entity_line_id: top_keys}).get(
            entity_line_id, {}
        )
    else:
        entity_lookup = {}

    enriched = []
    for bk, count, score, hub_pct in results:
        entry: dict = {"key": bk}
        props = entity_lookup.get(bk, {})
        if props:
            entry["properties"] = {k: v for k, v in props.items() if v is not None}
        entry["alive_edges"] = count
        entry["hub_score"] = round(score, 2)
        if hub_pct is not None:
            entry["hub_score_pct"] = hub_pct
        enriched.append(entry)

    output = {
        "pattern_id": pattern_id,
        "mode": "continuous" if pattern.edge_max is not None else "binary",
        "line_id_filter": line_id_filter,
        "top_n": top_n,
        "max_hub_score": max_hub_score,
        "score_stats": score_stats,
        "results": enriched,
    }
    if capped_warning:
        output["capped_warning"] = capped_warning
    return json.dumps(output, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def hub_history(primary_key: str, pattern_id: str) -> str:
    """Show hub score evolution over time for a single entity (continuous patterns only).

    Returns: history[] (oldest-first slices with hub_score) and base_state (final accumulated state).
    """
    _require_navigator()
    navigator = _state["navigator"]
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns[pattern_id]

    history = navigator.hub_score_history(primary_key, pattern_id)

    is_binary = pattern.edge_max is None
    if is_binary:
        base_state = None
    elif history:
        base_state = history.pop()
    else:
        base_state = {}

    payload = {
        "primary_key": primary_key,
        "pattern_id": pattern_id,
        "mode": "continuous" if not is_binary else "binary",
        "history": history,
        "base_state": base_state,
    }
    if is_binary:
        payload["note"] = (
            "hub_history is only meaningful for continuous patterns (edge_max configured). "
            "Binary mode stores no weighted hub scores or base_state."
        )
    return json.dumps(payload, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def get_centroid_map(
    pattern_id: str,
    group_by_line: str,
    group_by_property: str | None = None,
    include_distances: bool = True,
    top_n_distances: int | None = 20,
    sample_size: int | None = None,
    sample_pct: float | None = None,
) -> str:
    """Compute centroids (mean delta vectors) for entity groups within a pattern.

    group_by_line: line whose edges define groups. group_by_property: "line_id:property_name" for property-level grouping.
    include_distances: include inter-centroid pairwise distances (default true). top_n_distances: limit pairs (default 20).
    sample_size/sample_pct: subsample for large patterns (>100K entities).
    Returns: global centroid, per-group centroids with radius/spread/count/centroid_drift, structural outlier.
    """
    _require_navigator()
    _effective_sample = sample_size
    if sample_pct is not None and _effective_sample is None:
        sphere = _state["sphere"]._sphere
        pattern = sphere.patterns.get(pattern_id)
        if pattern:
            _effective_sample = pattern.effective_sample_size(sample_pct)
    navigator = _state["navigator"]
    try:
        result = navigator.centroid_map(
            pattern_id,
            group_by_line,
            group_by_property,
            sample_size=_effective_sample,
        )
    except ValueError as exc:
        error_msg = str(exc)
        resp: dict = {
            "error": error_msg,
            "pattern_id": pattern_id,
            "group_by": group_by_line,
            "hint": (
                "Continuous-mode pattern — cannot group by a related line. "
                "Group the entity's own line by a property instead: "
                "set group_by_line='{entity_line}' and "
                "group_by_property='{entity_line}:<property>'. "
                "See suggested_call and available_properties below."
            ),
        }
        if "continuous mode" in error_msg:
            nav = _state["navigator"]
            _sphere = _state["sphere"]._sphere
            # For event patterns, use the event line itself (e.g. gl_entries);
            # for anchor patterns, use the anchor entity line.
            entity_line_id = _sphere.event_line(pattern_id) or _sphere.entity_line(pattern_id)
            if entity_line_id:
                # suggest_grouping_properties works for anchor patterns;
                # for event patterns fall back to sphere metadata columns.
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
        return json.dumps(resp)

    if not result:
        return json.dumps(
            {
                "pattern_id": pattern_id,
                "group_by": group_by_property if group_by_property else group_by_line,
                "error": "No geometry or no entities with edges to group_by_line.",
            }
        )

    # Enrich group keys with human-readable properties
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    lookups = build_entity_lookups(reader, sphere, {group_by_line})
    dim_props = lookups.get(group_by_line, {})

    for g in result["group_centroids"]:
        props = dim_props.get(g["key"], {})
        if props:
            g["properties"] = {k: v for k, v in props.items() if v is not None}

    # Round vectors for readability
    for g in result["group_centroids"]:
        g["vector"] = [round(v, 6) for v in g["vector"]]
    result["global_centroid"]["vector"] = [round(v, 6) for v in result["global_centroid"]["vector"]]

    # centroid_drift is now computed by navigator.centroid_map (core)
    # Remove member_samples from response if still present
    for g in result.get("group_centroids", []):
        g.pop("member_samples", None)

    result["pattern_id"] = pattern_id
    result["group_by"] = group_by_property if group_by_property else group_by_line
    if group_by_property:
        result["group_by_property"] = group_by_property

    _MAX_CENTROID_DISTANCES = 500
    if not include_distances:
        result.pop("inter_centroid_distances", None)
    else:
        dists = result.get("inter_centroid_distances", [])
        dists_sorted = sorted(dists, key=lambda x: x["distance"])
        if top_n_distances is not None:
            result["inter_centroid_distances"] = dists_sorted[:top_n_distances]
        elif len(dists_sorted) > _MAX_CENTROID_DISTANCES:
            result["inter_centroid_distances"] = dists_sorted[:_MAX_CENTROID_DISTANCES]
            result["distances_truncated_warning"] = (
                f"top_n_distances=None with {len(dists_sorted)} pairs exceeds hard cap "
                f"{_MAX_CENTROID_DISTANCES} — truncated to closest {_MAX_CENTROID_DISTANCES}. "
                "Set top_n_distances=N explicitly or include_distances=False for summary only."
            )
        else:
            result["inter_centroid_distances"] = dists_sorted

    result["dead_dimensions"] = dead_dim_indices(pattern_id)

    return json.dumps(result, indent=2)


_MAX_DRIFT_SIMILAR_TOP_N = 50


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_drifting_similar(
    primary_key: str,
    pattern_id: str,
    top_n: int = 5,
) -> str:
    """Find entities with geometrically similar temporal trajectories (ANN over trajectory vectors).

    Anchor patterns with temporal history only. Hard cap 50.
    Returns: similar entities with distance, displacement, num_slices, timestamps.
    """
    import json as _json

    _require_navigator()
    if top_n > _MAX_DRIFT_SIMILAR_TOP_N:
        top_n = _MAX_DRIFT_SIMILAR_TOP_N
    navigator = _state["navigator"]
    try:
        results = navigator.find_drifting_similar(primary_key, pattern_id, top_n=top_n)
    except ValueError as exc:
        if "insufficient_temporal_history" in str(exc):
            msg = str(exc).split(": ", 1)[1] if ": " in str(exc) else str(exc)
            return _json.dumps({"warning": msg, "results": []})
        raise

    serialized = []
    for r in results:
        row = dict(r)
        for ts_field in ("first_timestamp", "last_timestamp"):
            v = row.get(ts_field)
            if v is not None and hasattr(v, "isoformat"):
                row[ts_field] = v.isoformat()
        serialized.append(row)
    return _json.dumps(serialized, indent=2)


_MAX_DRIFT_TOP_N = 50  # ~0.5K chars/entry; 50 ≈ 25K total
_STALE_SOLID_DAYS = 180  # same threshold as dive_solid in navigation.py


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_drifting_entities(
    pattern_id: str,
    top_n: int = 10,
    sample_size: int | None = None,
    filters: dict | None = None,
    forecast_horizon: int | None = None,
    rank_by_dimension: str | None = None,
    fdr_alpha: float | None = None,
    fdr_method: str = "bh",
    select: str = "top_norm",
) -> str:
    """Find entities with highest geometric drift over time (anchor patterns only).

    filters: {"timestamp_from": "...", "timestamp_to": "..."} to restrict time window.
    forecast_horizon: add drift_forecast per entity (requires >=3 slices).
    rank_by_dimension: rank by drift on a specific dimension instead of total displacement.
    sample_size: recommended for >100K entities to bound latency. Hard cap 50.
    fdr_alpha: apply Benjamini-Hochberg FDR control at this level. Returns only entities with q_value <= alpha. Default None = legacy behavior.
    fdr_method: "bh" only in this version. "storey" reserved for future.
    select: "top_norm" (default, rank by score) or "diverse" (submodular facility location — K most diverse representatives with representativeness counts).
    Returns: per-entity displacement, path_length, ratio, dimension_diffs, tac, reputation.
    """
    _require_navigator()
    capped_warning: str | None = None
    if top_n > _MAX_DRIFT_TOP_N:
        capped_warning = (
            f"top_n={top_n} exceeds hard cap {_MAX_DRIFT_TOP_N} — truncated to avoid "
            f"token overflow. Use sample_size to bound the temporal scan cost."
        )
        top_n = _MAX_DRIFT_TOP_N
    navigator = _state["navigator"]

    attract = navigator.π9_attract_drift
    results = attract(
        pattern_id,
        top_n=top_n,
        sample_size=sample_size,
        filters=filters,
        forecast_horizon=forecast_horizon,
        rank_by_dimension=rank_by_dimension,
        fdr_alpha=fdr_alpha,
        fdr_method=fdr_method,
        select=select,
    )

    # Enrich with entity properties
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    entity_line_id = resolve_entity_line_id(sphere, pattern_id)
    if entity_line_id:
        needed_keys = {entry["primary_key"] for entry in results}
        lookups = build_batch_lookups(reader, sphere, {entity_line_id: needed_keys})
    else:
        lookups = {}
    entity_lookup = lookups.get(entity_line_id, {}) if entity_line_id else {}

    for entry in results:
        props = entity_lookup.get(entry["primary_key"], {})
        if props:
            entry["properties"] = {k: v for k, v in props.items() if v is not None}

    # slice_window_days and drift_forecast are now computed by navigator (core)

    output: dict = {
        "pattern_id": pattern_id,
        "top_n": top_n,
        "sample_size": sample_size,
        "filters": filters,
        "forecast_horizon": forecast_horizon,
        "ranked_by": rank_by_dimension or "displacement",
        "count": len(results),
        "results": results,
    }
    if capped_warning:
        output["capped_warning"] = capped_warning
    if len(results) == 0:
        output["note"] = (
            "No drifting entities found — either this pattern has no temporal deformation slices, "
            "or all entities have fewer than 2 recorded slices (minimum required for displacement "
            "computation)."
        )
    return json.dumps(output, indent=2, default=str)


_MAX_CLUSTER_TOTAL_MEMBERS = 100  # n_clusters × top_n bound; member entry ≈ 0.5K chars


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def find_clusters(
    pattern_id: str,
    n_clusters: int = 5,
    top_n: int = 10,
    sample_size: int | None = None,
) -> str:
    """Discover intrinsic geometric archetypes via k-means++ clustering in delta-space.

    n_clusters: number of clusters (default 5). Set 0 for automatic k detection (slower).
    sample_size: recommended for >100K entities. Hard bound: n_clusters x top_n <= 100 members.
    Returns: clusters sorted by size desc, each with centroid, dim_profile, anomaly_rate, members.
    """
    _require_navigator()
    navigator = _state["navigator"]
    capped_warning: str | None = None
    # n_clusters=0 means auto-k; use safe upper bound of 10 for cap estimation
    _estimated_clusters = n_clusters if n_clusters > 0 else 10
    if _estimated_clusters * top_n > _MAX_CLUSTER_TOTAL_MEMBERS:
        capped_top_n = max(1, _MAX_CLUSTER_TOTAL_MEMBERS // _estimated_clusters)
        capped_warning = (
            f"top_n={top_n} with n_clusters={n_clusters} would produce "
            f"{_estimated_clusters * top_n} total members — capped top_n to {capped_top_n} "
            f"(total members ≤ {_MAX_CLUSTER_TOTAL_MEMBERS}) to avoid token overflow."
        )
        top_n = capped_top_n
    clusters = navigator.π8_attract_cluster(
        pattern_id,
        n_clusters=n_clusters,
        top_n=top_n,
        sample_size=sample_size,
    )

    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    pat = sphere.patterns[pattern_id]
    # Use pattern type to pick the correct entity container line.
    # entity_line() returns anchor-role lines, which for event patterns are
    # dimension lines (e.g. gl_accounts), not the event entry container.
    if pat.pattern_type == "event":
        lookup_line_id: str | None = sphere.event_line(pattern_id)
    else:
        lookup_line_id = resolve_entity_line_id(sphere, pattern_id)

    lookup: dict[str, dict] = {}
    if lookup_line_id:
        needed_keys: set[str] = set()
        for c in clusters:
            needed_keys.add(c["representative_key"])
            needed_keys.update(c["member_keys"])
        lookups = build_batch_lookups(reader, sphere, {lookup_line_id: needed_keys})
        lookup = lookups.get(lookup_line_id, {})

    enriched_clusters = []
    for c in clusters:
        raw_rep = lookup.get(c["representative_key"], {})
        enriched_clusters.append(
            {
                "cluster_id": c["cluster_id"],
                "size": c["size"],
                "anomaly_rate": c["anomaly_rate"],
                "centroid_delta": c["centroid_delta"],
                "delta_norm_mean": c["delta_norm_mean"],
                "delta_norm_std": c["delta_norm_std"],
                "representative_key": c["representative_key"],
                "representative_properties": {k: v for k, v in raw_rep.items() if v is not None},
                "dim_profile": c["dim_profile"],
                "members": [
                    {
                        "key": k,
                        "properties": {
                            kk: vv for kk, vv in lookup.get(k, {}).items() if vv is not None
                        },
                    }
                    for k in c["member_keys"]
                ],
            }
        )

    # Post-processing: enforce total member cap (auto-k can exceed pre-call estimate)
    total_members = sum(len(c["members"]) for c in enriched_clusters)
    if total_members > _MAX_CLUSTER_TOTAL_MEMBERS:
        actual_k = len(enriched_clusters)
        post_cap = max(1, _MAX_CLUSTER_TOTAL_MEMBERS // actual_k)
        for c in enriched_clusters:
            c["members"] = c["members"][:post_cap]
        if not capped_warning:
            capped_warning = (
                f"Auto-k found {actual_k} clusters — total members ({total_members}) "
                f"exceeded {_MAX_CLUSTER_TOTAL_MEMBERS}. Truncated to {post_cap} "
                "members per cluster to avoid token overflow."
            )

    _response: dict = {
        "pattern_id": pattern_id,
        "n_clusters_requested": n_clusters,
        "n_clusters_found": len(clusters),
        "auto_k": n_clusters == 0,
        "sample_size": sample_size,
        "clusters": enriched_clusters,
    }
    if n_clusters > 0 and len(clusters) < n_clusters and not capped_warning:
        _response["capped_warning"] = (
            f"Requested {n_clusters} clusters but only {len(clusters)} were found. "
            "This typically occurs with binary-mode patterns where all entities "
            "share identical delta vectors — the number of distinct geometric states "
            "limits the achievable k."
        )
    if capped_warning:
        _response["capped_warning"] = capped_warning

    _bgn = binary_geometry_note_for_pattern(pattern_id)
    if _bgn:
        _response["binary_geometry_note"] = _bgn

    _response["dead_dimensions"] = dead_dim_indices(pattern_id)

    return json.dumps(_response, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def extract_chains(
    event_pattern_id: str,
    from_col: str,
    to_col: str,
    time_col: str | None = None,
    category_col: str | None = None,
    amount_col: str | None = None,
    time_window_hours: int = 168,
    max_hops: int = 15,
    min_hops: int = 2,
    top_n: int = 20,
    sort_by: str = "hop_count",
    sample_size: int | None = 50000,
    max_chains: int = 100_000,
    seed_nodes: list[str] | None = None,
    bidirectional: bool = False,
) -> str:
    """Extract transaction chains by following from_col->to_col links within a time window.

    from_col/to_col: source/destination entity key columns. category_col/amount_col: optional per-hop tracking.
    seed_nodes: restrict to specific starting entities. bidirectional: treat edges as undirected.
    max_chains: global cap (default 100K). sample_size: limit starting nodes for large datasets.
    Returns: chains with hop_count, is_cyclic, keys, amount_decay. Sorted by sort_by.
    """
    _require_navigator()
    nav = _state["navigator"]
    session = _state["session"]
    reader = session._reader

    sphere = _state["sphere"]._sphere

    # event_pattern_id can be a pattern ID or a line ID — resolve to points table
    if event_pattern_id in sphere.patterns:
        version = nav._resolve_version(event_pattern_id)
        entity_type = sphere.patterns[event_pattern_id].entity_type
        points_table = reader.read_points(entity_type, version)
    elif event_pattern_id in sphere.lines:
        # Direct line ID — read points from that line
        line = sphere.lines[event_pattern_id]
        version = line.versions[-1] if line.versions else 1
        points_table = reader.read_points(event_pattern_id, version)
    else:
        raise ValueError(
            f"'{event_pattern_id}' is neither a pattern nor a line. "
            f"Available patterns: {sorted(sphere.patterns)}, "
            f"lines: {sorted(sphere.lines)}"
        )

    # Validate required columns exist before selection
    schema_names = {col.name for col in points_table.schema}
    for col_name, col_label in [(from_col, "from_col"), (to_col, "to_col")]:
        if col_name not in schema_names:
            return json.dumps(
                {
                    "error": f"{col_label}='{col_name}' not found in line schema. "
                    f"Available columns: {sorted(schema_names)}"
                }
            )

    # Select only needed columns to reduce downstream memory
    needed_cols = ["primary_key", from_col, to_col]
    if time_col and time_col in points_table.schema.names:
        needed_cols.append(time_col)
    if category_col and category_col in points_table.schema.names:
        needed_cols.append(category_col)
    if amount_col and amount_col in points_table.schema.names:
        needed_cols.append(amount_col)
    points_table = points_table.select(needed_cols)

    from_keys = points_table[from_col].to_pylist()
    to_keys = points_table[to_col].to_pylist()
    event_pks = points_table["primary_key"].to_pylist()

    timestamps = None
    if time_col and time_col in points_table.schema.names:
        from hypertopos.engine.chains import parse_timestamps_to_epoch

        ts_raw = points_table[time_col].to_pylist()
        timestamps = parse_timestamps_to_epoch(ts_raw)

    categories = None
    if category_col and category_col in points_table.schema.names:
        categories = points_table[category_col].to_pylist()

    amounts = None
    if amount_col and amount_col in points_table.schema.names:
        amounts = [float(v) if v is not None else 0.0 for v in points_table[amount_col].to_pylist()]

    from hypertopos.engine.chains import extract_chains as _extract

    chains = _extract(
        from_keys=from_keys,
        to_keys=to_keys,
        event_pks=event_pks,
        timestamps=timestamps,
        categories=categories,
        amounts=amounts,
        time_window_hours=time_window_hours,
        max_hops=max_hops,
        min_hops=min_hops,
        sample_size=sample_size,
        max_chains=max_chains,
        seed_nodes=seed_nodes,
        bidirectional=bidirectional,
    )

    # Sort
    if sort_by == "hop_count":
        chains.sort(key=lambda c: c.hop_count, reverse=True)
    elif sort_by == "amount_decay":
        chains.sort(key=lambda c: c.amount_decay)

    # Convert to dicts and truncate
    result_chains = [c.to_dict() for c in chains[:top_n]]

    resp = {
        "event_pattern_id": event_pattern_id,
        "total_chains": len(chains),
        "returned": len(result_chains),
        "sort_by": sort_by,
        "chains": result_chains,
    }

    # Propagate overlap hint from core if present
    if hasattr(chains, "hint") and chains.hint:
        resp["hint"] = chains.hint

    # Summary stats
    if chains:
        import numpy as np

        hops = [c.hop_count for c in chains]
        cyclic_count = sum(1 for c in chains if c.is_cyclic)
        resp["summary"] = {
            "total_chains": len(chains),
            "cyclic_chains": cyclic_count,
            "hop_count_mean": round(float(np.mean(hops)), 1),
            "hop_count_max": max(hops),
        }

    return json.dumps(resp, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def cross_pattern_profile(
    primary_key: str,
    line_id: str | None = None,
) -> str:
    """Multi-source risk profile: anomaly status across all patterns the entity participates in.

    Returns: source_count, risk_score, connected_risk, and per-pattern signal details.
    """
    _require_navigator()
    nav = _state["navigator"]
    profile = nav.cross_pattern_profile(primary_key, line_id=line_id)
    return json.dumps(profile, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def passive_scan(
    home_line_id: str,
    sources: str | None = None,
    scoring: str = "count",
    threshold: int = 2,
    top_n: int = 100,
    include_borderline: bool = False,
    borderline_rank_threshold: int = 80,
) -> str:
    """Batch multi-source anomaly screening — geometry once per pattern.

    home_line_id: anchor line to screen.
    sources: JSON array of source configs or null for auto-discover.
      Each source has a "type" field (default "geometry"):
        geometry — pattern_id, key_type, weight, filter_expr
        borderline — pattern_id, rank_threshold (80), weight
        points — line_id, rules {"col":[">=",0.5]}, combine, wt
        compound — geometry_pattern_id, line_id, rules, combine,
          geometry_key_type, geometry_filter_expr, chain_filter_expr
    scoring: "count" or "weighted". threshold: min score (default 2).
    include_borderline: auto-discover registers borderline sources.
    borderline_rank_threshold: rank pct for borderline (default 80).
    Returns: per-source breakdown with anomaly_intensity.
    """
    _require_navigator()
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    manifest = _state["session"]._manifest

    from hypertopos.navigation.scanner import PassiveScanner

    scanner = PassiveScanner(reader, sphere, manifest)

    if sources:
        import json as _json

        for src in _json.loads(sources):
            src_type = src.get("type", "geometry")
            if src_type == "geometry":
                scanner.add_source(
                    name=src.get("name", src["pattern_id"]),
                    pattern_id=src["pattern_id"],
                    key_type=src.get("key_type"),
                    weight=src.get("weight", 1.0),
                    filter_expr=src.get("filter_expr"),
                )
            elif src_type == "borderline":
                scanner.add_borderline_source(
                    name=src.get("name", f"borderline_{src['pattern_id']}"),
                    pattern_id=src["pattern_id"],
                    rank_threshold=src.get("rank_threshold", 80),
                    weight=src.get("weight", 1.0),
                )
            elif src_type == "points":
                rules_raw = src["rules"]
                rules = {k: tuple(v) for k, v in rules_raw.items()}
                scanner.add_points_source(
                    name=src.get("name", f"points_{src['line_id']}"),
                    line_id=src["line_id"],
                    rules=rules,
                    combine=src.get("combine", "AND"),
                    weight=src.get("weight", 1.0),
                )
            elif src_type == "compound":
                rules_raw = src.get("rules", {})
                rules = {k: tuple(v) for k, v in rules_raw.items()}
                scanner.add_compound_source(
                    name=src.get("name", f"compound_{src['geometry_pattern_id']}"),
                    geometry_pattern_id=src["geometry_pattern_id"],
                    line_id=src["line_id"],
                    rules=rules,
                    combine=src.get("combine", "AND"),
                    geometry_key_type=src.get("geometry_key_type"),
                    geometry_filter_expr=src.get("geometry_filter_expr"),
                    chain_filter_expr=src.get("chain_filter_expr"),
                    weight=src.get("weight", 1.0),
                )
            else:
                raise ValueError(f"Unknown source type: {src_type}")
    else:
        scanner.auto_discover(
            home_line_id,
            include_borderline=include_borderline,
            borderline_rank_threshold=borderline_rank_threshold,
        )

    result = scanner.scan(
        home_line_id,
        scoring=scoring,
        threshold=threshold,
        top_n=top_n,
    )

    resp = {
        "home_line_id": result.home_line_id,
        "scoring": scoring,
        "threshold": threshold,
        "total_entities": result.total_entities,
        "total_flagged": result.total_flagged,
        "sources_summary": result.sources_summary,
        "hits": [
            {
                "primary_key": h.primary_key,
                "score": h.score,
                "weighted_score": h.weighted_score,
                "sources": {
                    k: {
                        "anomalous_count": v.anomalous_count,
                        "related_count": v.related_count,
                        "max_delta_norm": round(v.max_delta_norm, 4),
                        "anomaly_intensity": round(v.anomaly_intensity, 4),
                    }
                    for k, v in h.sources.items()
                },
            }
            for h in result.hits
        ],
    }
    return json.dumps(resp, indent=2, default=str)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def composite_risk(
    primary_key: str,
    line_id: str | None = None,
) -> str:
    """Composite anomaly risk via Fisher's method on conformal p-values across all patterns.

    Returns: combined_p (low = multi-pattern anomaly), per-pattern p-values.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.composite_risk(primary_key, line_id)
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def composite_risk_batch(
    primary_keys: list[str],
    line_id: str | None = None,
) -> str:
    """Batch composite risk (Fisher's method) for multiple entities. Hard cap 200 keys.

    Returns: per-key combined_p and summary counts.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.composite_risk_batch(primary_keys, line_id)
    return json.dumps(result, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def check_anomaly_batch(
    primary_keys: list[str],
    pattern_id: str,
    line_id: str | None = None,
) -> str:
    """Check anomaly status for multiple entities in one call (stateless read). Hard cap 500 keys.

    Returns: is_anomaly and delta_rank_pct per key, anomalous_count, recall_if_all_bad.
    """
    _require_navigator()
    if len(primary_keys) > 500:
        return json.dumps({"error": "Maximum 500 keys per call. Paginate for larger sets."})

    _state["navigator"]
    reader = _state["session"]._reader
    sphere = _state["sphere"]._sphere
    pattern = sphere.patterns[pattern_id]
    version = pattern.version

    # Read geometry for requested keys — uses Lance BTREE index
    geo = reader.read_geometry(
        pattern_id,
        version,
        point_keys=primary_keys,
        columns=["primary_key", "is_anomaly", "delta_rank_pct"],
    )

    # Build result map
    results = []
    found_keys = set()
    for i in range(geo.num_rows):
        pk = geo["primary_key"][i].as_py()
        found_keys.add(pk)
        results.append(
            {
                "primary_key": pk,
                "is_anomaly": bool(geo["is_anomaly"][i].as_py()),
                "delta_rank_pct": round(float(geo["delta_rank_pct"][i].as_py()), 2),
            }
        )

    # Report keys not found in geometry
    missing = [k for k in primary_keys if k not in found_keys]
    anomalous_count = sum(1 for r in results if r["is_anomaly"])

    return json.dumps(
        {
            "pattern_id": pattern_id,
            "total_requested": len(primary_keys),
            "total_found": len(results),
            "anomalous_count": anomalous_count,
            "recall_if_all_bad": round(anomalous_count / max(len(primary_keys), 1), 4),
            "missing_keys": missing[:20] if missing else [],
            "results": results,
        },
        indent=2,
    )


@mcp.tool(annotations={"readOnlyHint": True})
@timed
def explain_anomaly(
    primary_key: str,
    pattern_id: str,
) -> str:
    """Full structured explanation of why an entity is anomalous.

    Returns: severity, witness set, repair set, top dimensions, p-value, temporal context, reputation, composite risk.
    """
    _require_navigator()
    nav = _state["navigator"]
    result = nav.explain_anomaly(primary_key, pattern_id)
    return json.dumps(result, indent=2, default=str)
