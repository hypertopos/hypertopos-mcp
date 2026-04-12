# Changelog

All notable changes to `hypertopos-mcp` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] — 2026-04-12

> **Theme:** downstream effect of the hypertopos 0.3.0 Lance perf upgrade. No new MCP tool surface, no signature changes, no breaking parameter renames. Every tool that calls `aggregate`, `find_anomalies`, `passive_scan`, or `composite_risk` benefits from the new Lance SQL aggregate engine and the precomputed contagion stats fast path under the hood.

### Changed

- Inherits the `pylance` 4.x bump from `hypertopos`. New writes target Lance format 2.2; existing spheres are read transparently.
- `aggregate(...)` and `aggregate_anomalies(...)` route through the new Lance SQL aggregate engine for count / sum / avg / min / max / pivot / group_by_property / percentile / filtered metric paths. The MCP layer is a thin passthrough — no parameter changes.
- `find_anomalies(...)` (the navigator-side fast path that the MCP tool wraps) reads from the Lance scanner via `LanceDataset.sql(...)` instead of forking a subprocess for the top-N + count query.
- `passive_scan(...)` and `composite_risk(...)` use the precomputed `_gds_meta/contagion_stats/{pattern_id}.lance` table instead of replaying the full edge table on every call. Spheres built before 0.3.0 must be rebuilt to get graph contagion hits — the runtime returns zero hits if the precomputed table is missing.

### Migration

- **Rebuild required.** Same as the underlying `hypertopos` 0.3.0 release: spheres built before 0.3.0 are still openable but graph contagion sources contribute zero hits until rebuilt. Rebuild with `hypertopos build sphere.yaml`.

## [0.2.2] — 2026-04-11

### Added

- Optional `timestamp_cutoff: float | None` parameter (Unix seconds) exposed on 6 edge-table MCP tools: `find_counterparties`, `entity_flow`, `contagion_score`, `contagion_score_batch`, `degree_velocity`, `propagate_influence`. When set, only edges with `timestamp <= timestamp_cutoff` are considered — thin passthrough to the matching navigator parameter. Enables as-of graph reconstruction: agents can reproduce contagion, flow, connection velocity, and influence propagation state at a prior point in time without reopening the sphere at a different manifest version.
- `find_counterparties`: documents that `timestamp_cutoff` is honored only on the edge-table fast path (the points-scan fallback has no timestamp column).

### Fixed

- `detect_cross_pattern_discrepancy` no longer triggers full edge-table reads through `PassiveScanner.auto_discover`. The detector measures geometry disagreement between patterns, not graph contagion, so the graph sources that `auto_discover` would otherwise register provide no signal for its downstream single-source hit check — and skipping their registration eliminates a per-event-pattern edge-table scan that previously dominated discrepancy-call latency on multi-pattern spheres.

## [0.2.1] — 2026-04-11

### Added

- `find_witness_cohort` — witness cohort discovery MCP tool. Phase 2 (edge tier). Ranks entities sharing the target's witness signature by combining delta similarity (`exp(-distance/theta)`), witness Jaccard overlap, trajectory cosine, and graded anomaly bonus, excluding already-connected entities via BTREE edge lookup. Available immediately after `open_sphere`. Investigative peer ranking — does NOT forecast future edges.

## [0.2.0] — 2026-04-10

### Added

- `find_geometric_path` — path finding with geometric coherence scoring (+ amount mode)
- `discover_chains` — runtime chain discovery without pre-built chain lines
- `edge_stats` — edge table statistics (row count, degree, timestamp/amount range)
- `entity_flow` — net flow analysis per counterparty
- `contagion_score` — anomaly neighborhood scoring for single entity
- `contagion_score_batch` — batch anomaly neighborhood scoring
- `degree_velocity` — temporal connection velocity
- `investigation_coverage` — agent guidance for investigation coverage
- `propagate_influence` — BFS influence propagation with geometric decay
- `cluster_bridges` — geometry+graph fusion cluster bridge analysis
- `anomalous_edges` — event-level edge scoring between entity pairs
- Output cap (top 20 paths / top 100 influenced) with warning when truncated

### Changed

- `find_counterparties` — edge table fast path with BTREE lookup and amount aggregates when `pattern_id` is given
- `detect_pattern` — edge table tools integrated into smart detection step handlers
- 3-phase visibility updated: Phase 2 now includes 11 edge table tools after `open_sphere`

---

## [0.1.0] — 2026-04-07

First release. 55 MCP tools wrapping hypertopos core library.

### Added

- 3-phase tool visibility (always → gateway → full manual)
- `detect_pattern` meta-tool with 39 step handlers and dependency resolution
- Session management: `open_sphere`, `close_sphere`, `get_session_stats`
- Navigation: goto, walk, jump, dive, emerge, position
- Geometry: polygon, solid, event polygons
- Anomaly detection: find anomalies, summary, batch check, explain
- Similarity & comparison: similar entities, pairwise compare, common relations
- Aggregation with filters, sampling, pivots
- Population analysis: contrast, centroids, clusters, boundary
- Hub & network: hubs, neighborhood, counterparties, chains
- Temporal: solid, hub history, drift, trajectory similarity, time windows, regime changes
- Risk profiling: cross-pattern profile, composite risk, passive scan
- `sphere_overview` gateway to full manual mode

Apache-2.0 licensed.
