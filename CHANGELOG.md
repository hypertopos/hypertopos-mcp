# Changelog

All notable changes to `hypertopos-mcp` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.5.2] — 2026-04-28

### Added
- `score_motif` and `find_high_potential_motifs` recognise two new motif types: `split_recombine` (diamond scatter-gather S → k intermediaries → D with stacked-bipartite temporal order) and `bipartite_burst` (complete K_{k,m} bipartite subgraph within a tight window). `score_motif(motif_type="split_recombine", direction="backward")` anchors the seed at the sink and enumerates backward; `direction="forward"` (default) anchors at the source. `bipartite_burst` accepts `min_k` and `min_m` for asymmetric density thresholds on the source and sink sides. Docstrings and `docs/tools.md` updated with per-motif typology atoms, parameter semantics, and seed anchoring. See hypertopos-py CHANGELOG for the canonical definitions.

### Changed
- score_motif and find_high_potential_motifs inherit the hypertopos-py chain_k adaptive frontier cap. No MCP-layer changes — speedup observed transparently through the existing tool surface. tools.md chain_k section gains a Performance paragraph explaining the per-k cap behaviour and `frontier_truncated` expectations.
- score_motif and trace_root_cause's motif_potential branch inherit the hypertopos-py fixes for per-edge scoring and uniform single-seed enumeration latency. No MCP-layer changes — the speedup is observed transparently through the existing tool surface.
- score_motif and find_high_potential_motifs inherit the hypertopos-py cycle_3 pre-filter speedup. No MCP-layer changes — speedup observed transparently through the existing tool surface.

## [0.5.1] — 2026-04-21

### Added
- `score_motif` and `find_high_potential_motifs` recognise two new motif types: `fan_in` (sink-centric mirror of `fan_out`) and `chain_k` (open directed chain of parametric length 3 ≤ k ≤ 8). New `k` parameter on both tools (default 4, chain_k only). Both tools pass through the new `log_score`, `score_clamped` numeric-stability fields and the `frontier_truncated` flag on `chain_k` results (see hypertopos-py CHANGELOG). `score_motif` additionally gains a `min_k: int | None = None` override — forwards to the single-seed `fan_out` / `fan_in` enumerators so agents can check "is this hub / sink connected to ≥ N distinct counterparties?" without the pattern-wide cold cache on `find_high_potential_motifs`. Extended docstrings and `docs/tools.md` tables cover the new types, parameters, and return fields.
- Large-motif response truncation on `score_motif` and `find_high_potential_motifs`. When a motif instance carries more than 50 edges, `edges` and `breakdown` are capped at the top 50 contributors by `edge_potential` DESC; `edges_total_count` reports the original count, `edges_truncated` / `breakdown_truncated` flag the truncation, and `breakdown_summary` surfaces `count`, `mean`, `std`, `min`, `max`, `p25`, `p50`, `p75`, `p95` of `edge_potential` over the full edge set so the agent sees the distribution even when only the top 50 are materialised. Rationale: pre-fix, a fan_in hub with ~500 sources produced ~200k-char responses that overflowed the MCP token limit and got spilled to a file instead of returned inline. `count` in the `find_high_potential_motifs` envelope counts motif instances and is unaffected.
- MCP wire format for motif tools is now strict RFC 8259 JSON. Non-finite float values (including `log_score = -inf` on motifs where a `saw_zero` edge collapses the product) are rendered as JSON `null` instead of the non-standard `Infinity` / `-Infinity` / `NaN` literals that Python's default `json.dumps` emits. Affects `score_motif`, `find_high_potential_motifs`, and (defensively) `trace_root_cause`. Consumers read `log_score == null` as "score degenerate / not finite"; strict parsers (browser `JSON.parse`, many non-Python MCP clients) no longer reject these payloads.

### Fixed
- `get_event_polygons` parameter renamed from `event_pattern_id` to `pattern_id` for consistency with all other polygon tools.
- `detect_cross_pattern_discrepancy` and `detect_segment_shift` return a `diagnostic` field explaining why results are empty, instead of a silent empty response.
- `detect_trajectory_anomaly` accepts a `sample_size` parameter to cap entity streaming on large patterns.
- `find_counterparties` smart step resolves `pattern_id` from `event_pattern_id` alias, enabling the edge-table fast path.
- `anomaly_confidence` is omitted from polygon output when bootstrap confidence was not computed for the pattern (previously emitted as `0.0`, which could be misread as a computed score).
- Downstream of the hypertopos-py single-seed window-filter fix: `score_motif(..., motif_type in {"fan_out", "cycle_2", "cycle_3"}, time_window_hours=H)` now enforces the declared window — previously silently disabled in production because of a microseconds-vs-seconds unit mismatch. Behaviour change on three of six motif types; consult the hypertopos-py CHANGELOG for the full rationale.

## [0.5.0] — 2026-04-19

### Added
- `find_anomalies` (and `pi5_attract_anomaly`) gain `fdr_method="storey"` — routes through the new Storey LSL estimator in `hypertopos` core. Default remains `"bh"`.
- `p_value_method` parameter on the same tools (`"rank"` default, `"chi2"` opt-in). `"chi2"` is required for `fdr_method="storey"` to actually shrink q-values — rank p-values are uniform by construction and defeat the Storey estimator. Power recovery is regime-dependent: moderate super-anomaly patterns (e.g. NYC Taxi trips) gain 10–15% discoveries; over-compressed or extreme patterns get zero uplift.
- `find_drifting_entities` returns include `gradient_alignment` and `drift_direction` (source: hypertopos core). No parameter changes.
- `trace_root_cause` MCP tool (top-level + smart-mode step) surfaces the new `GDSNavigator.trace_root_cause` DAG — one call replaces the manual `explain_anomaly → find_counterparties → contagion_score → π7 hub` investigation chain. Smart dispatcher keywords: `"root cause"`, `"why anomalous"`, `"trace anomaly"`, `"anomaly chain"`, `"multi-hop"`. Tool exposes `hub_pop_limit`, `contagion_min_threshold`, `max_total_nodes` knobs for tuning the branch-selection behaviour per sphere.
- `score_edge(from_key, to_key, pattern_id)` — per-edge geometric anomaly score.
- `find_high_potential_edges(pattern_id, top_n, from_key, to_key, min_pair_count)` — rank edges by geometric edge potential. Hard cap top_n=100. Smart-mode keyword triggers: `"suspicious edge"`, `"rare pair"`, `"edge anomaly"`, `"geometric edge"`.
- `score_motif(entity_key, motif_type, pattern_id, time_window_hours=None, amt1_min=10000.0, amt2_max=10000.0)` — score the best structural motif seeded at an entity. Valid `motif_type` values: `fan_out` (hub → k targets), `cycle_2` (A↔B round-trip), `cycle_3` (A→B→C→A triad with strict temporal ordering), `structuring` (open A→B→C→D chain with hop1 ≥ amt1_min and hops 2,3 ≤ amt2_max, default 10000 USD reporting threshold — overridable per jurisdiction). Scoring is product-of-edge_potential across motif edges.
- `find_high_potential_motifs(pattern_id, motif_type, top_n, time_window_hours, seeds, min_k, amt1_min, amt2_max)` — rank motifs of a given type across the pattern. `motif_type` includes the new `structuring` (open A→B→C→D chain with amount gating, default 1h window) alongside `fan_out`, `cycle_2`, `cycle_3`. Hard cap top_n=100. First call per (pattern, motif_type, window, amt1_min, amt2_max) is cold (30–90s on patterns with >500k entities); subsequent calls hit an LRU cache capped at 8 (structuring amount thresholds are part of the cache key so changing them triggers recompute). Motif ranking enumerates off the shared `AdjacencyIndex` cache used by every other graph primitive (`find_counterparties`, `entity_flow`, `contagion_score`, `discover_chains`, `anomalous_edges`, `find_geometric_path`, `detect_network_novelty`, `score_edge`, `find_high_potential_edges`) — the adjacency build cost is paid once per pattern per session and reused across all of them, so a typical `find_anomalies` → `find_counterparties` → `find_high_potential_motifs` flow only pays it on the first step. Smart-mode keyword triggers: `"fan out"`, `"fan-out"`, `"concentrator"`, `"round trip"`, `"round-trip"`, `"bidirectional burst"`, `"flash burst"`, `"triad"`, `"three-party cycle"`, `"round-tripping 3"`, `"laundering ring"`, `"closed loop"`, `"motif"`, `"structural pattern"`, `"subgraph pattern"`, `"structuring"`, `"smurfing"`, `"split transfer"`, `"deposit split"`, `"reporting threshold"`.

### Removed
- `explain_anomaly_chain` smart-mode step — superseded by `trace_root_cause`.

## [0.4.1] — 2026-04-16

### Fixed
- `find_geometric_path` docstring corrected: beam search → bidirectional BFS to match actual algorithm.

## [0.4.0] — 2026-04-15

### Added
- `anomaly_confidence` and `bregman_divergence` fields in `find_anomalies`, `get_polygon`, `passive_scan` responses.
- `min_confidence` parameter on `find_anomalies` — filter by bootstrap confidence threshold.
- `dimension_kinds` summary in `sphere_overview` per pattern.

### Fixed
- `open_sphere` resets `explored_steps` state — `detect_pattern` coverage no longer leaks between spheres.
- `detect_pattern` fallback planner filters steps by pattern type.
- `find_geometric_path` default `beam_width` updated from 10 to 50.
- `search_entities` no longer crashes on lines with datetime columns.

## [0.3.3] — 2026-04-13

### Added

- `dim_mask` and `metric` parameters on `find_similar_entities` tool — dimension-selective similarity and cosine distance.
- `metric` parameter on `find_anomalies` tool — `"Linf"` for single-dimension spike detection.

## [0.3.2] — 2026-04-13

### Added

- `find_novel_entities` tool — geometric heredity scoring. Ranks entities by deviation from neighbor-expected geometric position. Requires pattern with edge table. Parameters: `pattern_id` (required), `top_n` (default 10), `sample_size` (default 5000).

## [0.3.1] — 2026-04-12

### Added

- `fdr_alpha`, `fdr_method`, and `select` parameters on `find_anomalies`, `attract_boundary`, `find_hubs`, and `find_drifting_entities`. Thin passthrough to the navigator — all FDR correction and submodular selection logic lives in the core library. Default behavior preserved when parameters are omitted.

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
