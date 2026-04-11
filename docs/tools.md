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
| `primary_key` | string | required | Reference entity key |
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
| `event_pattern_id` | string | required | Event pattern |
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

**Returns:** `polygons[]`, `total_found` (total above threshold), `capped_warning` when top_n was reduced.

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

**Returns:** `severity` (`"normal"` / `"low"` 1.0–1.1× / `"medium"` 1.1–1.5× / `"high"` 1.5–2.5× / `"extreme"` >2.5× theta), `ratio`, `witness`, `repair`, `top_dimensions[]`, `conformal_p`, `reputation` (`{value, anomaly_tenure}`), `composite_risk`.

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

Finds the top-N entities nearest to a given entity by Euclidean delta distance (vectorized brute-force).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Reference entity |
| `pattern_id` | string | required | Pattern |
| `top_n` | int | `5` | Number of results (silent hard cap: 50) |
| `filter_expr` | string | `null` | Lance SQL predicate to pre-filter candidates |
| `missing_edge_to` | string | `null` | Keep only similar entities with NO edge to this line. Over-fetches 5× from ANN to compensate for post-filter attrition. Response includes `missing_edge_to_note` clarifying that the filter applies to geometric edges, not property values. |

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

**DataFusion acceleration:** For `sum/avg/min/max` metrics on patterns >500K rows, `aggregate` automatically uses Apache DataFusion (requires `pip install hypertopos[datafusion]`; falls back to Arrow path if unavailable). `event_filters` bypasses DataFusion.

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

Find paths between two entities scored by geometric coherence. Uses beam search over polygon edges, ranking candidate paths by a configurable scoring function.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from_key` | string | required | Source entity key |
| `to_key` | string | required | Target entity key |
| `pattern_id` | string | required | Pattern with edge table |
| `max_depth` | int | `5` | Maximum path length (hops) |
| `beam_width` | int | `10` | Beam search width — higher = more paths explored |
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

**Returns:** `outgoing_total`, `incoming_total`, `net_flow`, `flow_direction`, `counterparties[]` sorted by `|net_flow|` (each with `key`, `net_flow`, `direction`).

---

### `contagion_score`

Score how many of an entity's counterparties are anomalous. Requires event pattern with edge table.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity to score |
| `pattern_id` | string | required | Event pattern with edge table |

**Returns:** `score` (0.0–1.0), `total_counterparties`, `anomalous_counterparties`, `interpretation`.

---

### `contagion_score_batch`

Contagion score for multiple entities in one call.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_keys` | list[string] | required | Entity keys to score |
| `pattern_id` | string | required | Event pattern with edge table |
| `max_keys` | int | `200` | Max entities to process |

**Returns:** Per-entity `results[]` plus `summary` with `mean_score`, `max_score`, `high_contagion_count`.

---

### `degree_velocity`

Temporal connection velocity — how an entity's degree changes over time. Buckets edges by timestamp and counts unique counterparties per bucket.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `primary_key` | string | required | Entity to analyze |
| `pattern_id` | string | required | Event pattern with edge table |
| `n_buckets` | int | `4` | Number of time buckets |

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

**Returns per entity:**

| Field | Description |
|-------|-------------|
| `displacement` | Net shift: `‖delta_last − delta_first‖` (ranking metric) |
| `displacement_current` | `‖base_polygon.delta − delta_first‖` — distinguishes "drifted and stayed" from "drifted and recovered" |
| `path_length` | Total distance traveled: `Σ ‖delta[i+1] − delta[i]‖` |
| `ratio` | `displacement / path_length` — 1.0 = straight drift, ~0 = oscillation |
| `dimension_diffs` | Per-dimension breakdown of `displacement` |
| `dimension_diffs_current` | Per-dimension breakdown of `displacement_current` |
| `num_slices` | Number of temporal slices used (min 2) |
| `first_timestamp` / `last_timestamp` | Earliest and latest recorded deformation |
| `delta_norm_first` / `delta_norm_last` | Anomaly signal at start and end of recorded history |
| `reputation` | `{value: Bayesian posterior, anomaly_tenure}` |
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

Response includes `anomaly_intensity` per source hit for geometry sources.

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

Returns `[]` if entity_line covered by fewer than 2 patterns.

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

> `displacement_ranks` parameter is deprecated and ignored.

**Returns:** `pattern_id`, `top_n_per_range`, `total_found`, `results[]` where each result has: `entity_key`, `trajectory_shape`, `displacement`, `path_length`, `num_slices`, `first_timestamp`, `last_timestamp`, `cohort_size`, `cohort_keys`, `interpretation`.

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

**Returns:** `pattern_id`, `max_cardinality`, `min_shift_ratio`, `total_found`, `results[]` where each result has: `segment_property`, `segment_value`, `anomaly_rate`, `population_rate`, `shift_ratio`, `entity_count`, `anomalous_count`, `changepoint_date`, `interpretation`.

Returns `[]` if no categorical properties or no shifts above threshold.

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
| Analysis | 9 | find_hubs, find_clusters, find_drifting_entities, find_similar_entities, contrast_populations, explain_anomaly |
| Composite Risk | 2 | composite_risk, composite_risk_batch |
| Aggregation | 1 | aggregate |
| Observability | 5 | sphere_overview, check_alerts, detect_data_quality, anomaly_summary, aggregate_anomalies |
| Temporal | 3 | compare_time_windows, find_drifting_similar, hub_history |
| Network/Graph | 7 | find_counterparties, extract_chains, find_chains_for_entity, find_common_relations, find_geometric_path, discover_chains, edge_stats |
| Population | 2 | get_centroid_map, attract_boundary |
| Smart-mode exclusive | 7 | assess_false_positive, detect_event_rate_anomaly, explain_anomaly_chain, detect_hub_anomaly_concentration, detect_composite_subgroup_inflation, detect_collective_drift, detect_temporal_burst |

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
