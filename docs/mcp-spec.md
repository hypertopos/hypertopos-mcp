# MCP Server Specification

Full tool parameter reference: [tools.md](tools.md)

## Overview

**Transport:** stdio (FastMCP)
**Config:** `.mcp.json` (project-scoped)
**Start:** `python -m hypertopos_mcp.main`
**Dependencies:** `hypertopos` + `mcp[cli]>=1.20`
**Optional:** `anthropic>=0.40` (sampling fallback via Anthropic API)

## Dynamic Tool Loading

The MCP server uses context-aware tool registration to minimize token consumption.
Tools are exposed in three phases based on sphere state and capabilities.

### Phase 1 — Before `open_sphere`

Only 3 tools are visible (tier: `always`):
- `open_sphere` — load a GDS sphere
- `close_sphere` — close current sphere
- `get_session_stats` — session statistics

All other tools are hidden until a sphere is opened.

### Phase 2 — After `open_sphere` (gateway + edge)

Gateway tools (tier: `gateway`):
- `detect_pattern` — smart meta-tool: LLM plans + executes detection server-side
- `sphere_overview` — population overview, anomaly rates, health checks

Edge table tools (tier: `edge`, 12 tools):
- `find_geometric_path`, `discover_chains`, `edge_stats`, `entity_flow`, `contagion_score`, `contagion_score_batch`, `degree_velocity`, `investigation_coverage`, `propagate_influence`, `cluster_bridges`, `anomalous_edges`, `find_witness_cohort`

The agent faces a simple binary choice: use `detect_pattern` for automatic detection,
or call `sphere_overview` to enter manual exploration mode.

### Phase 3 — After `sphere_overview` (manual mode)

Calling `sphere_overview` triggers `_register_manual_tools()` as a side effect,
unlocking the full manual toolset based on the sphere's capabilities:

| Capability | Condition | Tools added |
|------------|-----------|-------------|
| **base** | Always after sphere_overview | 37 tools: navigation, geometry, basic analysis |
| **temporal** | `temporal/` directory exists for any anchor pattern | dive_solid, get_solid, hub_history, find_drifting_entities, compare_time_windows, find_regime_changes |
| **multi_pattern** | 2+ patterns cover same entity (any key type: direct, sibling, event_edge, composite, chain) | cross_pattern_profile, passive_scan, composite_risk, composite_risk_batch, detect_cross_pattern_discrepancy |
| **trajectory_index** | Trajectory ANN index exists in `_gds_meta/trajectory/` | find_drifting_similar, detect_trajectory_anomaly |

### Lifecycle

```
Server start → Phase 1 (3 tools: always)
    ↓
open_sphere(path) → Phase 2 (17 tools: always + gateway + edge)
    ↓
  ├─ detect_pattern(query) → smart mode (no extra tools)
  └─ sphere_overview()     → Phase 3 (54-67 tools: full manual mode)
    ↓
close_sphere() → Phase 1 (3 tools)
    ↓
open_sphere(other_path) → Phase 2 (17 tools, different sphere)
```

### open_sphere response

After opening a sphere, `open_sphere` returns `available_tools` (gateway only) and `capabilities`:

```json
{
  "status": "open",
  "hint": "Two paths: (1) detect_pattern(query) — describe what to find, server handles everything. (2) sphere_overview() — read the sphere first, then full manual toolset unlocks.",
  "capabilities": {
    "has_temporal": true,
    "multi_pattern": true,
    "has_trajectory_index": false
  },
  "available_tools": ["detect_pattern", "sphere_overview"]
}
```

## Operation Modes

After `open_sphere`, two operation modes are available via the gateway (Phase 2):

| Mode | Entry point | Tools visible | Tokens/turn | When to use |
|------|-------------|--------------|-------------|-------------|
| **Smart** | `detect_pattern` | 1 meta-tool (no Phase 3 unlock) | ~400 tk | Agent describes intent in natural language; server plans steps via MCP sampling, executes internally, filters + interprets results |
| **Manual** | `sphere_overview` | 54-67 (unlocked in Phase 3) | ~6-8k tk | Debugging, exploration, custom investigation sequences, follow-up on smart mode findings |

Modes are **not exclusive** — an agent can use `detect_pattern` for overview, then call
`sphere_overview` to unlock granular tools for drill-down.

## Smart Detection (`detect_pattern`)

Single meta-tool that automatically plans and executes detection workflows.

### Execution phases

1. **Planning:** LLM plans execution steps from sphere capabilities (via MCP sampling, 300 tokens, temp 0.1). Falls back to keyword matching → investigation templates when sampling unavailable.
2. **Execution:** Server executes steps internally with dependency resolution (Python calls, zero MCP round-trips). Progress reported via `ctx.report_progress`.
3. **Elicitation:** If >50 candidates found, optionally asks user for stricter threshold (via MCP elicitation, with fallback).
4. **Filtering:** LLM filters false positives from candidates, assigns confidence scores (via sampling, 500 tokens, temp 0.0).
5. **Interpretation:** LLM provides 3-5 sentence summary of findings (via sampling, 400 tokens, temp 0.0).

### Step handlers (39)

| Category | Steps | Capability |
|----------|-------|-----------|
| **Detection** (6) | find_anomalies, detect_trajectory_anomaly, detect_segment_shift, detect_neighbor_contamination, detect_cross_pattern_discrepancy, find_regime_changes | base / trajectory_index / multi_pattern / temporal |
| **Analysis** (9) | find_hubs, find_clusters, find_drifting_entities, find_similar_entities, contrast_populations, check_anomaly_batch, explain_anomaly, cross_pattern_profile, passive_scan | base / temporal / multi_pattern |
| **Composite Risk** (2) | composite_risk, composite_risk_batch | multi_pattern |
| **Aggregation** (1) | aggregate | base |
| **Observability** (5) | sphere_overview, check_alerts, detect_data_quality, anomaly_summary, aggregate_anomalies | base |
| **Temporal** (3) | compare_time_windows, find_drifting_similar, hub_history | temporal / trajectory_index |
| **Network/Fraud** (4) | find_counterparties, extract_chains, find_chains_for_entity, find_common_relations | base |
| **Population** (2) | get_centroid_map, attract_boundary | base |
| **Smart-mode exclusive** (7) | assess_false_positive, detect_event_rate_anomaly, explain_anomaly_chain, detect_hub_anomaly_concentration, detect_composite_subgroup_inflation, detect_collective_drift, detect_temporal_burst | base / multi_pattern / temporal |

The 7 smart-mode exclusive algorithms are NOT available as MCP tools — only reachable
via `detect_pattern`. They provide capabilities impossible in manual mode:

### Multi-step investigation chaining

Steps can depend on prior step results via `depends_on` / `input_key` / `param_target`:

```json
{
  "steps": [
    {"name": "find_anomalies", "params": {"pattern_id": "acct_behavior", "top_n": 5}},
    {"name": "explain_anomaly", "params": {},
     "depends_on": "find_anomalies", "input_key": "top_entities[0].key",
     "param_target": "primary_key"}
  ]
}
```

### Investigation templates (fallback)

When sampling is unavailable, keyword matching selects from pre-defined templates:

| Template | Steps | Triggered by |
|----------|-------|-------------|
| detect_and_explain | find_anomalies → explain_anomaly | "explain why", "root cause" |
| segment_investigation | detect_segment_shift → anomaly_summary | "segment shift", "segment anomal" |
| temporal_investigation | find_drifting_entities → find_regime_changes | "temporal drift", "drift and regime" |
| contamination_analysis | detect_neighbor_contamination → find_hubs | "contamination analysis", "surround" |
| fraud_network | passive_scan | "fraud ring", "fraud network", "aml" |
| population_profile | anomaly_summary → find_clusters | "population profile", "archetype" |
| full_scan | sphere_overview → find_anomalies | (default when no template matches) |

### Investigation hints

`sphere_overview(detail="full")` computes O(1) metadata-based hints per pattern
(temporal availability, cross-pattern opportunities, categorical properties for segment shift).
Hints are cached in `_state["investigation_hints"]` and injected into the planning prompt
to improve step selection even without sampling.

## Sampling Architecture

`_sample_llm` tries two paths in order:

1. **MCP sampling** — `ctx.session.create_message()` with `SamplingMessage`. Requires `clientCapabilities.sampling`. Uses `include_context="thisServer"`, respects `temperature` param.
2. **Anthropic API fallback** — `AsyncAnthropic().messages.create()` with `claude-haiku-4-5-20251001` (hardcoded — update when model retires). Uses `ANTHROPIC_API_KEY` env var. ~$0.003/call.
3. **Keyword fallback** — both LLM paths fail → `detect_pattern` proceeds with keyword matching only. No query planning, no result filtering, no interpretation. The agent gets raw step outputs without LLM-generated summaries.

Negative caching: if `anthropic` package is not installed, the import failure is cached
(sentinel `_anthropic_client = False`) so subsequent calls skip the import attempt.

### Internal modules

- **`enrichment.py`** — shared response enrichment helper. Adds `temporal_hint`, `calibration_stale`, `continuous_mode_note`, `degenerate_warning` to tool responses. Used by navigation and geometry tools.

## MCP Resources

Cacheable sphere metadata — zero token cost on repeated reads.

| URI | Content | Available |
|-----|---------|-----------|
| `sphere://info` | Sphere schema: lines, patterns, aliases | After `open_sphere` |
| `sphere://capabilities` | Detected capabilities dict | After `open_sphere` |

## MCP Prompts

User-triggered investigation workflows (slash commands):

| Prompt | Parameters | Workflow |
|--------|-----------|----------|
| `/investigate` | `entity_key`, `line_id` | goto → get_polygon → explain_anomaly → find_similar → dive_solid |
| `/scan` | `pattern_id` (optional) | sphere_overview → find_anomalies → detect_segment_shift → find_hubs → find_regime_changes |
| `/compare` | `key_a`, `key_b`, `pattern_id` | get_polygon × 2 → compare_entities → find_common_relations → contrast_populations |

## MCP Elicitation

Mid-execution user input for domain-specific thresholds. Triggered when `detect_pattern`
finds >50 candidates. Asks user for stricter threshold. Falls back silently when client
doesn't support elicitation (`hasattr(ctx, "elicit")` + `try/except`).

## MCP Primitives Summary

| Primitive | Usage | Fallback |
|-----------|-------|----------|
| **Sampling** | LLM plans + filters + interprets via `session.create_message` | Anthropic API → keyword fallback |
| **Resources** | `sphere://info`, `sphere://capabilities` — cached metadata | `get_sphere_info()` tool call |
| **Prompts** | `/investigate`, `/scan`, `/compare` — user workflows | Manual tool calls |
| **Elicitation** | Threshold refinement on large result sets | Proceed with all results |
| **Progress** | `ctx.report_progress(i, total)` during step execution | Silently skip |
| **Logging** | `ctx.info(step_name)` for structured diagnostics | No-op |
| **Instructions** | `FastMCP(instructions=...)` — mentions `detect_pattern` as entry point | N/A |
| **Tool Annotations** | `readOnlyHint=True` on 63 read-only tools | Host assumes worst case |

## Token Cost per Phase

| Phase | Tools visible | Estimated token cost |
|-------|:------------:|---------------------|
| Phase 1 — before open_sphere | 3 | ~200 tk |
| Phase 2 — after open_sphere | 17 | ~850 tk |
| Phase 3 — full sphere (all capabilities) | ~67 | ~7k tk (filtered + trimmed docstrings) |
| Phase 3 — simple sphere (base only) | ~54 | ~5k tk |

Without 3-phase loading, all 67 tool schemas would be in context from the start (~22k tk).

## Adding New Tools

When adding a new MCP tool:
1. Register with `@mcp.tool(annotations={"readOnlyHint": True})` (or without for mutation tools)
2. Add entry to `_TOOL_TIERS` in `server.py` (`always` / `gateway` / `base` / `temporal` / `multi_pattern` / `trajectory_index`)
3. Add step handler in `smart.py` (`_step_<name>`) + entry in `_STEP_HANDLERS` + `_STEP_CAPABILITIES`
4. Add keyword tuple in `_kw` dict (or add to `CHAINING_ONLY` if step is only for multi-step chains)
5. If new capability needed, add detection logic to `_detect_capabilities()`
6. Run `test_smart_coverage.py` + `test_dynamic_tools.py` — catches missing entries

## Behavioral Notes

### Anomaly counting consistency

All tools that count anomalies (`sphere_overview`, `anomaly_summary`, `find_anomalies`,
`aggregate_anomalies`, `detect_data_quality_issues`) use the same method: `delta_norm >= theta_norm`
(live filter on geometry). This ensures consistent counts across tools, including for patterns
with `group_by_property` where per-group thetas differ from the global theta.

### passive_scan source types

`passive_scan` dispatches sources by `type` field: `geometry` (default), `borderline`,
`points`, `compound`. See [tools.md](tools.md#passive_scan) for JSON schema.

### find_similar_entities degenerate warning

When >50% of ANN neighbors have `distance=0` (common on patterns with high `inactive_ratio`),
the response includes `degenerate_warning` and `population_diversity_note`. Smart mode
step handler also surfaces this warning.
