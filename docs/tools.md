# hypertopos-mcp — Tool Reference

> Server specification and architecture: [mcp-spec.md](mcp-spec.md)

**hypertopos-mcp** exposes a Geometric Data Sphere (GDS) over the Model Context Protocol. Each tool maps to a navigation primitive or utility function from the `hypertopos` library.

**Install:** `pip install hypertopos-mcp`

**Start:** `python -m hypertopos_mcp.main` (set `HYPERTOPOS_SPHERE_PATH` to your sphere directory)

**Concepts:** See [hypertopos core concepts](https://github.com/hypertopos/hypertopos-py/blob/main/docs/concepts.md) for Point, Edge, Polygon, Solid, Pattern, Alias, Manifest.

All tool responses include `elapsed_ms` (float, milliseconds).

---

## MCP Resources

Sphere metadata is also exposed as **MCP Resources** — cacheable, read-only endpoints that clients can subscribe to. After the first read, clients may cache the content and avoid repeated tool calls.

| URI | Name | Description |
|-----|------|-------------|
| `sphere://info` | `sphere_info` | Sphere schema: lines (with columns and roles), patterns (with type, entity line, temporal flag), and alias list. Returns an error message if no sphere is open. |
| `sphere://capabilities` | `sphere_capabilities` | Detected sphere capabilities: `has_temporal`, `multi_pattern`, `has_trajectory_index`. Returns an error message if no sphere is open or capabilities have not been detected. |

**Notes:** Resource content reflects the currently open sphere. If no sphere is open, both resources return a JSON object with an `error` field. Resources complement `get_sphere_info` — they provide the same core schema data in a client-cacheable form.

---

## Session Management

### `open_sphere`

Opens a sphere and creates a session.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | Relative path to the sphere directory |
| `force_reload` | bool | `false` | Reload all hypertopos.* Python modules before opening. Use during development after editing library code — eliminates the need for a full MCP server restart. Not safe in production (module reload is not thread-safe). |

**Returns:** `status`, `path`, `sphere_id`, `name`, `summary: {lines, patterns, aliases}`, `hint`

**Notes:** Always use relative paths — absolute Windows paths fail. Returns status only; call `sphere_overview()` for population health and `get_sphere_info()` for full schema.

```python
open_sphere("benchmark/berka/sphere/gds_berka_banking")
```

---

### `close_sphere`

Closes the active session and releases resources.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| _(none)_ | — | — | — |

**Returns:** `status`, `session_stats` (`{total_tool_calls, total_elapsed_ms, wall_clock_ms, per_tool}`)

---

### `get_sphere_info`

Returns full schema: lines, patterns, aliases, column schemas, and FTS index availability.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| _(none)_ | — | — | — |

**Returns:** `lines[]` (with `columns[]` — `{name, type}` — and `total_rows`), `patterns[]` (with `relations[]`, `event_dimensions[]`), `aliases[]`

**Notes:** `columns` per line lists all searchable entity properties. `total_rows` is the entity count. `has_fts_index` on each line indicates FTS availability. `relations[].edge_max > 0` means that relation uses continuous edges (see [Continuous Edges](#continuous-edges)).

---

### `get_session_stats`

Returns performance and cache statistics for the current session.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| _(none)_ | — | — | — |

**Returns:** Cache hit/miss counts, geometry read counts, elapsed totals.

---

## Sphere Overview & Health

### `sphere_overview`

Population summary for all patterns (or one pattern). Returns anomaly rates, calibration health, geometry mode, and optional temporal/forecast data.

**Performance:** `detail="summary"` is instant (O(1)).  `detail="full"` runs event-rate-divergence scans that cross-reference event anomalies per anchor entity — on large spheres (>100K entities or >1M events) this can take **minutes**.  Use `"summary"` for interactive exploration; reserve `"full"` for deep-dive diagnostics.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | `null` | Scope to one pattern; omit for all |
| `detail` | string | `"summary"` | `"summary"` = O(1), no I/O; `"full"` = adds temporal_quality and calibration staleness (**slow on large spheres**) |

**Returns per pattern:**

| Field | Description |
|-------|-------------|
| `pattern_id` | Pattern identifier |
| `entity_count` | Total entities in the pattern |
| `anomaly_rate` | Fraction of entities above theta_norm |
| `theta_norm` | Anomaly threshold in z-score space |
| `mean_delta_norm` | Population mean distance from centroid |
| `geometry_mode` | `"binary"`, `"continuous"`, or `"mixed"` — see below |
| `calibration_health` | `"good"` (1–20% anomaly rate), `"suspect"` (<1% or >20%), `"poor"` (<0.1% or >30%) |
| `calibration_drift_pct` | Drift from calibrated mu/sigma since last full recalibration |
| `calibration_stale` | `true` when drift exceeds soft threshold (5%) |
| `calibration_blocked` | `true` when drift exceeds hard threshold (20%); appends blocked |
| `inactive_ratio` | Fraction of anchor entities at the dominant low-activity mode (only reported when >25% of population is below median). See below. |
| `has_temporal` | `true` when the pattern has temporal slices |
| `profiling_alerts[]` | Dimension-level outlier clusters detected at build time. Each entry: `{dimension, max, p99, ratio, alert}` where `alert` is `"extreme cluster"` (ratio > 3.0) or `"moderate cluster"` (ratio 1.5–3.0). Absent = no outlier concentration. |
| `trends[]` | Per-metric population forecasts when pre-computed data exists: `{metric, current_value, forecast_value, direction, horizon, reliability}`. Metrics: `anomaly_rate`, `mean_delta_norm`, `entity_count`. Direction: `"rising"`, `"falling"`, `"stable"`. Uses Holt's double exponential smoothing (alpha=0.3). |
| `temporal_quality` | (`detail="full"` only) `{signal_quality: "persistent"/"volatile"/"mixed"}` — persistence of anomaly signals across time slices |
| `event_rate_divergence_alerts[]` | (`detail="full"`, anchor patterns only) Entities with high event anomaly rate (>15%) but below-theta static delta_norm — invisible to `find_anomalies`. Each entry: `{pattern_id, event_pattern_id, entity_key, event_anomaly_rate, delta_norm, theta_norm, alert}`. Top 20 by rate. Absent = no divergence detected. |
| `suggested_next_step` | (`detail="full"`, only when `event_rate_divergence_alerts` present) Actionable hint to run windowed `aggregate(time_from, time_to)` to confirm when the event burst happened. |
| `dimension_kinds` | Compact summary of per-dimension distribution families, e.g. `"bernoulli x4, poisson x2, gaussian x8"`. Absent on pre-2.3 spheres. |

**Geometry mode meanings:**

| Mode | Description |
|------|-------------|
| `"binary"` | Edges encoded as 0/1 indicators. Anomaly detection has minimal gradation. |
| `"continuous"` | Edges have rich value distribution. Full analytical expressiveness. |
| `"mixed"` | Some dimensions binary, some continuous. Binary dimensions contribute less signal. |

**Inactive ratio interpretation:**

| Value | Meaning |
|-------|---------|
| < 0.10 | Most entities active; anomalies reflect genuine structural deviations |
| 0.10–0.50 | Significant inactive segment; mu is shifted toward inactivity |
| > 0.50 | Zombie-dominant population; `is_anomaly=true` often means "active entity", not "problematic entity" |

---

### `check_alerts`

Evaluates 6 built-in geometric health checks across all patterns and returns any triggered alerts.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| _(none)_ | — | — | — |

**Returns:** `alerts[]` sorted by severity (HIGH first). Alert types: `anomaly_rate_spike`, `population_size_shock`, `high_anomaly_rate` (>30%), `theta_miscalibration` (includes >20% detection), `regime_changepoint`, `calibration_drift`.

---

### `detect_data_quality_issues`

Scans a pattern for **geometric integrity** issues: coverage gaps, degenerate polygons, high/zero anomaly rates, theta ceiling effects. Checks structural quality only — domain-level outliers (unusual values, truncated dates) are not detected here; use `find_anomalies` for those.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to inspect |
| `sample_pct` | float | `null` | Fraction of geometry to scan (e.g. `0.05` for 5%) |

**Returns:** `issues[]` with type, severity, and description.

---

### `line_geometry_stats`

Edge distribution and coverage for one relation line within a pattern.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern containing the relation |
| `line_id` | string | required | Relation line to analyze |
| `sample_size` | int | `null` | Entities to sample |
| `sample_pct` | float | `null` | Fraction of geometry to scan |

**Returns:** Edge count distribution, coverage fraction, mean/std/percentiles.

**Warning:** O(n) full scan — 5–30 s on >500K entities. Do not call in a loop; use `aggregate` for bulk stats. Use only to diagnose a specific relation.

---

## Entity Discovery & Search

### `search_entities`

Exact-match entity lookup by a single property value.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `line_id` | string | required | Entity line to search |
| `property_name` | string | required | Column name |
| `value` | string | required | Exact value (case-sensitive). Bool columns accept `"true"`/`"false"`. |
| `limit` | int | `20` | Max results to return |

**Returns:** `results[]` (entity records), `total` (all matches), `returned` (slice size).

Use `limit=1` to count matches cheaply without fetching full payloads.

---

### `search_entities_fts`

Full-text BM25 search across all indexed string columns of a line.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `line_id` | string | required | Entity line to search |
| `query` | string | required | Token query — space-separated tokens are OR'd |
| `limit` | int | `20` | Max results; ordered by relevance (highest BM25 score first) |

**Returns:** `results[]` (no `total` field — FTS returns only the top-limit matches).

**Notes:** Requires INVERTED index built by GDSBuilder. Manually constructed spheres without this index raise an error; use `search_entities` instead. Check `has_fts_index` in `get_sphere_info`.

---

### `search_entities_hybrid`

Fuses ANN vector similarity and BM25 full-text search into one ranked result.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Reference entity key (e.g. `"100428738"`). Must be an actual entity key — NOT a line name or pattern ID. Obtain from `walk_line` or `search_entities`. |
| `pattern_id` | string | required | Pattern (determines geometry and line) |
| `query` | string | required | BM25 text query |
| `alpha` | float | `0.7` | Vector weight in fusion: 0.0 = pure text, 1.0 = pure vector |
| `top_n` | int | `10` | Number of results |
| `filter_expr` | string | `null` | Lance SQL predicate for pre-filtering candidates |

**Returns:** `results[]` sorted by `final_score` desc, each with `vector_score`, `text_score`, `final_score`, and optionally `properties`.

**Notes:** Requires INVERTED index. Check `has_fts_index` in `get_sphere_info`.

---

### `get_line_schema`

Returns column schema for a line.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `line_id` | string | required | Entity line |

**Returns:** `columns[]` (`{name, type}`), `total_rows`.

**Notes:** For spheres built with GDSBuilder, `get_sphere_info` already contains this data in memory (zero I/O). `get_line_schema` falls back to a Lance scan on legacy spheres without embedded columns.

---

### `get_line_profile`

Profiles a single property column directly from the points table — no event scan required.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `line_id` | string | required | Entity line |
| `property_name` | string | required | Column to profile |
| `group_by` | string | `null` | Column to group numeric stats by |
| `limit` | int | `50` | Max distinct values to return (string/bool columns) |

**Returns by column type:**

| Column type | Returns |
|-------------|---------|
| String / bool | Value distribution: distinct values + counts |
| Numeric | Statistics: min, max, mean, std, median |
| Timestamp | Range: min, max |

With `group_by`: returns per-group numeric stats (mean/std/min/max per group value).

```python
get_line_profile("accounts", "region")                            # string distribution
get_line_profile("accounts", "balance_volatility")                # numeric stats
get_line_profile("accounts", "balance_volatility", group_by="region")  # stats per region
```

---

## Navigation

### `goto`

Sets navigator position to a named entity on a line (π1 entry point).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `line_id` | string | required | Line containing the entity |

**Returns:** Entity properties at that position.

---

### `walk_line`

Walks one step along a line to the next or previous entity (π1).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `line_id` | string | required | Line to walk |
| `direction` | string | `"+"` | `"+"` = next entity, `"-"` = previous entity |

**Returns:** Current entity position after the step.

**Notes:** Requires current position to be a Point on the given line. On large lines (100k+ points) this reads the full points table on every call — prefer `search_entities` or `aggregate` for bulk traversal.

---

### `jump_polygon`

Jumps from current entity through a polygon edge to a related entity on another line (π2).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_line_id` | string | required | Destination line |
| `edge_index` | int | `0` | Which alive edge to follow when multiple exist |

**Returns:** Target entity properties, `total_edges_to_target` (use to discover multi-edge count).

**Multi-edge navigation:** When `total_edges_to_target > 1`, iterate with higher `edge_index` values to visit all related entities.

**Continuous edges:** Patterns with `edge_max > 0` store edge counts, not foreign keys (`point_key=""`). `jump_polygon` raises `ValueError` on these patterns — use `aggregate` or `group_by_property` instead.

```python
jump_polygon("operations")              # edge_index=0, response: total_edges_to_target=3
jump_polygon("operations", edge_index=1)
jump_polygon("operations", edge_index=2)
```

---

### `emerge`

Emerges from current polygon or solid to a synthetic Point (π4). Position becomes `line_id="emerged"`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| _(none)_ | — | — | — |

**Returns:** Synthetic Point with `entity_properties` populated only when called after `dive_solid` (null after `get_solid`).

Call `goto(primary_key, original_line_id)` to resume navigation.

---

### `get_position`

Returns the current navigator position without moving.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| _(none)_ | — | — | — |

**Returns:** Current position type (Point / Polygon / Solid), `primary_key`, `line_id`.

---

## Geometry

### `get_polygon`

Reads the polygon (current geometric shape) for the entity at the current navigator position.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to read geometry from |

**Returns:**

| Field | Description |
|-------|-------------|
| `delta` | Z-scored delta vector — each component is `(shape − mu) / sigma` |
| `delta_norm` | L2 norm of delta vector |
| `delta_rank_pct` | Percentile in population delta_norm distribution (0–100) |
| `is_anomaly` | `true` when `delta_norm > theta_norm` |
| `edges[]` | Alive edges: `{line_id, point_key, direction}` |
| `anomaly_dimensions[]` | When `is_anomaly=true`: top dimensions driving the anomaly — `{dim, label, delta, contribution_pct}`. `contribution_pct` = % of `delta_norm²` from this dimension; dimensions < 5% excluded; top 3 shown. |
| `witness` | Minimal subset of dimensions that certifies the anomaly alone: `{witness_size, witness_dims[], delta_norm}` |
| `repair` | Minimal subset of dimensions to fix to become non-anomalous: `{repair_size, repair_dims[], residual_norm}` |
| `conformal_p` | (precision stack) Calibrated p-value — lower = more anomalous |
| `n_anomalous_dims` | (precision stack) Count of dimensions above p99 threshold |
| `bregman_divergence` | Distribution-aware anomaly distance (sum of per-dimension Bregman terms). `null` on pre-2.3 spheres. |
| `anomaly_confidence` | Bootstrap stability score (0–1). `null` when bootstrap was skipped (N > 50K, `group_by_property`, `use_mahalanobis`). |

---

### `get_solid`

Reads the temporal solid (deformation history) for an entity without changing navigator position.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern to read from |
| `timestamp_from` | string | `null` | ISO-8601 lower bound (inclusive) to reduce payload |
| `timestamp_to` | string | `null` | ISO-8601 upper bound (exclusive) to reduce payload |

**Returns:** `slices[]` (temporal snapshots), `num_slices`, `base_polygon` (shape at first observation).

When `num_slices >= 3`, also returns `forecast`:

| Forecast field | Description |
|----------------|-------------|
| `predicted_delta_norm` | Predicted `‖delta‖` at next version |
| `forecast_is_anomaly` | Will the entity cross the anomaly threshold? |
| `current_is_anomaly` | Is it anomalous now? |
| `reliability` | `"high"` (≥10 slices, r²≥0.7), `"medium"` (≥5 slices or r²≥0.4), `"low"` |
| `stale_forecast_warning` | Present when last temporal slice is >180 days old; `reliability` overridden to `"low"` |

**Notes:** Does not change navigator position. Event patterns are immutable (`num_slices` always 0). For current geometric state use `get_polygon`; `base_polygon.delta_norm` reflects the entity at first observation, not current state.

---

### `get_event_polygons`

Lists polygons for a specific entity in an event pattern.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity_key` | string | required | Entity key |
| `pattern_id` | string | required | Event pattern |
| `limit` | int | `10` | Max polygons to return |
| `offset` | int | `0` | Skip first N polygons (pagination) |
| `filters` | list[dict] | `null` | Record-level field filters on the event line: `[{"line": "company_codes", "key": "CC-PL"}]`. Do not use for `is_anomaly` — use `geometry_filters` instead. |
| `geometry_filters` | dict | `null` | Filter by geometry columns before returning. See [Geometry Filters](#geometry-filters). |
| `sample` | int | `null` | Draw exactly N random polygons |
| `sample_pct` | float | `null` | Draw a fraction (0.0–1.0) of polygons. Mutually exclusive with `sample`. |
| `seed` | int | `null` | Random seed for reproducible sampling |

**Returns:** `polygons[]`, `total` (unfiltered count for entity), `total_unfiltered` (all entries regardless of filters), `capped_warning` when limit was reduced by the adaptive cap.

**Hard cap:** Adaptive — computed from edge count to stay under ~50K chars (e.g. 15 for 10-edge patterns, 18 for 8-edge patterns). Use `offset` for pagination or `aggregate` for bulk counts.

```python
get_event_polygons("2", "tx_pattern", limit=10, offset=0)   # page 1
get_event_polygons("2", "tx_pattern", limit=10, offset=10)  # page 2
```

---

## Anomaly Detection

### `find_anomalies`

Finds the most anomalous polygons in a pattern, ranked by `delta_norm` descending.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to scan |
| `top_n` | int | `10` | Max results (subject to adaptive hard cap) |
| `offset` | int | `0` | Skip first N anomalies (pagination) |
| `radius` | float | `1.0` | Multiplies `theta_norm` threshold. >1 = looser boundary. ≤0 treated as 1. |
| `property_filters` | dict | `null` | Filter anomalous entities by property before ranking. Anchor/composite only. Syntax: `{"col": {"gt": X, "lt": Y}}` or `{"col": "value"}`. AND semantics. |
| `rank_by_property` | string | `null` | Re-rank by raw property value (DESC) instead of delta_norm |
| `missing_edge_to` | string | `null` | Keep only anomalies with NO edge to this line (orphan detection) |
| `include_emerging` | bool | `false` | Append `emerging[]`: non-anomalous entities whose forecast crosses the threshold. Scans up to 100 entities. Only evaluated when `offset=0`. |
| `fdr_alpha` | float | `null` | Apply Benjamini-Hochberg FDR control at this level (0-1 exclusive). Returns only entities with `q_value <= alpha`. Each retained entity carries a `q_value` field. `null` = no FDR filtering (legacy behavior). |
| `fdr_method` | string | `"bh"` | FDR method. `"bh"` (Benjamini-Hochberg, assumes pi0=1) or `"storey"` (Storey LSL estimator of the true null proportion; shrinks q-values by pi0 and typically recovers 10–15% more discoveries when combined with `p_value_method="chi2"` on spheres that have a genuine null mass). With the default `p_value_method="rank"`, `"storey"` collapses to `"bh"` — rank p-values are uniform by construction and carry no null signal. |
| `p_value_method` | string | `"rank"` | p-value construction. `"rank"` (default, empirical from `delta_rank_pct` — uniform by construction) or `"chi2"` (upper-tail χ²(df) survival on `||delta||²`, the parametric null assuming `delta_i ~ N(0, 1)`). Pair with `fdr_method="storey"` for power recovery on moderate-super-anomaly patterns; on over-compressed or extreme patterns the uplift collapses to zero. |
| `select` | string | `"top_norm"` | `"top_norm"` ranks by score descending. `"diverse"` applies submodular facility location to pick the K most geometrically diverse representatives — each result includes a `representativeness` count. |
| `metric` | string | `"L2"` | `"L2"` (pre-computed delta_norm, fast), `"Linf"` (max single-dimension \|delta\|, runtime scan), or `"bregman"` (distribution-aware Bregman divergence, runtime scan). Linf catches single-dimension spikes that L2 dilutes. Bregman uses per-dimension kind-aware scoring (poisson KL for counts, bernoulli KL for binary, gaussian for continuous) — can improve ranking on mixed-type patterns. |
| `min_confidence` | float | `0.0` | Keep only entities with `anomaly_confidence >= min_confidence` (0–1). `0.0` = no filter. Has no effect when `anomaly_confidence` is `None` (bootstrap was skipped). |

**Returns:** `polygons[]`, `total_found` (total above threshold), `capped_warning` when top_n was reduced.

Each polygon in `polygons[]` includes:

| Field | Description |
|-------|-------------|
| `bregman_divergence` | Distribution-aware anomaly distance (sum of per-dimension Bregman terms). `null` on pre-2.3 spheres. |
| `anomaly_confidence` | Bootstrap stability score (0–1): fraction of bootstrap resamples in which the entity is classified as anomalous. `null` when bootstrap was skipped (N > 50K, `group_by_property`, `use_mahalanobis`). |
| `total_impact` | M4 additive — aggregate L2 norm of leave-one-out impact on coordinate calibration. `null` when pattern is event-type, `N<2`, or storage backend lacks shape reconstruction prerequisites. Use `find_calibration_influencers` for the full per-dim breakdown + classification context. |
| `classification` | M4 additive — one of `"hidden"` / `"distorter"` / `"standard_anomaly"` / `"normal"`. Same null rules as `total_impact`. Use `find_calibration_influencers` for ranked entries within a specific cell. |

**Hard cap:** Adaptive — edge-count-based, typically 15–51. Use `offset` to paginate, `anomaly_summary` for counts, or `aggregate_anomalies` for distribution analysis.

**Pagination example:**
```python
page0 = find_anomalies("tx_pattern", top_n=25, offset=0)
page1 = find_anomalies("tx_pattern", top_n=25, offset=25)
# Stop when offset >= page0["total_found"]
```

**Emerging anomalies** (`include_emerging=True`): each entry has `key`, `predicted_delta_norm`, `current_delta_norm`, `reliability`, `horizon`. Requires ≥3 temporal slices per entity.

---

### `anomaly_summary`

Statistical overview of the anomaly population for a pattern.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to summarize |
| `max_clusters` | int | `20` | Maximum anomaly clusters to return. Continuous-dimension patterns can produce 50k+ unique cluster shapes — the default cap keeps response size manageable. Set `0` for unlimited. |

**Returns:** `count`, `rate`, `clusters[]` (anomaly shape clusters), `delta_norm_percentiles` (`p50/p75/p90/p95/p99/max`), `top_driving_dimensions[]` (aggregate per-dimension contribution across all anomalies).

---

### `aggregate_anomalies`

Aggregates the anomaly population by a property column — useful for understanding distribution without pagination.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to aggregate |
| `group_by` | string | required | Property column name to group by |
| `top_n` | int | `50` | Max groups to return, sorted by anomaly_count descending |
| `sample_size` | int | `null` | Subsample N entities from the anomaly population (for patterns with >500K entities) |
| `sample_pct` | float | `null` | Fraction to sample (mutually exclusive with `sample_size`) |
| `include_keys` | bool | `false` | When true, each group includes up to `keys_per_group` entity keys as a sample |
| `keys_per_group` | int | `5` | Number of sample keys to include per group when `include_keys=true` |
| `property_filters` | dict | `null` | Same syntax as `find_anomalies.property_filters` — narrows population before grouping |

**Returns:** `groups[]` (`{value, count}`), `total_anomalies`, `ungrouped_anomalies` (count of anomalies where the group column is null or missing).

---

### `explain_anomaly`

Full structured explanation combining severity, witness, repair, top dimensions, conformal p-value, reputation, and composite risk.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `pattern_id` | string | required | Pattern |

**Returns:** `severity` (`"normal"` / `"low"` 1.0–1.1× / `"medium"` 1.1–1.5× / `"high"` 1.5–2.5× / `"extreme"` >2.5× theta), `ratio`, `witness`, `repair`, `conformal_p`, `reputation` (`{value, anomaly_tenure}`), `composite_risk`, `top_dimensions[]`.

Each entry in `top_dimensions[]` has `dim` (dimension index), `label` (dimension name), `kind` (`"gaussian"`, `"poisson"`, or `"bernoulli"`, present when sphere has dimension kinds), `bregman` (raw per-dimension Bregman value), and `pct_of_total` (% of total `bregman_divergence` from this dimension). Absent on pre-2.3 spheres.

---

### `trace_root_cause`

Multi-hop root-cause DAG for an anomalous entity. Composes `explain_anomaly` (top witness dimensions) with `find_counterparties` (edge-derived witness follow, **sorted by anomaly — not transaction volume**), `contagion_score` (neighbour anomaly share with explicit anomalous counterparty keys), and `π7_attract_hub` (hub concentration) into one bounded tree. Candidate branches are scored on a unified severity scale and the top `max_branches` are kept — tree is priority-ordered, not FIFO. Replaces the prior `explain_anomaly_chain` (linear same-similar walk).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Anomalous entity to trace |
| `pattern_id` | string | required | Pattern the entity lives in |
| `max_depth` | int | `2` | Max hops away from root (0 = root only) |
| `max_branches` | int | `3` | Max children kept per node after priority sort |
| `hub_pop_limit` | int | `50_000` | Skip hub branch when the pattern has more than this many entities (π7 is O(n) — not worth it on 500k+ populations) |
| `contagion_min_threshold` | float | `0.10` | Below this score, the contagion branch is not attached. Set to `0.0` to always attach when the entity has counterparties, or above `0.5` to keep only high-signal contagion |
| `max_total_nodes` | int | `50` | Hard cap on total nodes expanded across the whole DAG; guards against recursion blowups on `max_depth` × `max_branches` combos |
| `edge_counterparty_top_n` | int | `1` | How many of the most-anomalous counterparties to expand as edge_counterparty branches. Raise to 2–3 when you want multiple distinct counterparty chains traced; each adds one candidate competing for `max_branches` slots |

**Returns:**

| Field | Description |
|-------|-------------|
| `root` | Nested tree dict: `{entity_key, role, severity, evidence, children}` |
| `summary` | One-line natural-language summary of the trace |
| `hop_count` | Number of nodes expanded |
| `branches_explored` | Total branches that yielded evidence |
| `truncated` | `true` iff at least one candidate was dropped because of `max_branches` OR the `max_total_nodes` cap was hit |

**Severity scale (unified across all nodes):** `"normal"` < `"low"` < `"moderate"` < `"high"` < `"critical"` < `"extreme"`.

**Contagion grading:** score < `contagion_min_threshold` → no branch, else `"low"` (≥ threshold), `"moderate"` (≥ 0.25), `"high"` (≥ 0.50), `"critical"` (≥ 0.75).

**Role values:** `"root"`, `"edge_counterparty"`, `"hub"`, `"neighbor_contamination"`.

**Contagion branch evidence includes:** `contagion_score`, `total_counterparties`, `anomalous_counterparties` (count), and `anomalous_cp_keys` (list of up to 10 anomalous counterparty primary keys — saves a follow-up `find_counterparties` call).

**Notes:** Returns a single-node tree with `severity="normal"` when the entity is not anomalous. Cycles are broken by a visited-set with a `cycle: true` evidence marker on the repeat node. The hub cache is version-keyed — a pattern rebuild automatically invalidates cached hubs.

---

### `check_anomaly_batch`

Checks anomaly status for a batch of entity keys.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_keys` | list[string] | required | Entity keys to check |
| `pattern_id` | string | required | Pattern |
| `line_id` | string | `null` | Entity line — optional, used for geometry resolution |

**Returns:** `results[]` — one entry per key with `is_anomaly`, `delta_rank_pct`.

---

## Similarity & Comparison

### `find_similar_entities`

Finds the top-N entities nearest to a given entity by geometric distance.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Reference entity |
| `pattern_id` | string | required | Pattern |
| `top_n` | int | `5` | Number of results (silent hard cap: 50) |
| `filter_expr` | string | `null` | Lance SQL predicate to pre-filter candidates |
| `missing_edge_to` | string | `null` | Keep only similar entities with NO edge to this line |
| `dim_mask` | list[string] | `null` | Compute distance only on named dimensions (from `pattern.dim_labels`). Focuses similarity on specific aspects of geometry. |
| `metric` | string | `"L2"` | `"L2"` (Euclidean, default) or `"cosine"` (1 - cos_sim — shape similarity ignoring magnitude) |

**Returns:** `results[]` with `primary_key`, `distance`, `delta_norm`, `is_anomaly`. When >50% of results have `distance=0` (inactive entities), response includes `degenerate_warning` and `population_diversity_note` — ANN search is unreliable on patterns with high `inactive_ratio`.

---

### `compare_entities`

Measures geometric distance between two entities.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key_a` | string | required | First entity key |
| `key_b` | string | required | Second entity key |
| `pattern_id` | string | required | Pattern |
| `mode` | string | `"intraclass"` | `"intraclass"` = Euclidean delta distance; `"temporal"` = DTW over slice sequences |

**Returns:** `distance`, `interpretation`, plus polygon details (intraclass) or slice counts (temporal).

---

### `compare_calibrations`

Per-dimension μ/σ/θ drift between two calibration epochs of one pattern. Diagnostic for inspecting how a pattern's calibration shifted between two builder rebuilds.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Which pattern to inspect |
| `v_from` | int | `null` | Starting epoch. `null` resolves to second-to-last epoch on disk |
| `v_to` | int | `null` | Ending epoch. `null` resolves to latest epoch on disk |
| `top_n` | int | `10` | Number of top-drifted dimensions to return |
| `verbose` | bool | `false` | When true, also include the full per-dimension breakdown in `per_dimension` |

**Returns:** JSON-encoded `CalibrationDriftReport` with `pattern_id`, `v_from`, `v_to`, `schema_hash`, `population_size_from`, `population_size_to`, `overall_drift_rms` (RMS in σ units, comparable across patterns), `top_drifted` (ranked list of `DimensionDrift`), and `per_dimension` (full list when `verbose=true`, else `null`). Each `DimensionDrift` carries `dim_index`, `dim_kind`, the from/to/delta triples for `mu`, `sigma`, `theta`, and `mu_delta_normalized` (z-score with sigma-safe guard for degenerate dims).

**Errors:**
- `ValueError` on `v_from == v_to`, single-epoch auto-resolve (only one epoch on disk), or schema_hash mismatch (cross-schema mu vectors are not dimensionally comparable).
- `CalibrationNotFoundError` from missing versions (trimmed by GC, schema bump wiped history).

**Use after** a builder rebuild to inspect calibration shifts; complementary to `compare_time_windows` (which compares geometry across temporal slices of a single fit) and `compare_entities` (which compares two entities under the same fit).

---

### `decompose_drift`

Per-entity intrinsic vs extrinsic decomposition of geometric drift between two temporal slices, viewed across two calibration epochs.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity_key` | string | required | Which entity to decompose |
| `pattern_id` | string | required | Anchor pattern with temporal data |
| `v_from` | int | `null` | Starting calibration epoch. `null` resolves to oldest retained on disk |
| `v_to` | int | `null` | Ending calibration epoch. `null` resolves to latest on disk |
| `timestamp_from` | float | `null` | Unix-seconds lower bound for slice window. `null` → first slice |
| `timestamp_to` | float | `null` | Unix-seconds upper bound for slice window. `null` → last slice |
| `top_n` | int | `10` | Number of top dimensions (by `|total|`) to return |
| `verbose` | bool | `false` | When true, also include full per-dimension breakdown |

**Returns:** JSON-encoded `IntrinsicExtrinsicReport` with `pattern_id`, `entity_key`, `v_from`, `v_to`, `schema_hash`, `timestamp_from`, `timestamp_to`, aggregate `intrinsic_displacement` / `extrinsic_displacement` / `total_displacement` / `intrinsic_fraction` (sum-of-squares ratio in `[0, 1]`), ranked `top_dimensions`, and optional `per_dimension` (when `verbose=true`). Each `DimensionDecomposition` carries `dim_index`, `dim_kind`, `dim_label`, `total` (delta_b - delta_a), `intrinsic` ((s_b - s_a) / σ_v1), `extrinsic` (residual), and per-dim `intrinsic_fraction`.

**Errors:**
- `ValueError` on `<2` retained calibration epochs, `v_from == v_to`, schema_hash mismatch, `<2` slices in window, or event pattern.
- `CalibrationNotFoundError` from missing versions.

**Use after** a builder rebuild + sufficient temporal history accumulation to ask: "did THIS entity move, or did the population calibrate around it?". Complementary to `compare_calibrations` (population-level shift between epochs) and `find_drifting_entities` (which now carries the same 3 scalar fields per entity for batch monitoring).

---

### `find_calibration_influencers`

Detect entities with high influence on the population-relative coordinate system. Classifies into a 4-cell influence × anomaly matrix (hidden / distorter / standard_anomaly / normal). Includes cascading reclassification (`cascading_flip_count` per entry).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern. |
| `top_n` | int | `10` | Max results (hard cap 50). |
| `classify` | string | `"hidden"` | Filter: `"hidden"` / `"distorter"` / `"standard_anomaly"` / `"normal"` / `"all"`. |
| `high_threshold_pct` | float | `90.0` | Percentile cutoff for "high impact" classification. |
| `sample_size` | int | `null` | Subsample N entities before leave-one-out scan. |
| `verbose` | bool | `false` | When true, each entry gains `cascading_flip_count` — count of OTHER entities that flip is_anomaly classification after this entity's removal. Adds O(top_n × N × D) recompute. |

**Returns:** JSON-encoded `InfluenceReport` with `pattern_id`, `pattern_version`, `population_size`, `high_threshold_pct`, `total_impact_threshold` (absolute value at percentile), `theta_norm` (echoed), `classify_filter` (echoed), `cell_counts` (population-level distribution: `{hidden: K1, distorter: K2, standard_anomaly: K3, normal: K4}` summing to N), and `entries` (filtered + sorted by `total_impact` desc, ≤ top_n). Each `InfluenceEntry` carries `entity_key`, `mu_impact`, `sigma_impact`, `total_impact`, `delta_norm` (current anomaly score), `classification`, `top_dim_contributions` (top 5 by `|contribution|`), and `cascading_flip_count` (null unless verbose=True). Each `DimensionContribution` carries `dim_index`, `dim_kind`, `dim_label`, `mu_shift`, `sigma_shift`, `contribution`.

**Math:** exact leave-one-out via rolling Σs/Σs². For each entity E:
- `μ_without[i] = (Σs[i] - s_E[i]) / (N-1)`
- `σ²_without[i] = (Σs²[i] - s_E[i]²) / (N-1) - μ_without[i]²`
- `mu_impact = ‖(μ_full - μ_without) / σ_full_safe‖`
- `sigma_impact = ‖(σ_full - σ_without) / σ_full_safe‖`
- `total_impact = sqrt(mu_impact² + sigma_impact²)`

Classification: `high_impact = total_impact ≥ percentile(total_impact, high_threshold_pct)`; `high_anomaly = ‖δ(E)‖ ≥ θ_norm`.

**Errors:** `ValueError` on event pattern, `N<2`, `high_threshold_pct ∉ (0, 100)`, invalid `classify`, or `top_n ∉ [1, 50]`.

**Use after** running `find_anomalies` to ask "which of these are calibration distorters that should be excluded vs hidden influencers that quietly define what 'normal' means?". Common operational triggers: data-quality audit, adversarial AML population manipulation detection.

---

### `find_group_influence`

Per-group leave-set-out impact + reinforcing/canceling factor (caller-supplied form). Detects coordinated population-shift attacks where individual entities have small impact but a group of coordinated entities together moves μ/σ.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern. |
| `groups` | `list[list[string]]` | required | List of groups; each group is a list of entity_keys. |

**Returns:** JSON-encoded `list[GroupInfluenceReport]` (input order preserved). Each report carries `pattern_id`, `pattern_version`, `group_index`, `member_count`, `members` (echoed entity_keys), `mu_impact_set`, `sigma_impact_set`, `total_impact_set`, `sum_individual_impacts` (Σ of per-entity total_impact), `reinforcing_factor = total_impact_set / sum_individual_impacts` (>1.0 reinforcing, <1.0 canceling), and `top_dim_contributions` (top 5 dims of group's collective shift).

**Errors:** `ValueError` on event pattern, `N<3`, empty groups list, group with `<2` members, group `≥ N`, missing entity_key, duplicate entity in group, or undefined reinforcing factor (sum of individual impacts = 0).

**Use** after a candidate-set forms (e.g. via `find_witness_cohort`, `cluster_bridges`, or co-anomalous account selection) to ask "is this set coordinating — together they shape calibration more than sum of individuals?". `reinforcing_factor > 1.5` on AML data is a signature of collusion rings or duplicate-record contamination.

---

### `find_motif_by_hops`

Declarative motif API — escape hatch from the closed-vocab `find_motif` registry. Caller passes a list of dicts describing per-hop constraints (`amount_min`, `amount_max`, `time_delta_max_hours`, `amount_ratio_to_prev`, `direction` (`"forward"` / `"reverse"` / `"any"`), `edge_dim_predicates: {dim: [op, value]}`) and the navigator walks the edge table via level-synchronous BFS for matching chains of length 1..8.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Event pattern with edge_table |
| `hops` | list of dict | required | 1..8 per-hop predicate dicts |
| `seed_keys` | list of string | `null` | Restrict to these seeds; `null` = all `from_key`s |
| `max_results` | int | `100` | Cap on returned motif instances |
| `score` | bool | `false` | When set, score each motif as the product of event-aware edge_potential (`delta_distance × (1/effective_pair_count) × (1 + event_norm)`) across its edges using the resolved anchor-companion's per-entity geometry plus the event pattern's per-transaction polygons. Distinct transactions between the same accounts now produce distinct motif scores (no rank collapse on shared node sequences). Each scored motif gains `score`, `score_breakdown` (per-edge `event_factor` included), and `anchor_pattern_id` fields together. Output sorted descending on score, unscored motifs at tail. Raises when no anchor companion is configured for the queried event pattern. |
| `time_window_hours` | float | `null` | Optional total-chain-span cap. When set, every hop after the first must satisfy `abs(current_edge_ts - first_edge_ts) <= time_window_hours`. Independent of per-hop `time_delta_max_hours`; both apply when both are set. Must be strictly positive when not `null` |

**Per-hop dict fields:** `amount_min: float`, `amount_max: float`, `time_delta_max_hours: float`, `amount_ratio_to_prev: float` (decreasing-chain ratio in `(0, 1.0]`; rejects edge unless `current_amount / prev_hop_amount ≤ ratio`; must be omitted on `hops[0]`), `direction: "forward"|"reverse"|"any"`, `edge_dim_predicates: {dim_name: [op, value]}` (op ∈ `<`, `<=`, `>`, `>=`, `==`), `require_anomalous_entity: bool` (when `true`, the hop's destination entity — `nodes[i+1]` of the motif — must satisfy `is_anomaly=true` in the resolved anchor companion's geometry; multiple hops AND together; raises if no anchor companion configured; `max_results` applies AFTER the filter).

**Returns:** JSON object with `pattern_id`, `n_results`, `motifs` (each carrying `nodes`, `edges`, `timestamps`, `amounts`, optional `dim_values_per_hop`; when `score=true` succeeds for the motif, also `score`, `score_breakdown`, and `anchor_pattern_id` together).

**Smart-mode keywords:** *custom motif*, *hop predicate*, *edge dim filter motif*, *motif by hops*, *decreasing chain*, *structuring chain*.

**Use** when the closed-vocab motif library doesn't fit — express ad-hoc temporal-amount-edge_dim chains without a Python PR.

---

### `find_density_gaps`

Joint density gap detection via probability integral transform plus independence null. For an anchor pattern, build a uniform-marginal `bins × bins` 2D histogram on selected dim pairs and flag bins whose observed count is significantly below the uniform-independence expectation. Each flagged bin maps back to a named delta-space (z-score) range with a BH-corrected q-value. Note: `delta_range_*` is in delta units (geometry z-scores), not raw property values; raw-unit mapping is a follow-up.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern id (event patterns get few usable pairs) |
| `top_n` | int | `10` | Max gap cells to return, sorted by `expected/observed` ratio desc |
| `dim_pairs` | list of `[a, b]` | `null` | Optional explicit pairs by dim name; otherwise auto-select top-20 in `[r_min, r_max]` |
| `bins` | int | `10` | Histogram resolution per axis; range `[4, 50]` |
| `alpha` | float | `0.05` | BH significance level |
| `r_min` | float | `0.1` | Lower bound on Pearson `|r|` for auto pair selection |
| `r_max` | float | `0.7` | Upper bound on Pearson `|r|` for auto pair selection |
| `sample_size` | int | `100000` | Max entities to read (random sample when sphere is larger). Pass `0` to read all entities. |

**Returns:** JSON object with `pattern_id`, `n_entities`, `n_pairs_tested`, `excluded_dims` (list of `{dim, reason}`), and `gaps` (each cell carrying `dim_i`, `dim_j`, `delta_range_i`, `delta_range_j`, `u_range_i`, `u_range_j`, `observed`, `expected`, `ratio`, `p_value`, `q_value`, `is_gap`, `correlation`).

**Smart-mode keywords:** *missing segment*, *density gap*, *dark matter*, *under-represented*, *missing combination*, *anomaly by absence*.

**Use** to surface "anomaly by absence" — combinations of feature values that the independence null says should be populated but are not. Complementary to `find_anomalies` (which surfaces present-but-unusual entities); together they cover both directions of structural surprise.

---

### `find_lead_lag`

Cross-pattern temporal lead-lag in population-relative coordinates. Population-aggregated centroid drift series cross-correlation between two anchor patterns plus per-dim D_A × D_B matrix with BH or Storey FDR.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_a` | string | required | First anchor pattern id |
| `pattern_b` | string | required | Second anchor pattern id (must differ from pattern_a) |
| `timestamp_from` | string | `null` | ISO-8601 lower bound (inclusive) |
| `timestamp_to` | string | `null` | ISO-8601 upper bound (exclusive) |
| `cohort` | string | `"fixed"` | `"fixed"` (entities present at every common epoch in both patterns) or `"all"` (per-epoch present entities) |
| `min_epochs` | int | `8` | Hard floor on the timestamp intersection; raises if violated |
| `max_lag` | int | `null` | Default = `(N - 1) // 4` |
| `fdr_alpha` | float | `0.05` | FDR level for the per-dim matrix |
| `fdr_method` | string | `"storey"` | `"bh"` or `"storey"` |
| `verbose` | bool | `false` | Include full `D_A × D_B` matrix in `per_dim_pairs` |
| `entity_key` | string | `null` | Per-entity drill-down: replace population centroid by this entity's delta trajectory |

**Returns:** JSON-encoded `LeadLagReport` with:

| Field | Description |
|-------|-------------|
| `lag` | Peak lag (epochs); positive = pattern_a leads pattern_b |
| `correlation` | Pearson correlation at peak lag (population centroid drift) |
| `lag_volatility`, `correlation_volatility` | Same on the volatility (mean step magnitude) confirmation series |
| `agreement` | `"strong"` / `"weak"` / `"divergent"` — match between centroid and volatility peaks |
| `is_significant` | `abs(correlation) > max_corr_threshold` (Bonferroni-adjusted peak threshold) |
| `bartlett_ci_95` | Single-test 95 % CI; informational |
| `max_corr_threshold` | The actual peak-adjusted cut-off |
| `reliability` | `"high"` (N-1 ≥ 24), `"medium"` (≥ 12), else `"low"` |
| `degenerate_signal` | `true` when either centroid drift series has zero variance (constant population) — agreement forced to `"divergent"` |
| `top_dim_pairs[]` | Top-10 `(dim_a, dim_b)` pairs sorted by ascending q-value, ties broken by descending |corr| |
| `per_dim_pairs[]` | Full sorted matrix when `verbose=true` |
| `centroid_drift_series_a/b`, `volatility_series_a/b`, `correlation_by_lag` | Raw arrays for downstream agent analysis |
| `n_epochs_used`, `n_dropped_a/b`, `cohort_size`, `cohort_dropped`, `coverage_warning` | Window + cohort diagnostic |

**Use cases:** "behavior leads stress" workflows on shared-entity-space patterns (e.g. Berka `account_behavior_pattern` × `account_stress_pattern`); cross-pattern monitoring when both patterns flag drift simultaneously; per-entity timelines via `entity_key`.

**Limits:** Cross-pattern lead-lag is well-defined only when both patterns share entity population (same account PKs etc.). On completely disjoint entity spaces (e.g. AML accounts vs chain entities) `cohort="fixed"` raises "empty cohort — no entities present at every epoch in both patterns" and `cohort="all"` raises "tensor budget exceeded". Use `entity_key=<id>` for per-entity drill-down if a specific entity appears in both patterns' temporal histories.

**Errors:** Raises `ValueError` on event pattern, `pattern_a == pattern_b`, intersection below `min_epochs`, empty fixed cohort (disjoint entity populations — use `entity_key=<id>` for per-entity drill-down), `entity_key` not present in both patterns over the window, `max_lag` too large for trimmed window, or tensor budget exceeded (>1 GB for cross-population queries).

---

### `find_common_relations`

Finds shared alive edges between two entities in a pattern.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key_a` | string | required | First entity key |
| `key_b` | string | required | Second entity key |
| `pattern_id` | string | required | Pattern |

**Returns:** `common_count`, `common_relations[]` (`{line_id, point_key}`).

---

## Aggregation

### `aggregate`

Aggregates event polygon data. Equivalent to SQL `SELECT metric FROM pattern GROUP BY line`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `event_pattern_id` | string | required | Event pattern to aggregate |
| `group_by_line` | string | required (unless `group_by_property`) | Line to group by (one row per entity on this line) |
| `group_by_line_2` | string | `null` | Second grouping line — two-dimensional aggregation |
| `group_by_property` | string | `null` | `"line_id:column"` — group by entity property value instead of entity key |
| `metric` | string | `"count"` | Metric to compute — see metric types below |
| `filters` | list[dict] | `null` | Edge filters: `[{"line": "districts", "key": "Prague"}]` or `{"line": "transactions", "key": "type:PRIJEM"}`. AND semantics. |
| `property_filters` | dict | `null` | Filter `group_by_line` entities by their own properties: `{"col": {"gt": X}}` or `{"col": "value"}`. AND semantics. |
| `geometry_filters` | dict | `null` | Filter by geometry columns before aggregation. See [Geometry Filters](#geometry-filters). |
| `event_filters` | dict | `null` | Filter event-line rows by column predicates. Operators: `gt`, `gte`, `lt`, `lte`, `eq`, `null`, `not_null`. See [Event Filters](#event-filters). |
| `time_from` | string | `null` | ISO-8601 lower bound (inclusive) on the event's timestamp column. Requires `timestamp_col` on the event pattern (set via `temporal` config in sphere.yaml). |
| `time_to` | string | `null` | ISO-8601 upper bound (exclusive). Half-open interval `[time_from, time_to)`. Use for windowed frequency analysis. |
| `entity_filters` | dict | `null` | Filter polygon scope by the pattern's own root entity properties. Same dict syntax as `property_filters`. Returns `entity_filtered_count` in response. |
| `having` | dict | `null` | Post-aggregation group filter: `{"gt": X}` or `{"gte": X, "lte": Y}`. Returns `total_groups` (pre-having) and `having_matched`. Not supported with `pivot_event_field`. |
| `filter_by_keys` | list[string] | `null` | Scope to specific polygon `primary_key` values. Applied after `geometry_filters`. Empty list returns 0 results. |
| `missing_edge_to` | string | `null` | Filter to polygons with NO edge to this line (orphan/data-gap analysis). Composes with all other filters (AND). |
| `include_properties` | list[string] | `null` | Attach entity properties to each result row. Avoid on high-cardinality group_by_line (>100 groups). Response includes `include_properties_warning` when payload exceeds threshold. |
| `distinct` | bool | `false` | Count unique `group_by_line` entities per `group_by_property` tier. See [distinct usage](#distinct-and-collapse). |
| `collapse_by_property` | bool | `false` | Collapse `group_by_property` results to per-tier rows. Requires `group_by_property`. Incompatible with `distinct=True`. |
| `pivot_event_field` | string | `null` | Pivot by event line column — wide output. Cannot combine with `group_by_property`. |
| `sort` | string | `"desc"` | `"desc"` = highest first; `"asc"` = lowest first (bottom-N queries) |
| `limit` | int | `20` | Max groups to return |
| `offset` | int | `0` | Skip first N results (pagination) |
| `sample_size` | int | `null` | Random subsample of N polygons before aggregation |
| `sample_pct` | float | `null` | Fraction to sample (mutually exclusive with `sample_size`) |
| `seed` | int | `null` | Random seed for reproducible sampling |

**Metric types:**

| Metric | Description |
|--------|-------------|
| `"count"` | Count polygons per group |
| `"sum:<col>"` | Sum of numeric column |
| `"avg:<col>"` | Average of numeric column |
| `"min:<col>"` | Minimum of numeric column |
| `"max:<col>"` | Maximum of numeric column |
| `"median:<col>"` | Median of numeric column |
| `"pct90:<col>"` | 90th percentile of numeric column |
| `"count_distinct:<line_id>"` | Count unique entities on a related line per group. Cannot combine with `group_by_property`, `distinct`, `pivot_event_field`, or `event_filters`. |

**Returns:** `results[]` (`{key, name, value, count}`), `total_groups`, `having_matched` (when `having` set), `sampled` / `sample_size` / `total_eligible` (when sampling).

**Two-dimensional grouping** (`group_by_line_2`): each row has `key` + `key_2`, `value`, `count`, `label` + `label_2`. Cannot combine with `group_by_property` or `pivot_event_field`. Both lines must be declared relations in the pattern.

**Dead entities:** `aggregate` only returns entities appearing as edges in at least one polygon. Entities with no polygon connections are excluded from `total_groups`. Compare with `search_entities(total)` to detect zero-event entities.

**Lance SQL acceleration:** All aggregate paths (count, sum, avg, min, max, pivot, percentile) push GROUP BY into the Lance scanner via built-in DataFusion. No extra install required.

**Examples:**
```python
# Count per district
aggregate("tx_pattern", group_by_line="districts", metric="count")

# Sum with edge filter
aggregate("tx_pattern", "districts", metric="sum:amount",
          filters=[{"line": "districts", "key": "region:Prague"}])

# Anomaly count per district
aggregate("tx_pattern", "districts", metric="count",
          geometry_filters={"is_anomaly": True})

# Distinct accounts per region
aggregate("tx_pattern", "accounts",
          group_by_property="districts:region", distinct=True)

# Pivot by transaction type
aggregate("tx_pattern", "districts", metric="sum:amount",
          pivot_event_field="type")

# Orphaned events (no edge to a specific line)
aggregate("tx_pattern", "accounts", metric="count",
          missing_edge_to="cpty_banks")

# HAVING equivalent
aggregate("tx_pattern", "accounts", metric="count",
          having={"gt": 2000})

# count_distinct: distinct accounts per operation type
aggregate("tx_pattern", "operations", metric="count_distinct:accounts")
```

---

## Population Analysis

### `contrast_populations`

Compares two entity groups by computing per-dimension Cohen's d effect sizes. Answers "WHY are these groups different?"

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern |
| `group_a` | dict | required | Group spec — see formats below |
| `group_b` | dict | `null` | Group spec; omit to auto-use complement of `group_a` |

**Group spec formats:**

| Format | Example | Selects |
|--------|---------|---------|
| `{"anomaly": true}` | `{"anomaly": True}` | Geometric outliers |
| `{"keys": ["K-1", "K-2"]}` | Named entities | Explicit list |
| `{"alias": "id", "side": "in"}` | Alias inside/outside | Cutting-plane segment |
| `{"edge": {"line_id": "l", "key": "K"}}` | Edge-linked | All events referencing entity K on line l — preferred for entity-based splits |

**Returns:** `dimensions[]` sorted by `|effect_size|` desc — each with `dim_label`, `effect_size`, `diff`. Also `dead_dimensions[]` — dim indices with near-zero variance (exclude from interpretation).

**Reading results:** `diff > 0` = group_a has higher mean. `|effect_size| > 2` = large separation. `effect_size ≈ 0` on a `required=true` relation is expected (all entities share that value).

---

### `get_centroid_map`

Reveals how entity groups are distributed in delta-space.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern |
| `group_by_line` | string | required | Line to group entities by |
| `group_by_property` | string | `null` | `"line_id:column"` — group by property value instead of entity key |
| `include_distances` | bool | `true` | Include `inter_centroid_distances` (set `false` for quick overview, avoids O(k²) output) |
| `top_n_distances` | int | `20` | Limit to N closest pairs (sorted ascending by distance). Set `null` to return all pairs (O(k²) — avoid for high-cardinality lines). |
| `sample_size` | int | `null` | Subsample N entities before computing centroids |

**Returns:**

| Field | Description |
|-------|-------------|
| `global_centroid.vector` | Population center of gravity (near zero for well-calibrated mu) |
| `group_centroids[].vector` | Mean delta of group — positive = more edges than average on that dimension |
| `group_centroids[].radius` | Mean `‖delta‖` within group — high = dispersed group |
| `group_centroids[].distance_to_global` | How far the group sits from the population center |
| `inter_centroid_distances` | Pairwise L2 between group centroids — low = structurally similar groups |
| `structural_outlier` | Group with highest `distance_to_global` |
| `centroid_drift` | Per group: `{predicted_delta_norm, current_delta_norm, drift_direction, reliability}` when representative entity has ≥3 slices |
| `dead_dimensions[]` | Dim indices with near-zero variance — exclude from interpretation |

**Continuous-mode patterns:** Standard edge-based grouping is unavailable when `edge_max` is set. Use `group_by_property` to group by entity property values instead.

---

### `find_clusters`

Discovers intrinsic geometric archetypes via k-means++ (π8). No external grouping needed.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern |
| `n_clusters` | int | `5` | Number of clusters. Set to `0` for auto-detection via silhouette analysis (searches k=2–15, picks highest mean silhouette, subsamples to 5000 entities). Response includes `auto_k: true` when used. |
| `top_n` | int | `10` | Members per cluster (hard bound: `n_clusters × top_n ≤ 100`). `capped_warning` when reduced. |
| `sample_size` | int | `null` | Subsample N entities before clustering. Recommended for N > 100K. |

**Returns:** `clusters[]` each with `centroid`, `members[]`, `size`, `dead_dimensions[]`.

---

### `attract_boundary`

Finds entities nearest to a geometric cutting plane defined on an alias (π6).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `alias_id` | string | required | Alias with `cutting_plane` defined in sphere.json (errors otherwise) |
| `pattern_id` | string | required | Pattern |
| `direction` | string | `"both"` | `"in"` = inside segment, `"out"` = outside, `"both"` = both sides |
| `top_n` | int | `10` | Number of results |
| `fdr_alpha` | float | `null` | Apply Benjamini-Hochberg FDR control at this level (0-1 exclusive). Returns only entities with `q_value <= alpha`. Each retained entity carries a `q_value` field. `null` = no FDR filtering (legacy behavior). |
| `fdr_method` | string | `"bh"` | FDR method. `"bh"` (Benjamini-Hochberg, assumes pi0=1) or `"storey"` (Storey LSL estimator of the true null proportion; shrinks q-values by pi0 and typically recovers 10–15% more discoveries when combined with `p_value_method="chi2"` on spheres that have a genuine null mass). With the default `p_value_method="rank"`, `"storey"` collapses to `"bh"` — rank p-values are uniform by construction and carry no null signal. |
| `p_value_method` | string | `"rank"` | p-value construction. `"rank"` (default, empirical from `delta_rank_pct` — uniform by construction) or `"chi2"` (upper-tail χ²(df) survival on `||delta||²`, the parametric null assuming `delta_i ~ N(0, 1)`). Pair with `fdr_method="storey"` for power recovery on moderate-super-anomaly patterns; on over-compressed or extreme patterns the uplift collapses to zero. |
| `select` | string | `"top_norm"` | `"top_norm"` ranks by boundary distance ascending. `"diverse"` applies submodular facility location to pick the K most geometrically diverse representatives — each result includes a `representativeness` count. |

**Returns per entity:** `primary_key`, `signed_distance` (>0 = inside, <0 = outside, ≈0 = at boundary), `is_in_segment`, `delta_norm`, `is_anomaly`.

**Note:** Cutting planes operate on z-scored delta vectors, not raw shape values.

---

## Hub & Network

### `find_hubs`

Ranks entities by geometric connectivity — shape-vector footprint (π7).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern |
| `top_n` | int | `10` | Max results (hard cap: 25). `capped_warning` when reduced. |
| `line_id_filter` | string | `null` | Restrict hub score to edges of one line. When set, `score_stats` reflects the filtered distribution. |
| `fdr_alpha` | float | `null` | Apply Benjamini-Hochberg FDR control at this level (0-1 exclusive). Returns only entities with `q_value <= alpha`. Each retained entity carries a `q_value` field. `null` = no FDR filtering (legacy behavior). |
| `fdr_method` | string | `"bh"` | FDR method. `"bh"` (Benjamini-Hochberg, assumes pi0=1) or `"storey"` (Storey LSL estimator of the true null proportion; shrinks q-values by pi0 and typically recovers 10–15% more discoveries when combined with `p_value_method="chi2"` on spheres that have a genuine null mass). With the default `p_value_method="rank"`, `"storey"` collapses to `"bh"` — rank p-values are uniform by construction and carry no null signal. |
| `p_value_method` | string | `"rank"` | p-value construction. `"rank"` (default, empirical from `delta_rank_pct` — uniform by construction) or `"chi2"` (upper-tail χ²(df) survival on `||delta||²`, the parametric null assuming `delta_i ~ N(0, 1)`). Pair with `fdr_method="storey"` for power recovery on moderate-super-anomaly patterns; on over-compressed or extreme patterns the uplift collapses to zero. |
| `select` | string | `"top_norm"` | `"top_norm"` ranks by hub score descending. `"diverse"` applies submodular facility location to pick the K most geometrically diverse representatives — each result includes a `representativeness` count. |

**Returns per entity:** `key`, `properties`, `alive_edges`, `hub_score`, `hub_score_pct`.

**Top-level fields:** `mode` (`"continuous"` or `"binary"`), `max_hub_score` (null in binary mode), `score_stats` (`mean, std, p25–p95, max, total_entities`).

`hub_score_pct` = `hub_score / max_hub_score × 100` — relative to global theoretical max regardless of `line_id_filter`. Use `score_stats.max` to benchmark within a filtered scope.

For **inbound** connectivity (how many events reference an entity), use `aggregate` with `metric="count"` instead.

---

### `hub_history`

Shows hub score evolution reconstructed from temporal slices.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `pattern_id` | string | required | Anchor pattern with `edge_max` (binary mode returns empty history with explanation `note`) |

**Returns:** `history[]` (`{timestamp, hub_score, alive_edges_est, changed_line_id, deformation_type, delta_norm}`), `base_state` (`{hub_score, alive_edges_est}`).

`base_state.hub_score` is consistent with `find_hubs.hub_score` for the same entity.

---

### `find_neighborhood`

BFS traversal through polygon edges, returning entities within N hops with anomaly enrichment.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Center entity |
| `pattern_id` | string | required | Pattern (binary FK mode only — `point_key != ""`) |
| `max_hops` | int | `2` | BFS depth limit |
| `max_entities` | int | `100` | Cap on returned neighbors |

**Returns:** `center`, `entities[]` (`{key, hop, is_anomaly, delta_rank_pct}`), `summary` (`{total, anomalous, max_hop_reached, capped}`).

**Limitation:** Only works for binary FK mode patterns. Continuous-mode patterns (edge_max > 0) have `point_key=""` — use `find_counterparties` instead.

---

### `find_counterparties`

Finds counterparty entities via an event line (outgoing targets and incoming sources). When `pattern_id` is given and the pattern has an edge table, uses fast BTREE lookup with amount aggregates.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `line_id` | string | required | Event line containing from/to columns |
| `from_col` | string | required | Column naming the sender |
| `to_col` | string | required | Column naming the receiver |
| `pattern_id` | string | `null` | When set, enriches results with `is_anomaly` + `delta_rank_pct`. Enables edge table fast path |
| `top_n` | int | `20` | Max counterparties to return per direction (outgoing/incoming) |
| `use_edge_table` | bool | `true` | Set `false` to force full points scan instead of edge table |
| `timestamp_cutoff` | float | `null` | Unix seconds. Edge-table fast path only — consider only edges with `timestamp <= cutoff`. As-of reconstruction. **Raises** `GDSNavigationError` when supplied with the points-scan fallback (no `pattern_id` or `use_edge_table=False`). |

**Returns:** `outgoing[]` (entity sends TO), `incoming[]` (entity receives FROM), each with `key`, `tx_count`, and optionally `is_anomaly`, `delta_rank_pct`. Edge table fast path adds `amount_sum`, `amount_max` per entry. Plus `summary` (`{total_outgoing, anomalous_outgoing, ...}`).

---

### `extract_chains`

Extracts chain patterns from an event line.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `event_pattern_id` | string | required | Pattern ID or line ID containing the event data |
| `from_col` | string | required | Column naming the sender entity key |
| `to_col` | string | required | Column naming the receiver entity key |
| `time_col` | string | `null` | Timestamp column — when set, chains are filtered by `time_window_hours` |
| `category_col` | string | `null` | Categorical column tracked per hop (e.g. `"currency"`) |
| `amount_col` | string | `null` | Numeric column tracked per hop (e.g. `"amount"`) |
| `time_window_hours` | int | `168` | Max hours between hops to be considered part of the same chain |
| `max_hops` | int | `15` | Maximum chain length (hops) |
| `min_hops` | int | `2` | Minimum chain length — chains shorter than this are discarded |
| `top_n` | int | `20` | Number of top chains to return |
| `sort_by` | string | `"hop_count"` | Sort criterion: `"hop_count"` or `"amount_decay"` |
| `sample_size` | int | `50000` | Max event rows to use for chain extraction. Pass `null` for no limit (risk of OOM on >5M events). |
| `max_chains` | int | `100000` | Global cap on chains produced by DFS — prevents hang/OOM on dense graphs |
| `seed_nodes` | list[string] | `null` | Restrict BFS starting nodes to this list (targeted extraction from specific entities) |
| `bidirectional` | bool | `false` | When true, each edge A→B also creates reverse edge B→A (for undirected relationship analysis) |

**Returns:** `chains[]` with `{hop_count, is_cyclic, keys, n_distinct_categories, amount_decay}`, `total_chains`, `returned`, `summary` (`{total_chains, cyclic_chains, hop_count_mean, hop_count_max}`).

---

### `find_chains_for_entity`

Finds which chains involve a specific entity — reverse lookup via chain keys.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `pattern_id` | string | required | Chain pattern (entity line must have `chain_keys` column) |

**Returns:** `chains[]` (`{chain_id, is_anomaly, delta_norm, delta_rank_pct}`), `summary` (`{total, anomalous}`).

---

### `find_geometric_path`

Find paths between two entities scored by geometric coherence. Uses bidirectional BFS on polygon edges, then ranks discovered paths by a configurable scoring function.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from_key` | string | required | Source entity key |
| `to_key` | string | required | Target entity key |
| `pattern_id` | string | required | Pattern with edge table |
| `max_depth` | int | `5` | Maximum path length (hops) |
| `beam_width` | int | `50` | Maximum paths returned (top-K by score). |
| `scoring` | string | `"geometric"` | Scoring function: `"geometric"` (delta coherence), `"amount"` (geometric score modulated by log(transaction amount)), `"anomaly"` (anomaly density), `"shortest"` (fewest hops) |

**Returns:** `paths[]` — each with `keys[]`, `hop_count`, `geometric_score`. Higher `geometric_score` = more coherent path.

**Notes:** Requires a pattern with an edge table. Raises an error if the pattern has no edges.

---

### `discover_chains`

Discover entity chains from a starting point via runtime temporal BFS. Unlike `extract_chains` (which scans the full event line), this tool starts from a specific entity and traverses the edge table at query time — no pre-built chain lines required.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Starting entity key |
| `pattern_id` | string | required | Pattern with edge table |
| `time_window_hours` | int | `168` | Max hours between consecutive hops |
| `max_hops` | int | `10` | Maximum chain length |
| `min_hops` | int | `2` | Minimum chain length — shorter chains discarded |
| `max_chains` | int | `20` | Max chains to return |
| `direction` | string | `"forward"` | Traversal direction: `"forward"`, `"backward"`, `"both"` |

**Returns:** `chains[]` scored by geometric coherence, `total_chains`, `returned`.

**Notes:** Does NOT require pre-built chain lines. Operates directly on the pattern's edge table via temporal BFS.

---

### `edge_stats`

Show edge table statistics for a pattern — quick diagnostic to verify edge data availability and distribution.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to inspect |

**Returns:** `row_count`, `unique_from`, `unique_to`, `timestamp_range`, `amount_range`, `avg_degree`. Returns `null` if the pattern has no edge table.

---

### `entity_flow`

Net flow analysis per counterparty via edge table. Computes outgoing/incoming totals and per-counterparty net flow.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity to analyze |
| `pattern_id` | string | required | Event pattern with edge table |
| `top_n` | int | `20` | Max counterparties to return |
| `timestamp_cutoff` | float | `null` | Unix seconds. Only edges with `timestamp <= cutoff` are considered. As-of flow reconstruction. |

**Returns:** `outgoing_total`, `incoming_total`, `net_flow`, `flow_direction`, `counterparties[]` sorted by `|net_flow|` (each with `key`, `net_flow`, `direction`).

---

### `contagion_score`

Score how many of an entity's counterparties are anomalous. Requires event pattern with edge table.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity to score |
| `pattern_id` | string | required | Event pattern with edge table |
| `timestamp_cutoff` | float | `null` | Unix seconds. Only edges with `timestamp <= cutoff` are considered. Enables as-of contagion reconstruction — e.g. pass the incident timestamp to see how much of the neighborhood was contaminated on that day. |

**Returns:** `score` (0.0–1.0), `total_counterparties`, `anomalous_counterparties`, `interpretation`.

---

### `contagion_score_batch`

Contagion score for multiple entities in one call.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_keys` | list[string] | required | Entity keys to score |
| `pattern_id` | string | required | Event pattern with edge table |
| `max_keys` | int | `200` | Max entities to process |
| `timestamp_cutoff` | float | `null` | Unix seconds. Forwarded to each per-entity `contagion_score`. |

**Returns:** Per-entity `results[]` plus `summary` with `mean_score`, `max_score`, `high_contagion_count`.

---

### `degree_velocity`

Temporal connection velocity — how an entity's degree changes over time. Buckets edges by timestamp and counts unique counterparties per bucket.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity to analyze |
| `pattern_id` | string | required | Event pattern with edge table |
| `n_buckets` | int | `4` | Number of time buckets |
| `timestamp_cutoff` | float | `null` | Unix seconds. Only edges with `timestamp <= cutoff` are considered; the last bucket endpoint is naturally `<= cutoff`. |

**Returns:** `buckets[]` (each with `period`, `out_degree`, `in_degree`), `velocity_out`, `velocity_in`, `interpretation`. Returns `warning` with null velocities when all timestamps are 0.

---

### `investigation_coverage`

Agent guidance: how much of an entity's edge neighborhood has been explored. Pass explored_keys to see what's left to investigate.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity to analyze |
| `pattern_id` | string | required | Event pattern with edge table |
| `explored_keys` | list[string] | `null` | Entity PKs already investigated |

**Returns:** `total_edges`, `explored`, `unexplored`, `unexplored_anomalous[]` (with `is_anomaly`, `delta_rank_pct`), `coverage_pct`, `summary`.

---

### `propagate_influence`

BFS influence propagation from seed entities with geometric decay. At each hop: influence = parent_score * decay * geometric_coherence.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `seed_keys` | list[string] | required | Starting entities (known bad actors) |
| `pattern_id` | string | required | Event pattern with edge table |
| `max_depth` | int | `3` | Maximum hops from seeds |
| `decay` | float | `0.7` | Score decay per hop |
| `min_threshold` | float | `0.001` | Stop expanding below this score |
| `timestamp_cutoff` | float | `null` | Unix seconds. BFS only follows edges with `timestamp <= cutoff`. Use to reconstruct what propagation would have surfaced on a prior date. |

**Returns:** `affected_entities[]` sorted by `influence_score` (each with `key`, `depth`, `influence_score`, `tx_count`, `is_anomaly`), `summary`. Influence weighted by `log1p(tx_count)` — multi-transaction relationships propagate stronger. Output capped to top 100.

---

### `cluster_bridges`

Find entities bridging geometric clusters via edge table. Runs π8 clustering then identifies cross-cluster edges.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Event pattern with edge table |
| `n_clusters` | int | `5` | Number of geometric clusters |
| `top_n_bridges` | int | `10` | Max bridge pairs to return |

**Returns:** `clusters[]` (with `cluster_id`, `size`, `anomaly_rate`), `bridges[]` (with `cluster_a`, `cluster_b`, `edge_count`, `bridge_entities[]`), `summary`.

---

### `anomalous_edges`

Find edges between two entities enriched with event-level anomaly scores. Unlike path/chain tools which score entities (anchor geometry), this scores individual transactions (event geometry).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from_key` | string | required | First entity key |
| `to_key` | string | required | Second entity key |
| `pattern_id` | string | required | Event pattern with edge table |
| `top_n` | int | `10` | Max edges to return |

**Returns:** `edges[]` sorted by `delta_norm` desc (each with `event_key`, `from_key`, `to_key`, `amount`, `timestamp`, `delta_norm`, `is_anomaly`, `delta_rank_pct`), `summary` with `total_edges`, `anomalous`, `max_delta_norm`.

---

### `score_edge`

Geometric anomaly score for a single edge. Formula: `||δ_from − δ_to|| × (1 / min(pair_tx_count, 1000))`. Complementary to entity-level `delta_norm`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from_key` | string | required | Source entity primary key |
| `to_key` | string | required | Destination entity primary key |
| `pattern_id` | string | required | Anchor pattern whose geometry provides delta vectors |

**Returns:** `{score, delta_distance, pair_tx_count, effective_weight, interpretation}`. High score = distant endpoints + rare pair (classic AML layering signature).

---

### `find_high_potential_edges`

Rank all edges in the pattern's companion event table by geometric edge potential, highest first.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern |
| `top_n` | int | `10` | Max results (hard cap 100) |
| `from_key` | string | `null` | Scope to edges starting at this entity |
| `to_key` | string | `null` | Scope to edges ending at this entity |
| `min_pair_count` | int | `1` | Filter out pairs appearing fewer times than this |

**Returns:** list of `{from_key, to_key, score, delta_distance, pair_tx_count}`.

---

### `score_motif`

Score the best structural motif seeded at `entity_key`. Composes `edge_potential` across the edges of the motif via product — a motif of rare edges is rare. Closed vocabulary of eight motif types covering the structural atoms of 25 documented AML typologies.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity_key` | string | required | Seed entity primary key (source for fan_out/chain_k/structuring, sink for fan_in, pivot for cycle_2/cycle_3, source/sink for split_recombine depending on `direction`, source-or-sink for bipartite_burst) |
| `motif_type` | string | required | One of `fan_out`, `fan_in`, `cycle_2`, `cycle_3`, `structuring`, `chain_k`, `split_recombine`, `bipartite_burst` |
| `pattern_id` | string | required | Pattern whose geometry provides delta vectors |
| `time_window_hours` | int | `null` | Override default: fan_out=168h, fan_in=168h, cycle_2=24h, cycle_3=72h, structuring=1h, chain_k=168h, split_recombine=168h, bipartite_burst=24h |
| `amt1_min` | float | `10000.0` | **structuring only** — minimum amount on hop 1 (A→B) |
| `amt2_max` | float | `10000.0` | **structuring only** — maximum amount on hops 2 and 3 (B→C, C→D) |
| `k` | int | `4` | **chain_k only** — chain length (3 ≤ k ≤ 8, k-1 edges). Default 4 matches typology T5 / T18 depth. |
| `min_k` | int | `null` | **fan_out / fan_in / split_recombine / bipartite_burst** — override distinct-neighbour (or source-side, for bipartite_burst) cardinality threshold (default 3 when `null`, must be ≥ 2). Lets you single-seed-check whether an entity has e.g. ≥ 10 sources without triggering the cold ranking cache on `find_high_potential_motifs`. |
| `direction` | string | `"forward"` | **split_recombine only** — `"forward"` treats the seed as the source S of a S → {M₁,…,Mₖ} → D diamond; `"backward"` treats it as the sink D. Ignored for other motif types. |
| `min_m` | int | `3` | **bipartite_burst only** — sink-side cardinality of the K_{k,m} subgraph (must be ≥ 2). Ignored for other motif types. |

**Motif types:**
- **`fan_out`** — hub → k distinct targets in the window (min k=3). Typology atoms: T6 Offshore Hub, T13 Concentrator (source side).
- **`fan_in`** — k distinct sources → sink in the window (min k=3). Mirror of `fan_out`. Typology atoms: T12 Parallel Layering (destination side), T13 Concentrator/Sink.
- **`cycle_2`** — A↔B bidirectional pair within the window. Typology atoms: T2 Flash-Burst Round-Trip, T4 Bidirectional Burst.
- **`cycle_3`** — A→B→C→A triad with strict temporal ordering `ts1 < ts2 < ts3`, total span ≤ window. Typology atoms: T3 Round-Tripping 3-Party, T5 Long-Cycle, T11 Multi-Round-Tripping.
- **`chain_k`** — open A→B→…→Z chain of length `k` (3 ≤ k ≤ 8), no cycle closure, no node revisit, strict monotone timestamps, total span ≤ window. Typology atoms: T5 Multi-Stage Layering, T18 Multi-Jurisdiction Latency Chain, T15 Attenuation Pattern (when `k ≥ 4` with wider window). Default `k=4` matches typology depth; raise for deeper layering investigations, lower (`k=3`) for faster scans of shallow chains.
- **`structuring`** — open A→B→C→D linear chain with hop1 amount ≥ `amt1_min`, hops 2 and 3 amount ≤ `amt2_max`, strict temporal ordering within `time_window_hours` (default 1h — flash). Typology atoms: structuring / smurfing (cash-deposit-split-and-wire for reporting-threshold evasion). Defaults 10000 assume the pattern's amount column is in USD; override per jurisdiction (GBP, EUR, crypto unit) via `amt1_min`/`amt2_max`. **Assumes the edge table's `amount` column is non-negative (positive money flow); NULL or ≤ 0 amounts on any hop are silently skipped rather than surfaced as structuring matches.** Producers emitting signed amounts (credit-positive / debit-negative convention) will see zero structuring results — pre-process to magnitude before building the sphere if that's the semantics.
- **`split_recombine`** — diamond S → {M₁,…,Mₖ} → D with stacked-bipartite temporal order: all split-hops S→Mᵢ precede all recombine-hops Mᵢ→D within the window, no node revisits. `direction="forward"` picks the seed as source S (split-then-recombine); `direction="backward"` picks the seed as sink D (gather-then-fan). `min_k` overrides the intermediary-cardinality threshold (default 3, must be ≥ 2). Typology atoms: T1 Structured Layering (forward — scatter-gather diamond), T12 Parallel Layering (backward — multiple chains converging on the seed), T13 Concentrator/Sink (backward — diamond subtype of fan_in).
- **`bipartite_burst`** — complete K_{k,m} bipartite subgraph in a tight time window: `k` distinct sources each transact with every one of `m` distinct sinks, all edges fall within the window. The seed is tried as a source first, then as a sink (fallback). `min_k` sets the source-side cardinality (default 3, must be ≥ 2); `min_m` sets the sink-side cardinality (default 3, must be ≥ 2). Typology atoms: T16 Mirror-Flow Burst (cohort / parallel-collusion variant — k coordinated senders fan to m shared receivers in a tight window).

**Performance:** k=3 and k=4 use a generous per-step frontier cap and are
practical for >500k populations. For k>=5, the cap tightens progressively
to bound worst-case latency on hub seeds. Results may surface
`frontier_truncated: true` more often at higher k — when that flag is true,
the ranking is incomplete; narrow the time window or lower k to recover
full recall.

**Returns:** `{found, score, log_score, score_clamped, motif_type, breakdown}` on success, or `{found: false, reason}` when no motif matches. `log_score` is `sum(log(edge_potential))` over non-zero edges (`-inf` when any edge is zero); `score_clamped` is `true` when the raw edge-potential product overflowed and was clamped at `1e300` — log_score is authoritative for ordering above the clamp. `breakdown` lists per-edge `edge_potential`, `delta_distance`, `pair_tx_count` so the agent can see which edge contributed most. `cycle_2` adds `counterparty`; `cycle_3` adds `ring` (list of 3 keys); `fan_out` / `fan_in` add `k` (distinct neighbours); `chain_k` adds `path` (list of k keys), `k`, and `frontier_truncated: bool` (true when the per-level frontier cap was hit during enumeration — rankings may be incomplete; retry with tighter window or lower k); `structuring` adds `path` (list of 4 keys), `timestamps` (per-hop unix seconds), `amounts` (per-hop amount); `split_recombine` adds `direction`, `source`, `sink`, `intermediaries` (list of k middle keys), `k` (intermediary count); `bipartite_burst` adds `sources` (list of k keys), `sinks` (list of m keys), `k` (source count), `m` (sink count).

**Large-motif response shape.** When a motif carries more than 50 edges, `edges` and `breakdown` are capped at the top 50 contributors by `edge_potential` DESC; `edges_total_count` reports the original count, `edges_truncated` / `breakdown_truncated` flag the truncation, and `breakdown_summary` provides `count`, `mean`, `std`, `min`, `max`, `p25`, `p50`, `p75`, `p95` of `edge_potential` over the full edge set so the agent sees the distribution even when only the top 50 are materialised. For motifs with ≤ 50 edges both truncation flags are `false` and no `breakdown_summary` is emitted. Rationale: pre-fix, a fan_in hub with ~500 sources produced ~200k-char responses that overflowed the MCP token limit.

Example (truncated, k = 500):

```json
{
  "found": true,
  "motif_type": "fan_in",
  "seed": "SINK",
  "score": 1e300,
  "log_score": 1151.29,
  "score_clamped": true,
  "k": 500,
  "edges": [["src_a", "SINK"], "... top 50 ..."],
  "edges_total_count": 500,
  "edges_truncated": true,
  "breakdown": ["... top 50 breakdown entries ..."],
  "breakdown_truncated": true,
  "breakdown_summary": {
    "count": 500, "mean": 2.3, "std": 1.1, "min": 0.2, "max": 9.8,
    "p25": 1.4, "p50": 2.1, "p75": 3.0, "p95": 4.9
  }
}
```

Example (small, k = 3):

```json
{
  "found": true,
  "motif_type": "cycle_3",
  "score": 0.42,
  "log_score": -0.87,
  "score_clamped": false,
  "ring": ["A", "B", "C"],
  "edges": [["A", "B"], ["B", "C"], ["C", "A"]],
  "edges_total_count": 3,
  "edges_truncated": false,
  "breakdown_truncated": false
}
```

---

### `find_high_potential_motifs`

Rank all motifs of a given type across the pattern's companion event table, highest score first.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern with companion event edge table |
| `motif_type` | string | required | One of `fan_out`, `fan_in`, `cycle_2`, `cycle_3`, `structuring`, `chain_k`, `split_recombine`, `bipartite_burst` |
| `top_n` | int | `10` | Max results (hard cap 100) |
| `time_window_hours` | int | `null` | Override motif default |
| `seeds` | list[string] | `null` | Restrict ranking to these entities (post-cache filter) |
| `min_k` | int | `null` | For `fan_out` / `fan_in` / `split_recombine` / `bipartite_burst` only: minimum distinct-neighbour (or source-side, for bipartite_burst) cardinality threshold (default 3 when `null`, must be ≥ 2). Part of the cache key. |
| `amt1_min` | float | `10000.0` | **structuring only** — minimum amount on hop 1 (A→B). Part of the cache key, so changing it triggers recompute. |
| `amt2_max` | float | `10000.0` | **structuring only** — maximum amount on hops 2 and 3 (B→C, C→D). Part of the cache key. |
| `k` | int | `4` | **chain_k only** — chain length (3 ≤ k ≤ 8). Part of the cache key; different `k` values are cached separately. |
| `direction` | string | `"forward"` | **split_recombine only** — `"forward"` ranks seeds as the source S of a S → {M₁,…,Mₖ} → D diamond; `"backward"` ranks them as the sink D. Part of the cache key. Ignored for other motif types. |
| `min_m` | int | `3` | **bipartite_burst only** — sink-side cardinality of the K_{k,m} subgraph (must be ≥ 2). Part of the cache key. Ignored for other motif types. |

**Latency note:** first call per `(pattern, motif_type, window, amt1_min, amt2_max, k, direction, min_m)` is cold — enumerates motifs across all seeds in the pattern. Cold call can take 30–90s on patterns with >500k entities. Subsequent calls hit an LRU cache (cap 8). `cycle_3` is deduplicated by canonical ring; `structuring` and `chain_k` are deduplicated by canonical path tuple; `split_recombine` is deduplicated by `(direction, source, sink, sorted intermediaries)`; `bipartite_burst` is deduplicated by `(frozenset sources, frozenset sinks)`. `chain_k` cost scales with out-degree and `k`; prefer `k=3` for fast scans and `k≥6` only for targeted deep-layering investigations.

**Returns:** list of motif instances with `score`, `log_score`, `score_clamped`, `score_rank_pct`, `is_high_potential` (p95 threshold within motif_type), motif-specific fields (see `score_motif` above for the per-type field list, including `frontier_truncated` on `chain_k`). The same large-motif truncation rules from `score_motif` apply — motifs with > 50 edges carry top-50 `edges` / `breakdown` plus `breakdown_summary` population stats; the envelope `count` (number of motif instances) is unaffected.

---

### `find_witness_cohort`

Rank entities that share an anchor entity's witness signature. **Investigative peer ranking — NOT a forecast of future edges.** Surfaces existing peers sharing the target's anomaly signature, not future connections.

Combines four signals into a composite score in [0, 1]:
- delta similarity: `exp(-distance / theta_norm)`, absolute and pool-independent
- witness overlap: Jaccard on witness dimension labels
- trajectory alignment: cosine on trajectory vectors (optional, [0, 1])
- anomaly bonus: graded by `delta_rank_pct / 100`

Excludes entities already connected via the resolved event pattern's edge table — this is the function's main contribution over plain ANN, removing legitimate counterparties so the cohort is denser in unknown peers worth investigating.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Anchor entity key |
| `pattern_id` | string | required | Anchor pattern (raises if event pattern given) |
| `top_n` | int | `10` | Max cohort members returned |
| `candidate_pool` | int | `100` | ANN over-fetch pool before filtering |
| `min_witness_overlap` | float | `0.0` | Drop candidates whose witness Jaccard is below this |
| `min_score` | float | `0.0` | Drop candidates whose composite score is below this |
| `weight_delta` | float | `0.40` | Weight on delta similarity component |
| `weight_witness` | float | `0.30` | Weight on witness overlap component |
| `weight_trajectory` | float | `0.20` | Weight on trajectory alignment component |
| `weight_anomaly` | float | `0.10` | Weight on anomaly bonus component |
| `use_trajectory` | bool | `null` | `null` = auto-detect trajectory index; explicit `false` skips and renormalizes weights |
| `bidirectional_check` | bool | `true` | When false, only outgoing edges count as existing connections |
| `edge_pattern_id` | string | `null` | Override the auto-resolved event pattern with edge table |

**Returns:** `members[]` sorted by `score` desc (each with `primary_key`, `score`, `delta_similarity`, `witness_overlap`, `trajectory_alignment`, `is_anomaly`, `delta_rank_pct`, `explanation`, `component_scores`), `excluded_existing_edges`, `excluded_low_score`, `candidate_pool_size`, `weights_used`, `summary` (`max_score`, `mean_score`, `anomaly_count`, `trajectory_used`, `target_witness_size`, `target_is_anomaly`).

**When weights are not 1.0:** they should sum to 1.0 for the final score to stay in `[0, 1]`. Defaults satisfy this.

---

### `find_novel_entities`

Find entities whose geometry deviates most from their neighbors' expected position. High novelty = entity doesn't behave like its neighborhood. Requires a pattern with an edge table.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Event pattern with edge table |
| `top_n` | int | `10` | Number of results |
| `sample_size` | int | `5000` | Population sampling for large spheres |

**Returns:** `results[]` sorted by `novelty_score` descending, each with `primary_key`, `novelty_score`, `n_neighbors`.

---

## Temporal Analysis

### `dive_solid`

Dives into an entity's temporal history and sets navigator position to Solid (π3). Required before `emerge` populates `entity_properties`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `pattern_id` | string | required | Anchor pattern |
| `timestamp` | string | `null` | ISO-8601 upper bound — only slices at or before this time are returned |

**Returns:** `slices[]`, `num_slices`, `base_polygon`, `forecast` (same fields as `get_solid` when ≥3 slices), `stale_forecast_warning`, `base_polygon_note` (when temporal slices exist, reminds that `base_polygon.delta_norm` reflects first observation not current state), `reputation` (`{value: Bayesian posterior 0–1, anomaly_tenure: longest consecutive anomalous streak}`).

**get_solid vs dive_solid:**
- `get_solid` reads temporal data without changing navigator position
- `dive_solid` changes position to Solid — required for `emerge` to return `entity_properties`

---

### `find_drifting_entities`

Finds entities with the highest temporal drift — geometric velocity over recorded slices (π9). Anchor patterns only.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern |
| `top_n` | int | `10` | Max results (hard cap: 50). `capped_warning` when reduced. |
| `filters` | dict | `null` | Time window: `{"timestamp_from": "2024-01-01", "timestamp_to": "2025-01-01"}`. Partition pruning is automatic. Without filters: scans all history. |
| `sample_size` | int | `null` | Subsample N entities before scanning. Recommended for large populations. |
| `forecast_horizon` | int | `null` | When set, each result includes `drift_forecast` with predicted displacement and anomaly status at `t + horizon`. Requires ≥3 slices. |
| `rank_by_dimension` | string | `null` | When set, re-rank by the absolute change on this specific dimension name instead of overall displacement. Use dimension display names or line IDs from the pattern relations. |
| `fdr_alpha` | float | `null` | Apply Benjamini-Hochberg FDR control at this level (0-1 exclusive). Returns only entities with `q_value <= alpha`. Each retained entity carries a `q_value` field. `null` = no FDR filtering (legacy behavior). |
| `fdr_method` | string | `"bh"` | FDR method. `"bh"` (Benjamini-Hochberg, assumes pi0=1) or `"storey"` (Storey LSL estimator of the true null proportion; shrinks q-values by pi0 and typically recovers 10–15% more discoveries when combined with `p_value_method="chi2"` on spheres that have a genuine null mass). With the default `p_value_method="rank"`, `"storey"` collapses to `"bh"` — rank p-values are uniform by construction and carry no null signal. |
| `p_value_method` | string | `"rank"` | p-value construction. `"rank"` (default, empirical from `delta_rank_pct` — uniform by construction) or `"chi2"` (upper-tail χ²(df) survival on `||delta||²`, the parametric null assuming `delta_i ~ N(0, 1)`). Pair with `fdr_method="storey"` for power recovery on moderate-super-anomaly patterns; on over-compressed or extreme patterns the uplift collapses to zero. |
| `select` | string | `"top_norm"` | `"top_norm"` ranks by displacement descending. `"diverse"` applies submodular facility location to pick the K most geometrically diverse representatives — each result includes a `representativeness` count. |

**Returns per entity:**

| Field | Description |
|-------|-------------|
| `displacement` | Net shift: `‖delta_last − delta_first‖` (ranking metric) |
| `displacement_current` | `‖base_polygon.delta − delta_first‖` — distinguishes "drifted and stayed" from "drifted and recovered" |
| `path_length` | Total distance traveled: `Σ ‖delta[i+1] − delta[i]‖` |
| `ratio` | `displacement / path_length` — 1.0 = straight drift, ~0 = oscillation |
| `gradient_alignment` | float in `[-1, 1]` — radially-inward component of the drift vector. `+1` = entity moving toward the null centre (normalising), `-1` = moving away (deteriorating), `0` = tangential (constant radius). Computed over structural dimensions only. |
| `drift_direction` | `"normalizing"` (gradient_alignment > +0.3) / `"deteriorating"` (< -0.3) / `"neutral"` (otherwise). |
| `dimension_diffs` | Per-dimension breakdown of `displacement` |
| `dimension_diffs_current` | Per-dimension breakdown of `displacement_current` |
| `num_slices` | Number of temporal slices used (min 2) |
| `first_timestamp` / `last_timestamp` | Earliest and latest recorded deformation |
| `delta_norm_first` / `delta_norm_last` | Anomaly signal at start and end of recorded history |
| `reputation` | `{value: Bayesian posterior, anomaly_tenure}` |
| `intrinsic_displacement` | M3 additive — L2 norm of the entity-driven (σ_v1-normalised) component of drift between the oldest retained and current calibration epoch. `null` when storage backend lacks multi-epoch retention, sphere has `<2` retained epochs, schema_hash mismatch, or `<2` slices for this entity. |
| `extrinsic_displacement` | M3 additive — L2 norm of the population-recalibration-driven (residual) component. Same null rules. |
| `intrinsic_fraction` | M3 additive — sum-of-squares ratio `‖I‖² / (‖I‖² + ‖E‖²)` bounded `[0, 1]`. Same null rules. Use `decompose_drift` for the full per-dimension breakdown. |
| `drift_forecast` | (when `forecast_horizon` set) `{predicted_delta_norm, forecast_is_anomaly, current_is_anomaly, reliability, horizon}` |

---

### `find_drifting_similar`

Finds entities with similar temporal change trajectory as a reference entity (π10). Uses ANN over trajectory summary vectors.

**Trajectory vector** = `concat([mean(all_deltas), std(all_deltas)])` — captures both direction and volatility.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Reference entity with at least one temporal deformation |
| `pattern_id` | string | required | Anchor pattern only (raises ValueError on event patterns) |
| `top_n` | int | `5` | Number of results (silent hard cap: 50) |

**Returns:** `results[]` (`{primary_key, distance, displacement, num_slices, first_timestamp, last_timestamp}`).

**Notes:** Requires trajectory index built by `GDSWriter.build_trajectory_index(pattern_id)`. Raises ValueError with instructions when index is missing. Finds similar trajectory shape — for similar current state, use `find_similar_entities`.

---

### `compare_time_windows`

Compares population centroid between two time windows (π11).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern |
| `window_a_from` | string | required | ISO-8601 start of first window |
| `window_a_to` | string | required | ISO-8601 end of first window |
| `window_b_from` | string | required | ISO-8601 start of second window |
| `window_b_to` | string | required | ISO-8601 end of second window |

**Returns:** `centroid_shift`, `top_shifted_dimensions[]`, `entity_count_a`, `entity_count_b`.

---

### `find_regime_changes`

Detects when population geometry shifted significantly — changepoint detection (π12). Anchor patterns only.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern |
| `timestamp_from` | string | `null` | ISO-8601 scan start |
| `timestamp_to` | string | `null` | ISO-8601 scan end |
| `n_regimes` | int | `3` | Number of regimes to detect |

**Returns:** `changepoints[]` (`{timestamp, magnitude, top_shifted_dimensions[]}`).

---

## Risk Profiling

### `cross_pattern_profile`

Gathers anomaly signals from all patterns an entity participates in — one call instead of multiple lookups.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `line_id` | string | `null` | Entity line — when omitted, auto-resolved from all patterns |

**Returns:**

| Field | Description |
|-------|-------------|
| `source_count` | Number of patterns flagging anomaly. ≥2 = multi-source risk signal (reduces FP rate). |
| `risk_score` | Weighted anomaly density across patterns (continuous 0.0–N). Each pattern contributes `anomalous_count / related_count`. |
| `connected_risk` | Mean `delta_rank_pct` of anomalous counterparties (0–100). `null` when no composite pattern signal. |
| `signals{}` | Per-pattern signals: `{pattern_id: {type, is_anomaly, delta_norm, ...}}`. Types: `direct`, `composite`, `chain`. |

**Signal types:**
- `direct` — entity is directly in the pattern: has `is_anomaly`, `delta_norm`
- `composite` — entity is referenced via pair/composite pattern: has `related`, `anomalous`
- `chain` — entity participates in chain patterns: has `related`, `anomalous`

---

### `composite_risk`

Combines conformal p-values across all patterns via Fisher's method.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity key |
| `line_id` | string | `null` | Entity line — when omitted, auto-resolved from all patterns |

**Returns:** `combined_p` (low = anomalous across multiple independent patterns), `chi2`, `df`, `n_patterns`, `per_pattern{}`.

---

### `composite_risk_batch`

Runs `composite_risk` for a batch of entity keys.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_keys` | list[string] | required | Entity keys |
| `line_id` | string | `null` | Entity line — when omitted, auto-resolved from all patterns |

**Returns:** `results[]` — one entry per key with same fields as `composite_risk`.

---

### `passive_scan`

Screens an entire entity population across all geometric patterns in one call. Faster than per-entity `cross_pattern_profile` for population-wide screening.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `home_line_id` | string | required | Entity line to screen |
| `threshold` | int | `2` | Minimum source count to include in results |
| `scoring` | string | `"count"` | `"count"` = binary source count; `"weighted"` = density-based (direct patterns use `delta_norm / theta_norm`) |
| `sources` | string | `null` | JSON array of source specs; null = auto-discover all patterns |
| `include_borderline` | bool | `false` | When `sources` is null (auto-discover), also register borderline sources for each pattern |
| `borderline_rank_threshold` | int | `80` | Rank percentile threshold for borderline sources (used with `include_borderline`) |
| `top_n` | int | `100` | Max results, sorted by score descending |

**Returns:** `total_flagged`, `sources_summary{}`, `hits[]` — each hit: `{primary_key, score, weighted_score, sources{}}`.

**Source spec formats** (dispatched by `type` field — 4 source types):
```json
[
  {"pattern_id": "account_pattern"},
  {"type": "borderline", "pattern_id": "account_pattern", "rank_threshold": 80},
  {"type": "points", "line_id": "accounts", "rules": {"return_ratio": [">=", 0.4]}},
  {"type": "compound", "geometry_pattern_id": "chain_pattern",
   "line_id": "accounts", "rules": {"return_ratio": [">=", 0.4]}},
  {"type": "graph", "pattern_id": "tx_pattern", "contagion_threshold": 0.3}
]
```

- `geometry` (default) — geometry-based anomaly detection (`is_anomaly=true`)
- `borderline` — near-threshold non-anomalous entities (rank ≥ threshold AND NOT anomaly)
- `points` — entity column rules (no geometry required)
- `compound` — geometry ∩ points intersection
- `graph` — graph contagion: flags entities whose anomalous counterparty ratio exceeds `contagion_threshold`. Requires event pattern with edge table. Auto-discovered by `auto_discover()`

Response includes `anomaly_intensity` per source hit for geometry sources. For chain and composite sources, `related_count` in each hit reflects the entity-specific related count (not the total pattern population).

---

## Calibration

### `recalibrate`

Full recalibration: reads all shape vectors, recomputes population statistics (mu/sigma/theta), rebuilds delta vectors, overwrites geometry via Lance MVCC. Active sessions remain isolated.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Pattern to recalibrate |
| `soft_threshold` | float | `null` | Update soft drift threshold (0.0–1.0, default 5%) |
| `hard_threshold` | float | `null` | Update hard drift threshold (0.0–1.0, default 20%) |

**Returns:** `pattern_id`, `previous_drift_pct`, `new_theta_norm`, `old_theta_norm`, `records_recalibrated`.

**When to use:** When `sphere_overview` reports `calibration_stale: true` or `calibration_blocked: true`. Check `check_alerts` first for `calibration_drift` alert type.

---

## Detection Recipes

Single-call tools that encapsulate multi-step gds-scanner recipes. Each tool replaces
a 4-6 tool workflow with one call and returns ready-to-use findings.

### `detect_cross_pattern_discrepancy`

Detect entities anomalous in one pattern but normal in another (cross-pattern split signal).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity_line` | string | required | Anchor line to screen (e.g. "customers") |
| `top_n` | integer | 50 | Max results, sorted by delta_norm descending |

**Returns:** `entity_line`, `total_found`, `results[]` where each result has: `entity_key`, `anomalous_pattern`, `normal_patterns`, `delta_norm_anomalous`, `delta_rank_pct_anomalous`, `interpretation`.

Returns `{"diagnostic": "..."}` (instead of silent empty) if entity_line is covered by fewer than 2 patterns.

---

### `detect_neighbor_contamination`

Detect normal entities surrounded by anomalous geometric neighbors (contamination risk).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor or event pattern to scan |
| `k` | integer | 10 | Nearest neighbors per entity |
| `sample_size` | integer | 20 | Anomalous entities to start from; normal candidates discovered from neighborhoods |
| `contamination_threshold` | float | 0.5 | Min fraction of anomalous neighbors to flag |

**Returns:** `pattern_id`, `k`, `sample_size`, `contamination_threshold`, `total_found`, `results[]` where each result has: `target_key`, `is_anomaly_target` (always False), `contamination_rate`, `anomalous_neighbor_count`, `total_neighbors`, `neighbor_keys`.

Returns `[]` if no anomalous entities in pattern.

---

### `detect_trajectory_anomaly`

Detect entities with non-linear temporal trajectories (arch, V-shape, spike-recovery).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern with temporal data |
| `top_n_per_range` | integer | 5 | Max results returned |
| `sample_size` | int | `10000` | Max distinct entities streamed before stopping. Pass `0` to scan the full population (may be slow on large patterns). |

> `displacement_ranks` parameter is deprecated and ignored.

**Returns:** `pattern_id`, `top_n_per_range`, `sample_size`, `total_found`, `results[]` where each result has: `entity_key`, `trajectory_shape`, `displacement`, `path_length`, `num_slices`, `first_timestamp`, `last_timestamp`, `cohort_size`, `cohort_keys`, `interpretation`.

Returns `{"error": "..."}` if pattern is event type or has no temporal data.

---

### `detect_segment_shift`

Detect population segments with disproportionate anomaly rates (segment shift).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern_id` | string | required | Anchor pattern with categorical properties |
| `max_cardinality` | integer | 50 | Skip columns with more distinct values |
| `min_shift_ratio` | float | 2.0 | Min segment/population anomaly rate ratio |
| `top_n` | integer | 20 | Max segments returned |

**Returns:** `pattern_id`, `max_cardinality`, `min_shift_ratio`, `total_found`, `results[]` where each result has: `segment_property`, `segment_value`, `anomaly_rate`, `population_rate`, `shift_ratio`, `entity_count`, `anomalous_count`, `interpretation`.

Returns `{"diagnostic": "..."}` (instead of silent empty) if no string columns exist in the pattern or no segments exceed the shift threshold.

---

## Smart Detection

Meta-tool that automatically plans and executes detection workflows.

### `detect_pattern`

Detect patterns in sphere data using natural language queries. Automatically selects
detection methods based on sphere capabilities and query intent.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | required | Natural language description of what to find |

**How it works:**
1. LLM plans execution steps from sphere capabilities (via MCP sampling)
2. Server executes steps internally (no agent round-trips)
3. LLM optionally filters/interprets results

Falls back to keyword-based planning if MCP sampling is unavailable.

**Available step handlers (42)** — selected automatically based on capabilities.
See [mcp-spec.md](mcp-spec.md) for the full handler table. Categories:

| Category | Count | Examples |
|----------|-------|---------|
| Detection | 6 | find_anomalies, detect_trajectory_anomaly, detect_segment_shift, detect_neighbor_contamination, detect_cross_pattern_discrepancy, find_regime_changes |
| Analysis | 10 | find_hubs, find_clusters, find_drifting_entities, find_similar_entities, contrast_populations, explain_anomaly, trace_root_cause |
| Composite Risk | 2 | composite_risk, composite_risk_batch |
| Aggregation | 1 | aggregate |
| Observability | 5 | sphere_overview, check_alerts, detect_data_quality, anomaly_summary, aggregate_anomalies |
| Temporal | 3 | compare_time_windows, find_drifting_similar, hub_history |
| Network/Graph | 7 | find_counterparties, extract_chains, find_chains_for_entity, find_common_relations, find_geometric_path, discover_chains, edge_stats |
| Population | 2 | get_centroid_map, attract_boundary |
| Smart-mode exclusive | 6 | assess_false_positive, detect_event_rate_anomaly, detect_hub_anomaly_concentration, detect_composite_subgroup_inflation, detect_collective_drift, detect_temporal_burst |

**Returns:** `query`, `capabilities`, `plan` (steps + rationale), `results` (per step),
`interpretation` (optional LLM summary), `elapsed_ms`.

**Operation modes — Smart vs Manual:**

| Mode | Entry point | Tokens/turn | When to use |
|------|-------------|-------------|-------------|
| **Smart** | `detect_pattern` | ~400 tk | 90% — describe intent, server handles orchestration |
| **Manual** | granular tools | ~6-8k tk | Debugging, exploration, custom sequences |

Modes are not exclusive — mix `detect_pattern` for overview, then granular tools to drill in.

---

## Response Conventions

### Delta values

Delta vectors are z-scored: each dimension is normalized by population standard deviation (`sigma_diag`).

- A component of `2.0` means 2 standard deviations from the class mean on that dimension
- `delta_norm` is the L2 norm of the z-scored vector — scale-invariant across dimensions
- `is_anomaly = (delta_norm > theta_norm)` where theta is also stored in z-score space

### Continuous anomaly signal

Every polygon exposes `delta_rank_pct` (0–100): percentile in the population's `delta_norm` distribution.

- `95+` = top 5% most anomalous; `50` = median entity
- `is_anomaly=true` typically corresponds to `delta_rank_pct > ~90–99` depending on calibration

**Ties in binary patterns:** Many entities may share identical `delta_norm`, resulting in identical `delta_rank_pct`. Use `delta_norm` directly and cross-reference `is_anomaly` within tied groups.

### Hard caps

| Tool | Cap | Warning field |
|------|-----|---------------|
| `find_anomalies` | `top_n` ≤ adaptive cap (edge-count-based, typically 15–51) | `capped_warning` |
| `get_event_polygons` | `limit` ≤ adaptive cap (edge-count-based, typically 15–51) | `capped_warning` |
| `find_hubs` | `top_n` ≤ 25 | `capped_warning` |
| `find_drifting_entities` | `top_n` ≤ 50 | `capped_warning` |
| `find_drifting_similar` | `top_n` ≤ 50 | silent cap |
| `find_similar_entities` | `top_n` ≤ 50 | silent cap |
| `find_clusters` | `n_clusters × top_n` ≤ 100 total members | `capped_warning` |
| `aggregate + include_properties` | warn at `total_groups × n_cols > 2000` | `include_properties_warning` |

### Precision geometry columns

Available when sphere is built with the precision stack (`GDSBuilder.add_pattern()`):

| Column | Type | Description |
|--------|------|-------------|
| `conformal_p` | float32 | Calibrated conformal p-value (0–1). Lower = more anomalous. |
| `n_anomalous_dims` | int32 | Count of dimensions where entity exceeds p99 threshold. |
| `max_rolling_z` | float32 | Maximum rolling z-score across all temporal slices (anchor patterns with history only). |

Missing on legacy spheres; `geometry_filters` on missing columns raises an error.

### Event pattern storage

Event pattern geometry is storage-optimized: no `edges` struct column, no `pattern_id`/`pattern_type`/`pattern_ver` columns on disk. `entity_keys[i]` = FK value for `pattern.relations[i]`. Edges are reconstructed transparently — response format is identical to anchor patterns.

---

## Filter Syntax Reference

### `filters` (aggregate)

Edge filters with AND semantics. Each dict has `line` and `key`:

```python
filters=[{"line": "districts", "key": "Prague"}]                  # by entity key
filters=[{"line": "districts", "key": "region:south Bohemia"}]    # by column value
filters=[{"line": "transactions", "key": "type:PRIJEM"}]          # event line field (auto-cast)
filters=[                                                           # multiple = AND
    {"line": "accounts", "key": "has_loan:true"},
    {"line": "districts", "key": "region:Prague"},
]
```

`get_event_polygons.filters` is a dict of record-level key-value pairs on the event line (e.g. `{"type": "PRIJEM"}`). Do not use for `is_anomaly` — use `geometry_filters` instead.

### `geometry_filters`

Filters by geometry columns before aggregation or result return.

```python
{"is_anomaly": True}
{"delta_rank_pct": {"gt": 95}}
{"delta_dim": {"accounts": {"gt": 0.5}}}
{"alias_inside": "alias_id"}        # requires cutting_plane on alias
```

Supported keys and operators:
- `is_anomaly` (bool): equality only
- `delta_rank_pct` (comparison): `gt`, `gte`, `lt`, `lte`, `eq`
- `delta_dim` (per-dimension): dimension name + comparison ops; AND across multiple dimensions
- `alias_inside` (string alias ID): full geometry scan required (~500ms on 1M-event patterns); combine with `is_anomaly` or `delta_rank_pct` to narrow scan first

Unknown keys raise an error. Combines with edge filters (AND): geometry pre-filter runs first.

### `property_filters`

Filters `group_by_line` entities (aggregate) or anomalous entities (find_anomalies) by their own properties.

```python
{"credit_limit": {"gt": 100000}}         # comparison
{"credit_limit": {"gte": 0, "lte": 100000}}  # range
{"customer_group": "SME"}                # equality shorthand
```

Ops: `<`, `<=`, `>`, `>=`, `!=`, equality shorthand. AND across keys.

### `event_filters`

Filters event-line rows by column predicates before aggregation.

```python
{"posting_date": {"gte": "2023-01-01", "lt": "2023-04-01"}}  # date range
{"journal_type": "SA"}                   # equality shorthand
{"shipmode": None}                       # IS NULL
{"shipmode": {"not_null": True}}         # IS NOT NULL
```

Operators: `gt`, `gte`, `lt`, `lte`, `eq`. Multiple keys = AND. Bypasses DataFusion acceleration.

**Performance note:** For null/not-null checks on event patterns, `event_filters={"col": None}` is ~100× faster than `missing_edge_to` for columns stored on the event line (works with sampling; `missing_edge_to` scans all edges).

### `entity_filters`

Filters the pattern's own root entity (anchor: the primary entity; event: the event entry) by properties. Same dict syntax as `property_filters`. Returns `entity_filtered_count` in response.

### `having`

Post-aggregation group filter (SQL HAVING equivalent). Applied after aggregation, before limit/offset.

```python
having={"gt": 1000}           # groups with metric > 1000
having={"gte": 500, "lte": 2000}  # range
```

Not supported with `pivot_event_field`. Response includes `total_groups` (pre-having) and `having_matched` (post-having count).

### `distinct` and `collapse`

**`distinct=True`** — counts unique `group_by_line` entities per `group_by_property` tier. Inverts the role of `group_by_line`: it becomes "what to count" rather than the grouping axis. Output rows: `{prop_value, count, value}`. Incompatible with `include_properties`.

**`collapse_by_property=True`** — collapses `group_by_property` results to per-tier rows for metric aggregation (avg/sum per tier). Requires `group_by_property`. Incompatible with `distinct=True`.

| Question | Correct call |
|----------|-------------|
| Transactions per district | `group_by_line="districts"` |
| Unique accounts per region | `group_by_line="accounts", group_by_property="districts:region", distinct=True` |
| Total amount per region | `group_by_line="districts", group_by_property="districts:region", collapse_by_property=True` |

---

## Continuous Edges

Patterns with `edge_max > 0` (check via `get_sphere_info` → `relations[].edge_max`) store edge **counts** rather than foreign keys. `point_key` is always `""` for these edges.

Affected tools:
- `jump_polygon` → raises `ValueError`
- `get_centroid_map(group_by_line=X)` → raises `ValueError` if all edges to X are continuous
- `contrast_populations({"edge": ...})` → raises `ValueError`

Use `group_by_property` or `aggregate` for grouping on continuous-edge patterns.

Delta values for continuous patterns are read from the geometry file (stored at write time with real edge counts). Hub scores and similarity tools always use the stored delta.

### Event dimensions

Event patterns can carry continuous dimensions via `event_dimensions` in `sphere.json`. Each dimension reads a numeric value from an entity column (e.g. `amount`) normalized by `edge_max` (auto-computed from p99 at build). Produces real-valued delta dimensions instead of binary 0/1. Check `get_sphere_info` → `patterns[].event_dimensions`.

**Impact:** `find_anomalies` can surface individual anomalous events by value; `explain_anomaly` shows which event dimension drove the anomaly score; `find_clusters` discovers value-based archetypes.

---

## Sampling

`sample_size` and `sample_pct` are available on: `aggregate`, `get_centroid_map`, `detect_data_quality_issues`, `line_geometry_stats`. Mutually exclusive. Sampling is applied **after** filters.

```python
aggregate("tx_pattern", "districts", sample_size=500)
aggregate("tx_pattern", "districts", sample_pct=0.01)
aggregate("tx_pattern", "districts", sample_size=500, seed=42)  # reproducible
```

Output includes `sampled: true`, `sample_size`, `total_eligible` when sampling is active.
