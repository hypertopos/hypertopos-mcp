# Changelog

All notable changes to `hypertopos-mcp` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.10.0] — 2026-10-28

### Changed

- `find_anomalies` ranks a calibrated pattern by its calibrated evidence score: the selection is the stored `is_anomaly` (`conformal_p <= 1 - anomaly_percentile/100`, the alarm budget) and the order is `conformal_p` ascending, then `anomaly_score` descending (rows added by incremental ingest are scored without the event axis, so only their p-values compare with built rows). `radius` and `rank_by` now default to `null` (the pattern's own score); `ranked_by` reports `anomaly_score` or `delta_norm`. `radius`, `metric="Linf"`, `dimension_weights`, `rank_by="delta_norm"` / `"signed_confidence"`, `boundary_aware` and `min_confidence > 0` return an error on calibrated patterns. `fdr_alpha` is the level of the cell gate and requires `fdr_resolution` or `fdr_temporal_resolution` (`fdr_method` defaults to `"storey"`).
- Polygons in `find_anomalies`, `get_polygon`, `attract_boundary`, `compare_entities`, `find_similar_entities` and `check_anomaly_batch` carry `score_rank_pct` in place of `delta_rank_pct`; polygons add `anomaly_score` (when it differs from `delta_norm`) and `conformal_p`. `geometry_filters` / `filter_expr` use `score_rank_pct`.
- `get_polygon` on a calibrated pattern returns `score_delta` (`{dim: rank-Gaussian value}` over `score_dims`) and no `theta_norm`, `witness` or `repair`.
- `explain_anomaly` on a calibrated pattern returns `severity` from `conformal_p` against the alarm budget, `conformal_p`, `anomaly_score`, `bits`, `axes` (`{axis: {rank, bits}}`) and `top_dimensions[]` as `{dim, z, bits, pct_of_total}`; no `theta_norm`, `ratio`, `witness`, `repair`, `reliability_flags` or `composite_risk` there.
- `assess_anomaly_certainty` reads `near_data_boundary` from `conformal_p` near the alarm budget on calibrated patterns.
- `get_sphere_info` lists `anomaly_percentile` per pattern and `score_dims` / `score_axes` on calibrated patterns.
- `check_alerts` reports a `calibration_age` alert when a pattern's calibration is older than 60 days.
- `sphere_overview`, `anomaly_summary`, `aggregate_anomalies`, `detect_data_quality_issues` and `compare_branches` count a calibrated pattern's anomalies by its stored `is_anomaly`, matching `find_anomalies`.
- `simulate_dimension_change` on a calibrated pattern takes natural values of score dimensions and returns the calibrated layer only: `anomaly_score_before/after`, `conformal_p_before/after`, `held_axes`, `top_dimensions_after` (`{dim, z, bits}`) and `dimensions_overridden` (`{dim, old_value, new_value, old_z, new_z}`); other dimensions return an error. `find_diverse_explanations(validate=True)` there reports `conformal_p_after_override`.
- `find_anomalies` on a calibrated pattern reports `anomaly_dimensions` as `{dim, z, mahal_share}` and `reliability_flags.dominant_dim` from the evidence split, so they name the same dimension as `explain_anomaly`.
- `find_calibration_influencers` returns an error on calibrated patterns; use `explain_anomaly` there.
- `find_anomalies` entries on event and calibrated patterns no longer carry `total_impact` and `classification` (they were always null there).
- `find_anomalies(include_emerging=True)` on a calibrated pattern returns an empty `emerging` list with `emerging_note`.
- `passive_scan` weighted scores on calibrated patterns follow the alarm budget: `anomaly_intensity = log(conformal_p) / log(alpha)`, at least 1 for every flagged entity.
- `compare_branches` on a calibrated pattern ranks `top_movers` by the change of evidence bits (`−log2 conformal_p`) and adds `anomaly_score_from` / `anomaly_score_to` and `evidence_change` to mover and flip rows; `theta_from` / `theta_to` are the lowest flagged score on each side.

### Removed

- `find_anomalies` parameters `p_value_method` and `fdr_axis` and `rank_by="min_q_per_dim"`; output fields `q_value`, `q_values_per_dim`, `min_q_per_dim` and `dominant_q_dim`.
- `attract_boundary` parameters `fdr_alpha`, `fdr_method` and `p_value_method`, and its `q_value` field.

### Fixed

- `combine_anomaly_pvalues` rankings no longer favour entities that a detector could not score; `composite_risk` reports `combined_p` unrounded.
- `find_anomalies` documents its `found` field (polygons in the page).
- The `X-Hypertopos-Session` header value is documented as the session's only credential: use a random, unguessable value; two clients sending the same value share one session.

### Security

- Over HTTP, a request with neither `Mcp-Session-Id` nor `X-Hypertopos-Session` is refused instead of being served from one session shared by every such client. Clients on the stateless MCP protocol send no session id, so two of them could open, read and replace each other's sphere; they now name their session with `X-Hypertopos-Session`, or connect with the session (initialize) protocol.

## [0.9.3] — 2026-10-26

### Fixed

- HTTP sessions keyed by the `X-Hypertopos-Session` header, and the shared anonymous session, are ended after `--session-idle-timeout` of inactivity like transport sessions, closing their sphere; before, they stayed open for the life of the server.

## [0.9.2] — 2026-10-25

### Added

- The README shows how to install the skills as a Claude Code plugin (`/plugin marketplace add hypertopos/hypertopos-skills`).

## [0.9.1] — 2026-10-25

### Fixed

- The `open_sphere` pattern summary lists the dimensions of `metric_properties` / `geo_properties` / `semantic_dim` blocks next to the relation dimensions, so a pattern built from blocks no longer shows an empty dimension list.

## [0.9.0] — 2026-10-25

### Added

- `get_session_stats` reports whether a newer sphere state exists than the one the session opened (`snapshot` block, with a hint to call `open_sphere`). After `recalibrate`, every tool sees the new calibration.
- `open_sphere` gains a `branch` parameter (string, default null) to open a named branch instead of main; the result includes a `ref` field that echoes which ref is active.
- `recalibrate` gains `branch` (run the fit on a branch, created when absent, without touching main) and `exclude_keys` (list of entity keys omitted from the fit but still scored); result adds `n_excluded`, `branch`, and `mu_sha256`.
- `compare_branches(pattern_id, branch, other=null, top_n=20)` — new read-only tool that compares one pattern between a branch and main (or two branches): anomaly counts, flipped-in / flipped-out entities, Jaccard overlap of anomaly sets, top movers by relative `delta_norm` change, keys present on one side only, calibration drift between the two fits, and the branch recipe log.
- `manage_branch(action, name=null, purpose=null)` — new tool to list, create, delete, or promote branches; `promote` replays the branch recipe log on main, verifies the fit fingerprint (sha256 of mu), and deletes the branch on success; refuses when main moved since branch creation or when the replayed fit does not match.
- `python -m hypertopos_mcp.main --transport http` accepts `--session-idle-timeout SECONDS` (default 1800) and `--max-sessions N` (default 10000). When an HTTP client session ends, by disconnect or idle timeout, the server closes that session's sphere and drops its state.
- Tool responses carry a top-level `failed_parts` list (`{part, error}`) when an optional part — entity properties of a line, `temporal_hint`, `dead_dimensions` — could not be loaded, instead of dropping it silently.
- `find_anomalies`, `aggregate_anomalies` and `aggregate` document the `in` membership operator for property and event filters.
- `aggregate` takes `group_by_relation` to group an event pattern by one of several relations to the same line (e.g. sender or receiver accounts).
- Over the HTTP transport each client session keeps its own open sphere, session state and call statistics: sessions are keyed by `Mcp-Session-Id`. stdio is unchanged.
- `find_anomalies` takes `rank_order` (`"desc"` default or `"asc"`) for the direction of a `rank_by_property` ranking, and `detect_pattern` ranks ascending for queries about low, lowest or few values.
- Response format control: `compact` (default) or `full`, chosen by `open_sphere(response_format=...)` for the session or the `HYPERTOPOS_MCP_FORMAT` environment variable. Compact leaves out positional `delta` vectors where a labelled form is present and numeric columns of entity `properties` tables (ranked and filtered properties stay), and every such response carries an `omitted` map naming what was left out and how to get it. `open_sphere` and `get_session_stats` report the active `response_format`.
- `get_session_stats` reports `session_key` (which client session answered) and `sessions_live` (client sessions with a sphere open); `close_sphere` forgets a closed HTTP client session; `open_sphere(force_reload=True)` is refused while another client session has a sphere open.

### Changed

- The descriptions of `find_counterparties` and `extract_chains` clarify when to use which: one-hop counterparties vs chains, population-wide or seeded and exact from a specific entity.
- After `recalibrate`, `compare_calibrations` and the other calibration-history tools can compare the previous and the new calibration.
- The server runs on the MCP Python SDK 2.x (`mcp>=2.3,<3`); 1.x is no longer supported. Tool names, parameters and results are unchanged.
- `extract_chains`: `from_col`, `to_col`, `time_col`, and `amount_col` parameters are now optional, defaulting to the event pattern's edge-table column configuration. With `seed_nodes` supplied, every event is used for those entities (exact chains — no start-node sampling).
- One name per concept across tools: the entity key parameter is `primary_key` everywhere (`decompose_drift`, `find_lead_lag`, `score_motif`, `get_event_polygons` used `entity_key`) and the result-count parameter is `top_n` everywhere (`aggregate`, `get_event_polygons`, `get_line_profile`, `search_entities`, `search_entities_fts` used `limit`; `find_motif_by_hops`, `find_chains_with_coherent_anomaly`, `extend_chain` used `max_results`; `investigate_chain`'s `extension_max_results` is `extension_top_n`). `get_event_polygons` echoes `primary_key`.
- A tool called with a parameter it does not declare now fails with an error naming the closest valid parameter (`unknown parameter 'limit' for tool 'aggregate'; did you mean 'top_n'?`) instead of silently using the default.
- `get_polygon` in the compact format lists the dimension-line edges as one `dim_edges` list of line ids instead of a full edge object each (the full format is unchanged); `find_anomalies(summary=true)` also leaves the positional `delta` out of `clusters[]`, as documented.
- `aggregate` no longer echoes request options that were not set: `group_by_property`, `pivot_event_field`, `filters` and `geometry_filters` appear only when given, `distinct` and `collapse_by_property` only when true.
- `find_group_influence` and `find_drifting_similar` return an object with a `results` list instead of a bare list, so their responses carry `elapsed_ms` and `failed_parts` like every other tool; `find_drifting_similar` now has the same shape whether it finds matches or returns a `warning`
- `get_centroid_map`: `centroid_drift.direction` is a `{dimension: value}` map instead of a positional list.
- `compare_branches` in the compact response format cuts recipe list parameters longer than 10 items (such as a recalibration's `exclude_keys`) to their first 10 and reports each full length in `omitted.recipe`; the full format returns the complete lists.
- `get_event_polygons` returns each event's own properties (amount, date, … from the event line) in a top-level `properties` table keyed by `primary_key`.
- `get_event_polygons` and `find_anomalies` no longer copy the referenced entities' properties onto every edge: edges keep only `line_id`, `point_key`, `status`, `direction` and `is_jumpable`, and those properties are sent once per entity in `related_properties` (one `{columns, rows}` table per line, keyed by `point_key`).
- Picks up the `pylance` 13.x requirement from `hypertopos`; existing spheres open without a rebuild and the MCP tools return the same payloads.
- Smaller responses: search tools, `compare_entities`, `find_common_relations` and `get_centroid_map` return properties in one table; `get_sphere_info` lists derived-dimension lines once; `get_polygon` on an event puts endpoint properties in `related_properties`; compact `get_polygon` returns `delta_by_dim`; compact solids carry one `slice_dim_labels` header; `explain_anomaly` and `investigate_entity` leave out zero-share dimensions and repeated fields; `contagion_score_batch`, `simulate_edge_removal` and `find_high_potential_motifs` stop repeating per-item constants; unset parameters are no longer echoed.
- `find_anomalies` per-dim FDR returns `q_values_per_dim` as a `{dim: q}` map of the dims with q < 1 and names the dominant dim in `dominant_q_dim`.
- `find_high_potential_edges` drops the `score_rank_pct` alias of `score_rank_pct_global`.
- Entity-list tools (`find_anomalies`, `find_clusters`, `find_hubs`, `find_similar_entities`, `find_drifting_entities`) return entity properties in one top-level `properties` table (`{columns: [<key field>, …], rows: [[key, …], …]}`) instead of a `properties` dict on every item.
- `find_clusters`: `centroid_delta` is a `{dim: value}` map replacing the positional list plus `dim_profile`; `members` is a list of entity keys; `representative_properties` is dropped when the representative is among the members.
- `find_anomalies` and `get_event_polygons`: polygons no longer repeat `type` and the requested `pattern_id`.
- `sphere_overview`: warning remediation text is sent once in a top-level `advice_by_warning_type` map instead of an `advice` field on every `dim_quality_warnings` entry; `profiling_alerts` entries no longer repeat their pattern's `pattern_id`.
- `passive_scan`: an interpretation text shared by several hits is sent once in a top-level `interpretations` map, referenced from each hit by `interpretation_ref`.
- `get_sphere_info`: line `columns` is a `{name: type}` map.
- `find_drifting_entities` echoes `filters` and `forecast_horizon` only when they are set.
﻿- Tool responses are more compact: floats keep at least 7 significant digits (more for large integer parts) instead of full repr precision, non-ASCII text is emitted as-is instead of `\u`-escaped, and tool input schemas no longer carry auto-generated `title` keywords.
- `open_sphere`, `get_sphere_info`, `get_line_schema` and `goto` no longer read whole points tables: line schemas and row counts come from dataset metadata, and `goto` reads only the requested point.
- Over HTTP, tool calls of different client sessions run concurrently; each session's own calls still run one at a time.
- The three tool phases (before `open_sphere`, after `open_sphere`, after `sphere_overview`) are tracked per client session: `tools/list` shows each session only its own tools, a call to a tool its phase has not unlocked is refused with a message naming the unlocking call, and the `sphere://capabilities` resource reports the calling session's sphere.
- `explain_anomaly.top_dimensions` entries are `{dim, kind, delta, pct_of_total}`, with `pct_of_total` the dimension's share of `delta_norm²`; the `bregman` field is removed, and on spheres without kind tags the list holds every dimension with a nonzero share.
- `reliability_flags.dominant_dim_share` / `single_dim_driven` on `find_anomalies`, `explain_anomaly` and `investigate_entity`, and the `find_diverse_explanations` / `diverse_explanations` hypotheses, use the same share.
- `find_drifting_entities` leaves windows without events out of the trajectory and reports `num_inactive_slices`; entities with fewer than two active windows no longer appear.
- The HTTP transport is documented as local-only: it binds to the loopback interface, has no authentication, and any session can open any sphere path the server process can read, so it must not be exposed to a network.
- `find_anomalies` and `find_clusters` `summary` and the `verbose` flag of `compare_calibrations`, `decompose_drift`, `find_calibration_influencers` and `find_lead_lag` default to `null`, which follows the session response format; an explicit `true` / `false` still wins. `find_anomalies` in the full format also returns `dim_labels`, naming the positions of every `delta` vector.
- `find_anomalies` leaves out `edges` on a polygon when it has no jumpable entity edges, instead of emitting an empty list (`total_edges` / `alive_edges` still give the counts).

### Removed

- Rank-derived p-values for FDR are removed: they only encoded an entity's position in the result set, so FDR filtering on them depended only on how many results were requested. `find_anomalies` and `attract_boundary` run FDR only on χ² model p-values (`p_value_method` defaults to and accepts only `"chi2"`), and `find_hubs` and `find_drifting_entities` no longer offer `fdr_alpha`, `fdr_method` or `p_value_method`.
- The `find_neighborhood` tool is removed. Use `find_counterparties` for transaction partners, and `get_polygon` / `jump_polygon` for an entity's relation neighbours.
- Removed `discover_chains` MCP tool; use `extract_chains` with `seed_nodes=[key]` for exact per-entity chain discovery.

### Fixed

- `find_drifting_entities`, `find_regime_changes`, `compare_time_windows`, `detect_trajectory_anomaly` and temporal-quality notes report deltas in the pattern's stored metric (Mahalanobis and dimension weights included), so their numbers agree with `dive_solid` and with the anomaly threshold.
- Tool errors reach the agent with their message again (for example an unknown pattern or a missing prerequisite) instead of a bare "Error executing tool".
- Responses are no longer sent twice (as text and as structured content), and tools no longer advertise an output schema.
- Changes to the tool list are announced to clients of every protocol version.
- Requests that read the open sphere run one at a time, so a sphere closed by one request is never read by another.
- `manage_branch` and `recalibrate` are marked as destructive.
- Sampling for `detect_pattern` checks the capabilities the client negotiated.
- Every tool response is strict JSON: a non-finite number (±infinity, NaN) is returned as `null` in all tools, not only in the tools that handled it before.
- `get_centroid_map` on a high-cardinality grouping property (tens of thousands of values) now returns in seconds with bounded memory instead of running out of memory: `max_groups` is applied before distances and centroid drift are computed, `inter_centroid_distances` covers the returned groups, and no entity line is read in full for the response. `detect_pattern`'s centroid step is capped the same way.
- `detect_pattern` merges its step results in plan order and builds the plan from investigation hints in hint order, so the same query gives the same result order (and the same candidates for result filtering) on every call.
﻿- `combine_anomaly_pvalues`, `classify_detector_consensus` and `assess_anomaly_certainty` accept `trajectory_continuous` in `detectors` and return its p-value (it never fired before); `detectors=null` uses the default set, which leaves this opt-in detector out.
- `find_anomalies` documentation lists the metrics it accepts (`L2`, `Linf`); it no longer advertises a `"bregman"` metric the tool rejects, and points to the `bregman_divergence` field each result carries.
- The tool reference documents every tool and every parameter the tools accept, and no parameter they reject: `classify_detector_consensus` and `calibration_influencer_history` have their own sections, and rows for options such as `include_reliability_flags` and `max_edges_loaded`, which the tools do not take, are gone.
- Tool responses carry numpy numbers as JSON numbers and booleans, not as strings: a score, count or flag that came out of a numpy computation (for example `"0.5"` or `"True"`) is now `0.5` / `true`, and a non-finite numpy value is `null`. Dates and times are ISO strings.
- `open_sphere(force_reload=True)` returns normally after reloading the server modules instead of failing at the end of the call.
- `detect_pattern` keeps one result per plan step when a step runs for several patterns (keys `name[pattern_id]`), and coverage / follow-ups follow each step's own pattern.
- A dimension named in a `detect_pattern` query ranks the fallback anomaly scan by that dimension.
- `detect_pattern` handles planned steps without `params`, and `open_sphere` clears investigation hints left from a previous sphere.
- Edge `direction`, `is_jumpable`, `line_id` and `point_key` are never overwritten by endpoint properties of the same name (those appear as `prop_<name>`).
- `q_value`, `min_q_per_dim`, `q_values_per_dim` and `cell_q_*` keep full precision, so very small q-values are no longer reported as 0.
- Local servers no longer delete each other's temporal scratch, a failed `open_sphere` leaves the previous session unchanged, and temporal tools are offered only when temporal data exists.
- `find_counterparties` returns counterparties with equal `tx_count` in a deterministic order (`amount_sum` descending on the edge-table path, then key ascending), so `top_n` results are stable.
- `find_anomalies` no longer adds a contradictory `signal_stability_note` ("Temporal signal is 'volatile' — 0% of anomalous entities change status") when the temporal history has no anomaly transitions to measure; the note is emitted only for `volatile` or `mixed` signals. `sphere_overview(detail="full")` reports such patterns with `temporal_quality.signal_quality="insufficient_data"`.
- When an HTTP client session ends while one of its tool calls is still running, the server closes that session's sphere after the call finishes (waiting up to 4 seconds) instead of closing it under the running call.
- `chain_full_loop_summary` counts its typology and R9-loop signals again; they never fired, so every chain was scored up to 30 points too low (a chain that `investigate_chain` escalated could come back as a false-positive candidate).
- `open_sphere` lists each pattern's entity count (it showed `?`) and labels repeated relations by role (`sender`, `receiver`) instead of repeating the line name.
- `open_sphere(force_reload=True)` serves the reloaded tool code instead of the code loaded at server start.
- Six list-returning tools (`simulate_counterparty_removal`, `combine_anomaly_pvalues`, `classify_detector_consensus`, `find_topological_anomalies`, `find_novel_entities`, `find_regime_changes`) answer `{results: [...]}` with `elapsed_ms`, like every other tool.
- `consensus_classification` always scores the focal entity; `found=false` now only means the entity has no geometry row.
- `chain_full_loop_summary` and `investigate_chain` report `inconclusive` with `failed_steps` when failed steps could change the verdict; an error repeated across steps is given once.
- `dive_solid` describes `base_polygon` as the current state (the slices are the history); `walk_line` from a polygon or solid position says to call `goto` first.
- `find_hubs` and `hub_history` no longer report `alive_edges` for continuous patterns, where the hub score is a capacity-weighted sum of clipped shapes, not an edge count.
- `audit_pattern_dims` explains why label-aware calibration is unavailable by naming the actual gap.
- `detect_pattern` follow-up hints name each dimension once.
- `find_anomalies(rank_by_property=...)` returns results in property order and pages with `offset`.
- `HYPERTOPOS_SPHERE_PATH` now works as documented: it is the default path of `open_sphere()`, so an agent can open the configured sphere without knowing its path. Tools no longer try to open it behind the agent's back (that path was unreachable behind the tool phases).
- `get_event_polygons` now rejects an `is_anomaly` key inside a partition `filters` entry and points to `geometry_filters`, instead of silently ignoring it.
- `passive_scan` returns an error listing the allowed values (`"count"`, `"weighted"`) for an unsupported `scoring` mode, instead of silently ignoring `threshold`.

## [0.8.1] — 2026-06-01

### Changed

- `extract_chains` now reports an unrecognised `event_pattern_id` as an `{"error": ...}` body instead of raising, matching how it already reports a missing `from_col`/`to_col` — one failure shape for the whole tool.
- `search_entities` adds a `hint` on zero matches (the match is case-sensitive) pointing at `search_entities_fts` and `get_line_schema`, mirroring the existing `search_entities_fts` zero-result hint.
- `find_motif_by_hops` docstring + docs no longer reference a non-existent `find_motif` tool; they point at the real closed-vocab path (`score_motif` / `find_high_potential_motifs`).
- `score_edge` now defaults to fast single-edge scoring; the population percentile (`score_rank_pct`, `is_high_potential`) is opt-in via `include_ranking=True`. The default response carries a `ranking_note` explaining how to request the percentile.
- `simulate_edge_removal` now returns an `{edges, edges_total, edges_evaluated, truncated}` envelope. On a high-degree entity whose candidate edges exceed the internal cap, `truncated` is `true` and `edges_evaluated` < `edges_total` — so a partial per-edge ranking is no longer mistaken for a complete one (previously the response was a bare list with no signal that the ranking covered only a capped subset).

### Fixed

- `discover_chains` now reports cyclic round-trip chains. Previously every returned chain had `is_cyclic: false` and the summary `cyclic` count was always `0` even when a funds-return cycle (A→…→A) existed — a silent false-negative on a core AML typology. The closing hop back to the origin is now surfaced as a cyclic chain. The `tools.md` return description for the tool was also corrected to document `is_cyclic` and `summary.cyclic`.
- The position tools (`goto`, `get_position`, `walk_line`, `jump_polygon`, and `π4_emerge`) now serialize non-finite entity-property floats (`NaN` / `±inf`) as JSON `null`, matching `get_polygon`. A property stored as a non-finite float would otherwise have serialized as the bare `NaN` / `Infinity` literal, which strict MCP clients reject.
- The `theta_sensitivity` tool now returns strict-JSON-safe output: a non-finite cliff `ratio` (e.g. `+inf` when a low-percentile θ collapses to zero on a degenerate subgroup) serializes as `null` instead of the bare `Infinity` literal that strict JSON parsers reject. The sibling `theta_sensitivity_report` tool already sanitized this; the lower-level tool now matches.

## [0.8.0] — 2026-05-30

### Added

- `get_session_stats` (and the `close_sphere` summary) now reports a `points_handle_cache` block with hit/miss counters, so an agent can see whether repeated entity lookups are reusing the open dataset handle.
- `assess_anomaly_certainty(primary_key, pattern_id, perturbation_alphas=[0.005, 0.01, 0.05])` — agent-correctness composer that fuses conformal p-value, FDR-gated perturbation stability, single-dim-driven flag, boundary-band proximity, calibration health, and cross-pattern consistency into one verdict (`high` / `moderate` / `low` / `contested`) plus a `[0, 1]` certainty score, a rationale, and recommended next steps; returns per-step `steps_status` and strict-JSON sanitised payload.
- `consensus_classification(primary_key, pattern_id, sample_size=10000)` — single-entity view over the multi-detector consensus sweep: extracts and routes the focal entity's detector-agreement pattern (`mixed_signal` / `anomalous_consensus` / `single_detector_signal` / `normal_consensus` / `insufficient_data`) with a `found` flag, `population_rank`, interpretation, and recommended next steps; returns `found=false` with a note when the entity falls outside the scored sample.
- `calibration_drift_report(pattern_id, calibration_a=None, calibration_b=None, top_n=10)` — adds a `drift_verdict` (`stable` / `moderate` / `significant`) over the per-dimension μ/σ/θ calibration drift between two epochs, plus an interpretation and routing recommendation for cross-epoch reasoning.
- `diverse_explanations(primary_key, pattern_id, k=3, min_contribution_pct=0.10)` — runs the diverse-cover explanation with counterfactual validation on and synthesises a `robustness_verdict` (`multi_cause_robust` / `single_cause` / `fragile` / `insufficient_signal`) from how many hypotheses' counterfactuals clear the anomaly flag, with interpretation and next steps.
- `theta_sensitivity_report(pattern_id, version=None)` — adds a `recalibration_safety` verdict (`safe` / `caution` / `unsafe`) derived from the stable-band / cliff structure of the per-percentile theta sweep, answering whether moving the anomaly percentile shifts the threshold smoothly or off a cliff.
- `audit_pattern_dims` now returns a `vector_index_health` block reporting ANN (IVF) index staleness for the pattern's geometry — `{index_present, index_type, num_indexed_rows, num_unindexed_rows, total_rows, indexed_fraction, num_partitions, is_stale, stale_threshold, recommendation}`. `is_stale` is `true` when incrementally-added rows sit outside the index (unindexed fraction above the threshold), so an agent can tell whether ANN-backed tools such as `pi10_attract_trajectory` currently see the full population. Metadata-only read, no geometry scan.
- `check_alerts` now emits a `stale_vector_index` alert (severity `MEDIUM`) for any pattern whose IVF index no longer covers all geometry rows, with a recommendation to reindex.

### Changed

- Picks up the `pylance` 7.x floor from `hypertopos`; existing spheres open transparently and all MCP tools return equivalent payloads.

### Fixed

- Detection, observability, navigation, geometry, aggregation, session, and smart-mode tools now sanitise non-finite floats (`±inf` / `NaN`) to JSON `null` before serialising, so every tool's output is strict-JSON-valid and parses cleanly in non-Python MCP clients even on degenerate populations.
- `assess_anomaly_certainty` accepts a `sample_size` parameter (default 20000) that caps the population sampled by each stability-sweep anomaly scan, bounding the per-verdict cost on large patterns.

## [0.7.3] — 2026-05-27

### Added

- `classify_trajectory(primary_key, pattern_id, sample_size=10000)` — new MCP tool in the `trajectory_index` tier. Categorises one entity's temporal trajectory as `outlier` / `lagging` / `leading` / `typical` by combining DTW distance vs the population-median trajectory with a first-derivative slope comparison. Returns `{primary_key, pattern_id, dtw_distance, category, category_evidence}`. ±inf and NaN floats are sanitised to JSON `null`.

### Changed

- `audit_pattern_dims` — each per-dim row now carries `auroc_per_dim`, the closed-form Gaussian-approximation AUROC `Phi((mu_pos - mu_neg) / sqrt(sigma_pos^2 + sigma_neg^2))`; top-level response adds `intrinsic_displacement_mean` and `extrinsic_displacement_mean` — the per-polygon decomposition means along and orthogonal to the Fisher LDA direction. Both populated only when label-aware calibration is available; legacy spheres receive `null` for the pattern-level means.
- `chain_full_loop_summary` `summary` block gains four chain-level reliability fields when the anchor pattern carries `label_aware_calibration`: `chain_mean_signed_confidence`, `chain_n_low_confidence_members`, `chain_n_single_dim_driven_members`, and `chain_confidence_verdict` ∈ {high, medium, low, label-aware-unavailable}. Derived from per-member `signed_confidence` ranking on the anchor pattern. Verdict "low" subtracts 10 from the overall investigation score. On patterns without label-aware calibration, all four fields are `null` and the verdict is `"label-aware-unavailable"` (no score change).
- `sphere_overview` MCP response is now an object with `patterns` (the prior per-pattern list, preserved verbatim) and a new top-level `cross_pattern_discrepancy: dict | null` block populated when the sphere has at least two patterns sharing the same `entity_line`. Each `pairs[]` entry carries `pattern_a`, `pattern_b`, `shared_line`, the four anomaly-bucket counts (`n_anomalous_only_in_a`, `n_anomalous_only_in_b`, `n_anomalous_in_both`, `n_anomalous_in_neither`), and `jaccard_anomaly_overlap` (intersection-over-union of anomalous primary_keys, `null` when both anomaly sets are empty). Clients that previously indexed the response as a list must read `response["patterns"]` instead.
- `aggregate` MCP tool per-row results carry `anomaly_rate: float | null` when `metric="count"` and the grouping is not composite (no `group_by_property` / `group_by_line_2` / `pivot_event_field` / `distinct`). Value is the share of the group's events flagged anomalous; `null` when the group has zero events.
- `find_anomalies` MCP tool accepts `sample_size: int | None = None` and `boundary_aware: bool = False` — pass-through to the navigator. `boundary_aware=True` stratifies the sample budget around the decision threshold, surfacing more boundary cases for calibration audits.
- `find_calibration_influencers` MCP tool accepts `auto_discover: bool` + `auto_k: int` — pass-through to navigator.
- `calibration_influencer_history(primary_key, pattern_id)` — new MCP tool (`base` tier) returning per-epoch μ-impact history for a known influencer.
- `passive_scan` hits gain an `interpretation` field on partial / full multi-source matches: `"anomalous in {source} only, normal in {others} — potential cross-pattern discrepancy"` when exactly one source flagged the entity and at least two sources participated, or `"anomalous across all {N} sources — coordinated multi-pattern anomaly"` when every participating source flagged the entity. The rule is invariant under `scoring="count"` vs `scoring="weighted"`.
- `find_similar_entities` response gains top-level `neighbor_anomaly_rate` and `neighbor_anomaly_count` fields summarising the share of returned neighbours with `is_anomaly=true`. Each neighbour entry also carries an `is_anomaly` boolean.
- `dive_solid` response gains a `trajectory_shape` field (`arch` / `V` / `linear` / `flat`) when the solid has at least three temporal slices. The shape is computed locally from the entity's own `delta_norm_snapshot` series and requires no population reference.

## [0.7.2] — 2026-05-21

### Added

- `audit_label_alignment(pattern_id, top_n=10)` — Fisher LDA direction alignment audit. Reports `auroc` (label-discrimination power of `delta_norm_signed` against the binary label declared in `sphere.yaml`'s `label_audit:` block), `n_pos` / `n_neg` (class population sizes), and the `top_n` most label-discriminating dims by `|direction_component|`. Sibling to `audit_pattern_dims`. Returns fallback shape with `auroc: null` on patterns without label-aware calibration.
- `chain_full_loop_summary(chain_id, chain_pattern_id, anchor_pattern_id, *, include_extension=True, include_drift=True, include_witness=True, include_sar_rationale=False, top_n_extensions=3)` — chain-side investigation orchestrator. Mirror of `investigate_entity` (entity-side). Composes `find_chains_with_coherent_anomaly`, `chain_witness_intersection`, `chain_drift_trajectory`, `classify_chain_typology`, `extend_chain` (forward + backward), `investigate_chain`, and optionally `generate_sar_rationale` into one MCP call with per-step `{ok, error}` envelopes. Top-level `summary` block reports `investigation_strength` ∈ {strong, moderate, weak}, `recommended_action` ∈ {escalate to SAR, continue investigation, false-positive candidate}, derived score, and a one-sentence rationale citing the load-bearing steps.

### Changed

- `find_anomalies` `rank_by` literal extended with `"signed_confidence"` — fuses `delta_norm_signed`, Fisher LDA direction alignment, and `reliability_flags` into one ranking: `score = delta_norm_signed × |lda_alignment| × (1 − reliability_penalty)`. Each surviving polygon carries `signed_confidence_score`, `lda_alignment`, `reliability_penalty`. Activates on patterns rebuilt with `label_audit:`-declared sphere.yaml. Fail-fast on patterns lacking label-aware calibration (structured `GDSNavigationError`, no silent fallback).
- `audit_pattern_dims` `recommended_action` field gains new categorical value `"kind_mismatch_review"`. Fires per dim when `|direction_component| < 0.05` AND `cohens_d_pos_neg >= 0.3` on a gaussian-declared dim — the dim has raw separation but zero Fisher weight, suggesting confounding with another dim. Highest priority in the decision tree (preempts `keep` / `split` / `drop_low_separation` / `investigate_drift`).
- `extract_chains` MCP tool gains an optional `anchor_pattern_id` parameter and a new per-chain `edge_potentials` field — Euclidean distance per consecutive-pair hop between endpoint delta vectors against the supplied anchor pattern. When `anchor_pattern_id` is null the field is a list of nulls per hop. Strict-JSON sanitised: `null` on missing polygon, mismatched delta shapes, or non-finite distance.
- `dive_solid` MCP tool accepts `counterfactual_frozen_population: bool = False` — passes through to the navigator. When `True`, every returned slice gains a `delta_norm_frozen_pop` field reporting the per-slice L2 norm against the FIRST slice's raw shape as the entity-relative reference. Default `False` keeps the existing response shape.
- README adds CI test workflow badge linking to the public-mirror Actions run.

## [0.7.1] — 2026-05-20

### Added

- `python -m hypertopos_mcp.main` accepts `--transport {stdio,http}` and `--port PORT` (default `stdio`, port `8080`). Selecting `--transport http` runs the server over the MCP streamable-HTTP transport on `127.0.0.1:<port>`; `stdio` behaviour is unchanged.
- `get_sphere_info` returns an additional top-level boolean field `label_aware_available` — `true` when the open sphere carries a `label_audit` block (format 3.1+), `false` otherwise. Lets agents discover whether label-aware calibration data is available without probing patterns one by one.
- `audit_pattern_dims(pattern_id, top_k=10)` — per-dim calibration audit. Reports raw `mu` / `sigma` alongside class-conditioned `mu_pos` / `sigma_pos` / `mu_neg` / `sigma_neg`, Cohen's d separation, and the per-dim component of the Fisher LDA direction vector when the pattern has label-aware calibration available. Each dim carries a categorical `recommended_action` ∈ {`"keep"`, `"split"`, `"drop_low_separation"`, `"investigate_drift"`}. Rows sorted by `|cohens_d_pos_neg|` descending, capped by `top_k`. Patterns without label-aware calibration return a fallback shape (raw stats + `recommended_action: "keep"` + top-level `reason`). The full-field path is now produced by the builder hook on spheres rebuilt with a `label_audit:` block in `sphere.yaml` and the `--label-aware-calibration` flag; previously the field stayed unpopulated and the tool always returned the fallback shape.
- `sphere_overview` `dim_quality_warnings[]` gains a new pattern-level type `"heteroscedasticity"` — fires for patterns carrying `group_by_property` when a build-time Brown-Forsythe (median-centred Levene) test on `delta_norm` partitioned by the grouping column returns `p < 0.01`. The `dim_label` carries the grouping variable name (not a δ-dim); `evidence_value` is the p-value and `threshold` is 0.01. Means the global θ / pooled-σ / global-percentile assumptions are statistically violated for this pattern — agents can read the warning as confirmation that per-group θ calibration is statistically warranted on this grouping.
- `sphere_overview` `dim_quality_warnings[]` gains a new per-dim type `"non_normal_dim"` — fires when a dim declared with `kind='gaussian'` has a build-time Shapiro-Wilk / KS normality `p < 0.01`. The `dim_label` is the δ-dim name; `evidence_value` is the p-value and `threshold` is 0.01. Suppressed for dims already flagged `negative_space` (the kind itself is the bug, not the empirical departure). Bernoulli and poisson dims are silently skipped — normality does not apply.

## [0.7.0] — 2026-05-18

Non-finite floats (`±inf`, `NaN`) on new tools sanitised to `null` on the wire per the strict-JSON convention.

### Added

#### Sphere format 3.0 + detector composition refresh (breaking)
- Sphere format bumped 2.4 → 3.0 (breaking). `open_sphere` rejects 2.4 spheres with a structured error pointing to the rebuild path; no MCP tool surface, parameter, or return-shape changes.
- `composite_risk` and `composite_risk_batch` now combine cross-pattern p-values via the Wilson harmonic-mean p-value (HMP); `combined_p`, `n_patterns`, `per_pattern{}` retained, `chi2` and `df` removed.

#### Entity-side investigation orchestrator + counterfactual suite
- `investigate_entity(primary_key, pattern_id, line_id, chain_pattern_id=null, include_polygon=true, include_explain=true, include_witness_cohort=true, include_chains=true, include_root_cause=true, include_graph_geometry_tension=true, include_per_edge_counterfactual=false, top_n_witnesses=5, top_n_chains=3, top_n_edges=5)` — one-call entity investigation orchestrator returning per-step blocks plus `steps_status`; tier `base`.
- `simulate_edge_removal(primary_key, pattern_id, line_id, top_n=5, edge_ids=null)` — per-edge counterfactual ranking returning `(edge_id, delta_norm_before, delta_norm_after, drop_pct, dominant_dim_label, source_value_pvalues, min_pvalue, dominant_significance_dim, dimensions_skipped)`; tier `edge`.
- `simulate_counterparty_removal(primary_key, pattern_id, line_id, top_n=5, edge_top_n=null)` — per-counterparty rollup returning `{partner_key, n_edges, sum_drop_pct, sum_abs_drop_pct, max_abs_drop_pct, dominant_dim_label, edge_ids}` sorted by `sum_abs_drop_pct`; tier `edge`.
- `select_minimal_joint_edge_removal(primary_key, pattern_id, line_id, target_drop_pct=50.0, k_max=10)` — greedy joint counterfactual returning `{selected_edge_ids, selected_partner_keys, achieved_drop_pct, selection_sequence, target_reached, k_max_reached}`; tier `edge`.
- `simulate_dimension_change(primary_key, pattern_id, line_id, set_dimension, top_n=5)` — what-if dimension override reporting `delta_norm_before/after`, anomaly flip, top witness dims, audit trail; tier `edge`.

#### Detector composition + multi-hypothesis explanation
- `combine_anomaly_pvalues(pattern_id, detectors=null, weights=null, sample_size=10000, top_n=50)` — multi-detector anomaly consensus across `delta_norm`, `neighbor_contamination`, `segment_shift`, `trajectory_continuous`, and `density_gap` (skipped silently); returns ranked `{primary_key, hmp, p_per_detector, rank}`; tier `multi_pattern`.
- `classify_detector_consensus(pattern_id, detectors=null, sample_size=10000, top_n=50, anomaly_threshold=0.01, normal_threshold=0.5)` — categorical detector-agreement typology returning `{primary_key, classification, anomalous_detectors, normal_detectors, borderline_detectors, n_detectors_fired, hmp, p_per_detector, rank}`; tier `multi_pattern`.
- `find_diverse_explanations(primary_key, pattern_id, n_hypotheses=3, min_contribution_pct=0.10, validate=False)` — K diverse disjoint hypotheses for an anomaly returning `hypotheses`, `diversity_score`, `degraded_reason`; tier `base`.

#### FDR upgrades + reliability triage + sphere-validation
- `find_anomalies` gains `fdr_resolution: str | null` and `fdr_temporal_resolution: str | null`; survivors carry `cell_q_spatial`, `cell_q_temporal`, `cell_path` when the corresponding axis ran. When a resolution is set, unspecified `p_value_method` defaults to `"chi2"` and unspecified `fdr_method` defaults to `"storey"`.
- `find_anomalies` gains `fdr_axis: "entity" | "per_dim" | "both"` and `rank_by: "delta_norm" | "min_q_per_dim"`; per-dim mode attaches `min_q_per_dim`, `q_values_per_dim`, `dominant_q_dim_idx` to each survivor.
- `reliability_flags` field surfaced on `find_anomalies`, `explain_anomaly`, `composite_risk`, `combine_anomaly_pvalues`, and `investigate_entity`; shape `{single_dim_driven, dominant_dim, dominant_dim_share, low_confidence_bucket, confidence, flags}`.
- `sphere_overview` per-pattern `dim_quality_warnings` gains `dominant_dim_mass` and `negative_space` auditor types alongside the existing `dead_dim` and `sparse_dim`.

#### Chain extensions + graph-geometry + persistent-homology + declarative compliance
- `chain_witness_intersection(chain_id, chain_pattern, member_pattern, min_jaccard=0.5, top_k_witness=5)` — coordinated-witness diagnosis returning `intersected_witness_dims`, `union_witness_dims`, `mean_pairwise_witness_jaccard`, `coordinated`, `interpretation`; tier `base`.
- `chain_drift_trajectory(chain_id, chain_pattern, member_pattern, n_windows=4)` — per-member regime + chain-level drift returning `per_position_trajectory`, `chain_level_regime`, `chain_drift_score`; tier `base`.
- `find_graph_geometry_tension(primary_key, pattern_id, line_id, k_geometric=20, top_n_hidden=5, top_n_suspicious=5)` — behavioural k-NN vs graph adjacency cross-tab returning `hidden_cluster`, `suspicious_links`, `tension_score`; tier `edge`.
- `find_topological_anomalies(pattern_id, top_n=20, force=false, sample_size=50000, k_neighbors=50, pca_dim=10)` — rank entities by local persistent-homology H_1 cycle persistence returning `primary_key`, `topo_score`, `h1_max_persistence`, `h0_mean_death`, `n_h1_features`, `computed_at`; tier `base`.
- `find_conformance_violations(pattern_id, rule_id=null, severity_min="low", top_n=100)` — read-only query over declarative-rule violations returning `{pattern_id, n_violations, violations, rules_evaluated, manifest, warnings, follow_up}`; tier `base`.

### Changed

#### Stress-test follow-up hardening
- `simulate_counterparty_removal` default for `edge_top_n` is now 500 (was unbounded). Hub entities with thousands of counterparties no longer push per-call latency past several minutes by default; pass an explicit larger value when exhaustive coverage on a known hub is required.
- `select_minimal_joint_edge_removal` accepts a new `max_candidates` parameter (default 500) that caps the greedy search input before the joint-removal loop. Response carries `n_candidates_seen`, `n_candidates_used`, and `candidates_truncated`.
- `get_centroid_map` accepts a new `max_groups` parameter (default 100) capping the returned `group_centroids` list by member count descending. The `structural_outlier` is always preserved even when outside the top-N. Truncation surfaces `groups_truncated_warning`, `n_groups_total`, and `n_groups_returned`.
- `score_edge` / `score_motif` / `anomalous_edges` error message for "entity not found in pattern geometry" now identifies the pattern type and the expected key shape, and points the agent at `search_entities` for valid keys.

### Fixed
- `find_hubs` and `hub_history` no longer crash with a numpy broadcast error on anchor patterns whose geometry dim count exceeds the relation count (the underlying navigator paths slice the shape matrix to `len(pattern.edge_max)` before the per-relation multiply). The `line_id_filter` agent-side workaround is no longer required.
- `classify_detector_consensus` ranking is deterministic on the HMP-saturation case (per-detector p-values collapsed at the float floor) — `delta_norm` from the underlying combiner's reliability flags is used as a final tiebreaker.
- `find_high_potential_motifs` and `score_motif` reject event `pattern_id` early with a clear error pointing at the anchor companion. Calling these with an event pattern previously burned the full enumeration cost and returned an empty list.

## [0.6.7] — 2026-05-10

### Added
- `sphere_overview` per-pattern entry gains an optional `dim_quality_warnings` block surfacing two silent build-time failure modes that break z-score / `delta_norm` semantics: **dead_dim** (zero variance, z-score undefined) and **sparse_dim** (mostly-zero with rare nonzero, gaussian assumption wrong). Each warning carries `type`, `dim_label`, `reason`, and `advice`. Computed from cached pattern state — sub-millisecond, no storage scan; skipped silently for patterns where neither failure mode applies. Both classes were previously invisible at agent runtime — the dim sat in the delta vector contributing nothing or contributing wrong signal, and the investigator had no way to know without scrolling the calibration log.

### Added
- `generate_sar_rationale(chain_id, pattern_id, anchor_pattern_id, evidence=null, regulatory_template="FinCEN SAR")` — template-based SAR narrative composition over R9 evidence. Use as the LAST call in the R9 loop, after `investigate_chain` aggregates the evidence — produces a 3-5 paragraph draft narrative (chain identification + typology, per-hop trace, boundary extensions, chain-shape corroboration, aggregated strength + recommended action) plus structured `evidence_anchors` pointers per narrative claim and a derived `confidence` (`high` / `moderate` / `low`). When `evidence` is null, runs the R9 loop server-side first; when supplied, the dict must match the `investigate_chain` return shape (cheaper for repeated narratives on the same chain). No LLM call — pure template composition. The `regulatory_template` parameter is a passthrough hint for downstream filing systems, does NOT change the narrative content. Closes the investigation→SAR pipeline that the chain-coherent loop opened in 0.6.4-0.6.7.

### Added
- `find_anomalies(..., dimension_weights=null)` — optional per-dimension weight mapping that scales each dim's contribution to the rank score before computing `delta_norm`. Default `null` leaves behaviour unchanged. Missing dims default to `1.0`; explicit `0.0` silences a dim. Requires `metric` in `'L2'` or `'Linf'`. Connects stratified correlation-gate verdicts to runtime ranking — discount NOISE-classified dims via `0.0`, down-weight HEAVY-TAIL dims via `0.5`. Validates dim names against the pattern's dim labels and rejects negative / non-finite / non-numeric weights with a clear error.
- `chain_investigation_summary(chain_pattern_id, anchor_pattern_id, min_hops=2, max_runs=10000)` — pre-investigation triage MCP tool for a chain pattern. Use as the FIRST call when entering a sphere with a chain anchor pattern: low `coherent_run_rate` (<0.5%) and low `cross_pattern_overlap.jaccard` with shape anomalies signals "skip the deep R9 loop, fall back to `find_anomalies(chain_pattern)`"; high `coherent_run_rate` (>5%) signals a productive R9 loop; `recommended_min_hops` surfaces the natural threshold for the population so the agent doesn't drill in at the default and miss the strongest cases. Cost is one `find_chains_with_coherent_anomaly` sweep — the same call an investigator would issue as the first step, with the aggregates surfaced for free.
- `investigate_chain(chain_id, pattern_id, anchor_pattern_id, extension_max_results=20)` — one-shot orchestrator MCP tool that runs the full R9 investigative loop on a single chain and aggregates the per-step outputs into a single SAR-ready report. Use as the SECOND call after `chain_investigation_summary` triage points at a specific chain — saves the round-trip cost of running `anomaly_propagation_in_chain`, `classify_chain_typology`, the chain-shape anomaly lookup, and `extend_chain` (forward + backward) sequentially. Per-step blocks are individually wrapped in `{ok, data}` or `{ok=False, error}` so a partial failure does not abort the whole report. Summary block derives `investigation_strength` (`strong` / `moderate` / `weak`) and a `recommended_action` (`escalate to SAR` / `continue investigation` / `false-positive candidate`) with a single-paragraph rationale.

## [0.6.6] — 2026-05-09

### Added
- `sphere_overview` per-pattern entry gains an optional `theta_sensitivity_summary` block when the pattern's latest calibration epoch carries a populated `theta_sensitivity` field. Each block carries `stable_band_from`, `stable_band_to`, `stable_band_length`, `n_cliffs`, and `theta_at_p95` so an agent reading the population overview can triage at a glance: `stable_band_length >= 8` and `n_cliffs == 0` is a smooth pattern; `stable_band_length <= 4` or `n_cliffs >= 2` warrants a `theta_sensitivity(pattern_id)` drill-down for the full sweep. Calibration epochs from older builds that lack the underlying field continue to render `sphere_overview` entries as before — the block is silently skipped.
- `theta_sensitivity(pattern_id, version=null)` — new MCP tool surfacing the calibration-quality diagnostic for one pattern. Returns the per-percentile sweep at `p90 .. p99` (`theta_mean`, `anomaly_count_mean`, `anomaly_rate` per percentile) plus derived `stable_band` (`from`, `to`, `length` — longest contiguous run of percentiles whose adjacent-pair `theta_mean` ratio stays below 1.30) and `cliffs[]` (boundary pairs whose `theta_mean` ratio is at or above 1.50, signalling a heavy-tail region of the underlying `delta_norm` distribution). Use to ask "is the chosen `anomaly_percentile` sitting near a cliff or in a stable band?" before reducing/raising the threshold. Resolves the latest epoch on disk by default; `version=N` selects an explicit historical epoch. Raises `ValueError` when the calibration epoch lacks the field (calibration epochs from prior builds need a rebuild before the diagnostic is available).

### Changed
- `detect_pattern` smart-mode meta-tool gains chain-coherent intent recognition. Natural-language queries like "find chains where consecutive accounts cascade through structuring", "classify chain CHAIN-XXX", "trace chain CHAIN-XXX hop by hop", and "extend chain CHAIN-XXX forward" now route to `find_chains_with_coherent_anomaly`, `classify_chain_typology`, `anomaly_propagation_in_chain`, and `extend_chain` respectively, with chain anchor pattern + entity anchor pattern auto-detected from sphere context. Chain-id tokens (e.g. `CHAIN-109852`) are extracted from the query and threaded through. The keyword fallback path (used when LLM sampling is unavailable) supports the same routing. Specific chain-coherent keyword sets take precedence over the generic "chain"/"flow" routing to `extract_chains` so investigators get the right primitive without manually specifying tool names.
- `open_sphere` `suggested_queries` extended with a chain-coherent investigative loop entry point when the sphere contains both a chain anchor pattern (entity_line or pattern_id matching `chain`) and a non-chain entity anchor pattern. The new suggestion takes the form `find chains where consecutive <entity_line> are individually anomalous in <chain_pattern_id>` and is recognised by the `detect_pattern` smart-mode router so agents discover the chain-coherent loop without manually drilling into `sphere_overview`. Suggestion cap raised from 5 to 6 entries to keep room for both the chain-coherent suggestion and the existing per-pattern anomaly suggestions.

## [0.6.5] — 2026-05-08

No MCP-tool surface changes in this release. Tools remain identical to 0.6.4; underlying chain anchor pattern feature set expanded in `hypertopos` core (see `hypertopos-py` 0.6.5 changelog) and surfaces automatically through existing chain-coherent investigative loop tools after sphere rebuild.

## [0.6.4] — 2026-05-07

### Added
- `classify_chain_typology` MCP tool — exposes the new typology classifier. Args: `chain_id`, `pattern_id`, `anchor_pattern_id`. Returns the typology block (shape, peak_position, position_in_chain, extension_signals, pre_run / breakpoint rank buckets, dominant_top_dim, run summary).
- `extend_chain` MCP tool — exposes the boundary-extension primitive. Args: `chain_id`, `pattern_id`, `anchor_pattern_id`, `direction` (`"forward"` or `"backward"`, default `"forward"`), `max_results` (default 20). Returns boundary key, candidate extension entities ranked by their own anchor anomaly status, and a summary.
- `anomaly_propagation_in_chain` MCP tool — exposes the new navigator inspector primitive. Args: `chain_id`, `pattern_id` (chain anchor pattern id), `anchor_pattern_id` (entity anchor pattern). Returns JSON with `hops` (per-hop progression), `summary` (`n_hops`, `n_anomalous`, `max_run_length_same_top_dim`, `dominant_top_dim`), and `elapsed_ms`. Read-only.
- `find_chains_with_coherent_anomaly` MCP tool — exposes the new navigator primitive. Args: `pattern_id` (chain anchor pattern id), `anchor_pattern_id` (entity anchor pattern whose primary keys match the chain hops), `min_hops` (default 3, must be >= 2), `max_results` (default 100). Returns JSON with `chains` (ranked runs) and `diagnostics` (`n_chains_total`, `n_anomaly_entities`, `elapsed_ms`). Read-only.

## [0.6.3] — 2026-05-06

### Changed
- `compare_calibrations` return payload gains a new `edge_dim_threshold_drift`
  field — a per-source-dim `{from, to, delta}` map of the
  `_count_above_threshold` cutoffs (population p95 per source dim)
  persisted in the calibration epoch JSON. Populated when both compared
  epochs declared `edge_dim_aggregations:` on the anchor pattern; `null`
  when at least one of the compared epochs lacks the aggregations block,
  preserving the prior shape for spheres without aggregations.
- Anchor-pattern responses (`find_anomalies`, `find_similar_entities`,
  `find_clusters`, `explain_anomaly`, `find_calibration_influencers`,
  `decompose_drift`, etc.) on spheres rebuilt with the expanded
  `edge_dim_aggregations:` block transparently include the new aggregated
  dim columns (three additional canonical aggregates `_std` / `_p95` /
  `_count_above_threshold`, k>2 composite anchor support, per-dim subset
  selector) in the polygon `delta` vector and derived metrics. Spheres
  without the YAML hook are byte-identical to the prior response shape.

## [0.6.2] — 2026-05-05

### Changed
- Anchor-pattern responses (`find_anomalies`, `find_similar_entities`,
  `find_clusters`, `explain_anomaly`, `find_calibration_influencers`,
  `decompose_drift`, etc.) on chain anchor patterns rebuilt with
  `chain_lines.<id>.edge_dim_aggregations:` transparently include
  `<source_dim>_mean` / `<source_dim>_max` aggregates of the per-edge
  sidecar dims in the polygon `delta` vector and derived metrics. No
  tool-level API change. Spheres without the new YAML hook are
  byte-identical to the prior response shape.
- Cold-call latency on motif tools (`score_motif`, `find_high_potential_motifs`,
  `find_motif_by_hops`) drops materially due to the rewrite of
  `AdjacencyIndex` load path in hypertopos-py. No tool surface change.
  See hypertopos-py CHANGELOG for details.

## [0.6.1] — 2026-05-01

### Fixed
- `find_motif_by_hops` MCP tool: `score=true` now returns scored motifs
  (was a no-op since the declarative motif API shipped — the scoring
  branch in the navigator was unreachable on the only patterns the tool
  accepts). Scored motifs include `score`, `score_breakdown`, and a new
  `anchor_pattern_id` provenance field together. Each per-edge entry in
  `score_breakdown` now carries an `event_factor` reflecting the event
  pattern's per-transaction polygon norm — distinct transactions between
  the same `(from, to)` accounts now produce distinct motif scores
  rather than collapsing to a tie. Raises when no anchor companion is
  configured for the queried event pattern. Thin passthrough; no MCP
  tool surface change beyond the new fields in the response payload
  when `score=true`.

### Added
- `find_motif_by_hops` MCP tool: per-hop predicate dict accepts a new
  optional `require_anomalous_entity: bool` field — see hypertopos
  CHANGELOG for semantics (closes the X1 predicate set). Thin
  passthrough; no MCP tool surface change.

### Changed
- `find_motif_by_hops` MCP tool: per-hop list now accepts up to 8 hops
  (was 6). New optional `time_window_hours: float` top-level parameter
  for total-chain-span cap (independent semantic from per-hop
  `time_delta_max_hours`; both apply when both are set). Validation
  fires before any sphere-state-dependent early-return so bad values
  surface as errors on edge-table-less spheres rather than silent
  empty results. Thin passthrough; no other MCP surface change.
- `find_motif_by_hops` per-hop predicate dict accepts a new optional
  `amount_ratio_to_prev: float` field — see hypertopos CHANGELOG for
  semantics. Thin passthrough; no MCP tool surface change.
- Anchor-pattern responses (`find_anomalies`, `find_similar_entities`,
  `find_clusters`, `explain_anomaly`, `find_calibration_influencers`,
  `decompose_drift`, etc.) on spheres rebuilt with the new
  `edge_dim_aggregations:` YAML block on an anchor pattern transparently
  include `<source_dim>_mean` / `<source_dim>_max` aggregates of the
  per-edge sidecar dims in the polygon `delta` vector and derived
  metrics. No tool-level API change — the new dims appear in the same
  fields existing primitives already expose. Spheres without the YAML
  block are byte-identical to the prior response shape.

## [0.6.0] — 2026-04-30

### Fixed
- `detect_trajectory_anomaly`: `sample_size` default changed from `null`
  (full scan) to `10,000`. Pass `sample_size=0` to restore the full-
  population temporal scan.
- `detect_segment_shift`: `changepoint_date` field removed from output
  (it was populated by an internal full changepoint-detection scan for
  a single optional enrichment field). Tool is now significantly faster.
  Callers needing changepoint context should call `find_regime_changes`
  directly.
- `find_high_potential_edges` on event patterns with large edge tables:
  pair-count computation now uses a direct two-column PyArrow groupby
  on the edge table instead of a full-table `to_pylist()` rebuild of
  the in-memory adjacency index. Added an entity-type ratio guard that
  fires BEFORE any edge-table or geometry I/O — read the build-time
  `edge_stats` cache for `unique_from + unique_to` and compare against
  `pattern.population_size` from `sphere.json`. When the ratio is < 1 %
  the edge endpoints belong to a different entity type than the host
  geometry (e.g. zone IDs in a trip-edge pattern whose geometry holds
  trips) and the tool returns an empty ranking without opening either
  dataset.
- `search_entities_hybrid` docstring: clarified that `primary_key` must
  be an actual entity key (e.g. `"100428738"`), not a line name or
  pattern ID. Obtain from `walk_line` or `search_entities` first.

### Added
- `find_motif_by_hops` MCP tool — declarative motif API. Power-user
  escape hatch from the closed-vocab `find_motif` registry; agent passes
  a list of dicts describing per-hop predicates and the navigator walks
  the edge table for matching chains of length 1..6. Composes with the
  per-edge sidecar from the edge_dimensions YAML block. Smart-mode
  keywords: *custom motif*, *hop predicate*, *edge dim filter motif*,
  *motif by hops*. Strict-JSON sanitisation of `±inf` scores. Tier
  `base` — accessible after `sphere_overview`. `direction="reverse"`
  walks the predecessor chain in causal order (timestamps strictly
  decreasing); `direction="any"` drops monotonicity and treats the
  time window as `|Δt|`. `hops[0].time_delta_max_hours` must be omitted
  (validation rejects it — there is no previous timestamp on the first
  hop). `score` defaults to `false`. Seeded queries (`seed_keys`
  provided) build a scoped adjacency via Lance BTREE-pushdown reads
  expanding the BFS frontier hop-by-hop — only the visited subgraph is
  materialised, never the full edge table. Unseeded full enumeration
  uses the cached global adjacency.
- `find_density_gaps` MCP tool — joint density gap detection via
  probability integral transform plus independence null on dim pairs.
  Thin passthrough to `GDSNavigator.find_density_gaps` with strict-JSON
  sanitisation of `±inf` (gap ratio for zero-observed cells) to `null`.
  Returns under-populated cells named in delta-space (z-score) ranges
  with BH-corrected q-values. `sample_size` parameter (default
  `100,000`) is passed directly to the Lance reader; pass `0` to scan
  the full population. Surface keywords for smart-mode dispatch:
  *missing segment*, *density gap*, *dark matter*, *under-represented*,
  *missing combination*.

### Changed
- Event-pattern responses (`find_anomalies`, `find_similar_entities`,
  `find_clusters`, etc.) on spheres rebuilt with the new
  `edge_dimensions:` YAML block transparently include up to five
  additional dimensions per event in the polygon `delta` vector and
  derived metrics. No tool-level API change — the new dims appear in
  the same fields existing primitives already expose. Spheres without
  the YAML block are byte-identical to the prior response shape.

### Added
- `compare_calibrations` MCP tool — per-dimension μ/σ/θ drift between two
  calibration epochs of one pattern; thin passthrough to
  `GDSNavigator.compare_calibrations`.
- `decompose_drift` MCP tool — per-entity intrinsic vs extrinsic decomposition
  of geometric drift between two temporal slices viewed across two calibration
  epochs; thin passthrough to `GDSNavigator.decompose_drift`. The tool body
  imports `asdict` locally next to its other per-call imports — every prior
  smoke-test path fired one of the ValueError gates BEFORE the asdict call,
  so the missing import only surfaced on the first real ≥2-epoch sphere;
  regression test `test_decompose_drift_mcp.py` rounds-trips the full MCP
  serialisation path on a real fixture and skips cleanly when the on-disk
  sphere has only one epoch.
- `find_drifting_entities` per-entity dict gains 3 additive scalar fields:
  `intrinsic_displacement`, `extrinsic_displacement`, `intrinsic_fraction`.
  Auto-defaults to `(oldest retained, current)` calibration epochs; resolves
  to `null` per-entity when decomposition isn't computable (storage backend
  without multi-epoch retention, `<2` retained epochs, schema mismatch, or
  `<2` slices for the entity).
- `find_calibration_influencers` MCP tool — per-entity influence on coordinate
  system calibration with 4-cell classification matrix; thin passthrough to
  `GDSNavigator.find_calibration_influencers`. The tool body imports `asdict`
  locally next to its other per-call imports (per
  feedback_mcp_serializer_imports_local.md). Regression test
  `test_calibration_influencers_mcp.py` rounds-trips the full MCP
  serialisation path on a real fixture (rebuilt Berka format 2.4).
- `find_group_influence` MCP tool — caller-supplied leave-set-out impact +
  reinforcing/canceling factor; thin passthrough to
  `GDSNavigator.find_group_influence`.
- `find_anomalies` MCP per-entity polygon dict gains 2 additive scalar fields:
  `total_impact`, `classification`. Auto-defaults; resolves to `null`
  per-entity when not computable.
- `find_lead_lag` MCP tool — cross-pattern temporal lead-lag in
  population-relative coordinates. Thin passthrough to
  `GDSNavigator.find_lead_lag` with strict-JSON sanitisation of ±inf/NaN to
  `null`. Three modes through one parameter set: population-aggregated
  centroid drift cross-correlation (default), per-dim D_A × D_B FDR-corrected
  matrix (`top_dim_pairs` always; full matrix in `per_dim_pairs` when
  `verbose=True`), per-entity drill-down via `entity_key`. Surfaces
  `agreement` (`"strong"` / `"weak"` / `"divergent"` between centroid and
  volatility series), `is_significant` (Bonferroni-adjusted peak),
  `reliability` (high/medium/low based on `N - 1`), `degenerate_signal`
  (forced `agreement="divergent"` when either centroid drift series has
  zero variance — typical on disjoint-entity-space `cohort="all"` runs).

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
