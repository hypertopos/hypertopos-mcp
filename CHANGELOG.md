# Changelog

All notable changes to `hypertopos-mcp` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
