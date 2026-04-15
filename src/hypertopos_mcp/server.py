# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Core MCP server instance, global state, and sphere lifecycle helpers."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json as _json
import os
import sys
import time
from functools import wraps
from typing import Any

from hypertopos.sphere import HyperSphere  # eager import — avoids first-call latency
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel.server import NotificationOptions
from mcp.types import SamplingMessage, TextContent

# ---------------------------------------------------------------------------
# Anthropic API fallback for sampling when client doesn't support it
# ---------------------------------------------------------------------------
_anthropic_client: object | None = None  # None=untried, False=unavailable


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is False:
        return None  # negative cache — don't retry failed imports
    if _anthropic_client is not None:
        return _anthropic_client
    try:
        from anthropic import AsyncAnthropic
        _anthropic_client = AsyncAnthropic()
        return _anthropic_client
    except Exception:
        _anthropic_client = False  # cache negative result
        return None


# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------
def _record_timing(fn_name: str, elapsed_ms: float, result: str) -> str:
    """Record call stats and inject elapsed_ms into JSON result."""
    _call_stats["call_count"] += 1
    _call_stats["total_elapsed_ms"] += elapsed_ms
    if fn_name not in _call_stats["per_tool"]:
        _call_stats["per_tool"][fn_name] = {"count": 0, "total_ms": 0.0}
    _call_stats["per_tool"][fn_name]["count"] += 1
    _call_stats["per_tool"][fn_name]["total_ms"] += elapsed_ms
    with contextlib.suppress(Exception):
        data = _json.loads(result)
        if isinstance(data, dict):
            data["elapsed_ms"] = elapsed_ms
            return _json.dumps(data, separators=(",", ":"))
    return result


def timed(fn):
    """Inject elapsed_ms into the JSON response and accumulate call stats."""

    if asyncio.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            result = await fn(*args, **kwargs)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            return _record_timing(fn.__name__, elapsed_ms, result)

        return async_wrapper

    @wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return _record_timing(fn.__name__, elapsed_ms, result)

    return wrapper


mcp = FastMCP(
    "hypertopos",
    instructions=(
        "Navigate a Geometric Data Sphere (GDS). "
        "Start with open_sphere(path). Use detect_pattern(query) with "
        "descriptive sentences — include entity type and dimension name. "
        "After each result, check follow_up suggestions before moving on. "
        "When 0 results, rephrase with different dimensions not same words. "
        "For manual drill-down: call sphere_overview() to unlock all tools."
    ),
)

# Advertise tools/list_changed capability — dynamic tool loading changes
# the tool list after open_sphere / close_sphere.
_orig_create_init_opts = mcp._mcp_server.create_initialization_options


def _create_init_opts_with_tools_changed(**kwargs):
    return _orig_create_init_opts(
        notification_options=NotificationOptions(tools_changed=True),
        **kwargs,
    )


mcp._mcp_server.create_initialization_options = _create_init_opts_with_tools_changed  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Global state — one sphere / session / navigator per server process
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "sphere": None,
    "session": None,
    "navigator": None,
    "engine": None,
    "manifest": None,
    "path": None,
    "manual_mode": False,
}

# ---------------------------------------------------------------------------
# Call stats — aggregate MCP tool call counting (separate from _state so
# close_sphere can read stats after clearing _state)
# ---------------------------------------------------------------------------
_call_stats: dict[str, Any] = {
    "call_count": 0,
    "total_elapsed_ms": 0.0,
    "per_tool": {},
    "session_start": None,
}


def _reset_call_stats() -> None:
    """Zero all counters and set session_start."""
    _call_stats["call_count"] = 0
    _call_stats["total_elapsed_ms"] = 0.0
    _call_stats["per_tool"] = {}
    _call_stats["session_start"] = time.perf_counter()


def _reload_hypertopos_modules() -> None:
    """Reload hypertopos.* then hypertopos_mcp.* tool modules (leaves first, root last).

    server.py is intentionally excluded — it holds global _state and _state references
    imported by tool modules would break if server.py were reloaded.

    Note: after reload, tool module re-import re-registers tools via @mcp.tool().
    Dynamic stash may hold pre-reload Tool objects. This is acceptable for dev use
    (force_reload=True) — open_sphere calls _register_phase2_tools which refreshes.
    """
    global HyperSphere

    def _is_reloadable(n: str) -> bool:
        return (
            n == "hypertopos"
            or n.startswith("hypertopos.")
            or (n.startswith("hypertopos_mcp.") and n != "hypertopos_mcp.server")
        )

    # hypertopos.* first (library), then hypertopos_mcp.* (MCP layer); shallowest first
    # so dependencies are reloaded before consumers that import from them
    to_reload = sorted(
        [n for n in sys.modules if _is_reloadable(n)],
        key=lambda n: (
            0 if (n == "hypertopos" or n.startswith("hypertopos.")) else 1,
            n.count("."),
        ),  # noqa: E501
    )
    for mod_name in to_reload:
        importlib.reload(sys.modules[mod_name])
    from hypertopos.sphere import HyperSphere  # noqa: F811


def _require_sphere() -> None:
    if _state["sphere"] is None:
        default = os.environ.get("HYPERTOPOS_SPHERE_PATH")
        if default:
            _do_open_sphere(default)
        else:
            raise RuntimeError("No sphere open. Call open_sphere(path) first.")


def _require_navigator() -> None:
    _require_sphere()
    if _state["navigator"] is None:
        raise RuntimeError("Navigator not available.")


def _do_open_sphere(path: str) -> None:
    """Load a GDS sphere into global state (no return value)."""
    if _state["session"] is not None:
        with contextlib.suppress(Exception):
            _state["session"].close()

    hs = HyperSphere.open(path)
    hs._writer.purge_all_agents()
    session = hs.session("mcp-agent")
    navigator = session.navigator()

    _state["sphere"] = hs
    _state["session"] = session
    _state["navigator"] = navigator
    _state["engine"] = session._engine
    _state["manifest"] = session._manifest
    _state["path"] = path
    _state.pop("explored_steps", None)
    _state["manual_mode"] = False
    _reset_call_stats()


# ---------------------------------------------------------------------------
# Dynamic tool loading — capability-aware tool registration
# ---------------------------------------------------------------------------

_TOOL_TIERS: dict[str, str] = {
    # always — visible without open_sphere (Phase 1)
    "open_sphere": "always",
    "close_sphere": "always",
    "get_session_stats": "always",
    # gateway — visible after open_sphere (Phase 2: simple binary choice)
    "detect_pattern": "gateway",
    "sphere_overview": "gateway",
    # base — visible after sphere_overview (Phase 3: full manual mode)
    "get_sphere_info": "base",
    "get_line_schema": "base",
    "get_line_profile": "base",
    "search_entities": "base",
    "search_entities_fts": "base",
    "recalibrate": "base",
    "goto": "base",
    "get_position": "base",
    "walk_line": "base",
    "jump_polygon": "base",
    "emerge": "base",
    "find_anomalies": "base",
    "anomaly_summary": "base",
    "aggregate_anomalies": "base",
    "attract_boundary": "base",
    "find_neighborhood": "base",
    "get_polygon": "base",
    "get_event_polygons": "base",
    "compare_entities": "base",
    "find_similar_entities": "base",
    "search_entities_hybrid": "base",
    "find_common_relations": "base",
    "find_counterparties": "base",
    "find_chains_for_entity": "base",
    "contrast_populations": "base",
    "find_hubs": "base",
    "find_clusters": "base",
    "extract_chains": "base",
    "check_anomaly_batch": "base",
    "explain_anomaly": "base",
    "detect_data_quality_issues": "base",
    "line_geometry_stats": "base",
    "check_alerts": "base",
    "aggregate": "base",
    "detect_neighbor_contamination": "base",
    "detect_segment_shift": "base",
    "get_centroid_map": "base",
    # edge — visible after open_sphere (Phase 2), alongside gateway tools
    # These require a navigator but NOT sphere_overview — they appear immediately
    # after open_sphere so agents can traverse edges without manual mode unlock.
    "find_geometric_path": "edge",
    "discover_chains": "edge",
    "edge_stats": "edge",
    "entity_flow": "edge",
    "contagion_score": "edge",
    "contagion_score_batch": "edge",
    "degree_velocity": "edge",
    "investigation_coverage": "edge",
    "propagate_influence": "edge",
    "cluster_bridges": "edge",
    "anomalous_edges": "edge",
    "find_witness_cohort": "edge",
    "find_novel_entities": "edge",
    # temporal — visible after sphere_overview IF sphere has temporal data
    "dive_solid": "temporal",
    "get_solid": "temporal",
    "hub_history": "temporal",
    "find_drifting_entities": "temporal",
    "compare_time_windows": "temporal",
    "find_regime_changes": "temporal",
    # multi_pattern — visible after sphere_overview IF 2+ patterns cover same entity line
    "cross_pattern_profile": "multi_pattern",
    "passive_scan": "multi_pattern",
    "composite_risk": "multi_pattern",
    "composite_risk_batch": "multi_pattern",
    "detect_cross_pattern_discrepancy": "multi_pattern",
    # trajectory_index — visible after sphere_overview IF trajectory ANN index exists
    "find_drifting_similar": "trajectory_index",
    "detect_trajectory_anomaly": "trajectory_index",
}

_tool_stash: dict[str, object] = {}

_sphere_capabilities: dict[str, bool] | None = None


def _detect_capabilities() -> dict[str, bool]:
    """Derive sphere capabilities from loaded sphere metadata."""
    import pathlib

    sphere = _state["sphere"]._sphere
    patterns = sphere.patterns
    sphere_path = pathlib.Path(_state["path"])
    has_temporal = any(
        (sphere_path / "temporal" / pid).exists()
        for pid, p in patterns.items()
        if p.pattern_type == "anchor"
    )
    # multi_pattern: check if any entity line has 2+ patterns of any cross-pattern
    # key type (direct, sibling, event_edge, composite, chain).
    _CROSS_PATTERN_TYPES = ("direct", "sibling", "event_edge", "composite", "chain")
    multi_pattern = False
    nav = _state.get("navigator")
    if nav:
        seen_lines = set()
        for pid, p in patterns.items():
            if p.pattern_type == "anchor":
                el = sphere.entity_line(pid)
                if el and el not in seen_lines:
                    seen_lines.add(el)
                    pm = nav._discover_pattern_map(el)
                    n_cross = sum(
                        1 for kind in pm.values()
                        if kind in _CROSS_PATTERN_TYPES
                    )
                    if n_cross >= 2:
                        multi_pattern = True
                        break

    trajectory_dir = sphere_path / "_gds_meta" / "trajectory"
    has_trajectory_index = trajectory_dir.exists() and any(trajectory_dir.glob("*.lance"))

    return {
        "has_temporal": has_temporal,
        "multi_pattern": multi_pattern,
        "has_trajectory_index": has_trajectory_index,
    }


def _tier_available(tier: str, caps: dict[str, bool] | None) -> bool:
    """Check if a capability tier is available given current sphere capabilities."""
    if tier in ("always", "gateway", "edge"):
        return True
    if caps is None:
        return False
    if tier == "base":
        return True
    if tier == "temporal":
        return caps.get("has_temporal", False)
    if tier == "multi_pattern":
        return caps.get("multi_pattern", False)
    if tier == "trajectory_index":
        return caps.get("has_trajectory_index", False)
    return True


def _stash_tool(name: str) -> None:
    """Remove a tool from FastMCP and stash its Tool object for later re-add.

    Uses internal _tool_manager._tools — pinned to FastMCP/mcp SDK behavior.
    """
    tool_obj = mcp._tool_manager._tools.get(name)
    if tool_obj is None:
        return
    _tool_stash[name] = tool_obj
    mcp.remove_tool(name)


def _restore_tool(name: str) -> None:
    """Re-add a previously stashed tool to FastMCP.

    Restores the exact Tool object (not re-wrapped) to preserve original
    registration state. Uses internal _tool_manager._tools dict.
    """
    tool_obj = _tool_stash.pop(name, None)
    if tool_obj is None:
        return
    mcp._tool_manager._tools[name] = tool_obj


def _notify_tools_changed() -> None:
    """Send notifications/tools/list_changed to the connected client.

    At startup (no active session) this is a silent no-op.
    During open_sphere/close_sphere the request context is available
    so the notification reaches the client.
    """
    try:
        session = mcp._mcp_server.request_context.session
        asyncio.get_event_loop().create_task(session.send_tool_list_changed())
    except (LookupError, RuntimeError, AttributeError):
        pass  # no active request context (startup) — skip


def _unregister_phase2_tools() -> None:
    """Remove all non-always tools. Called at startup and after close_sphere."""
    global _sphere_capabilities
    _sphere_capabilities = None
    for name, tier in _TOOL_TIERS.items():
        if tier != "always":
            _stash_tool(name)
    _notify_tools_changed()


def _register_phase2_tools() -> None:
    """Re-add gateway + edge tools. Called after open_sphere (Phase 2).

    Exposes detect_pattern + sphere_overview + edge table tools — agent can
    traverse edges immediately without manual mode unlock.
    Full manual toolset is unlocked by _register_manual_tools() after sphere_overview.
    """
    global _sphere_capabilities
    _sphere_capabilities = _detect_capabilities()
    _state["manual_mode"] = False
    for name, tier in _TOOL_TIERS.items():
        if tier in ("gateway", "edge"):
            _restore_tool(name)
    _notify_tools_changed()


def _register_manual_tools() -> None:
    """Unlock full manual toolset. Called after sphere_overview (Phase 3).

    Restores all capability-matched tools (base, temporal, multi_pattern,
    trajectory_index). No-op if already in manual mode.
    """
    if _state.get("manual_mode"):
        return
    _state["manual_mode"] = True
    for name, tier in _TOOL_TIERS.items():
        if tier not in ("always", "gateway") and _tier_available(tier, _sphere_capabilities):
            _restore_tool(name)
    _notify_tools_changed()


# ---------------------------------------------------------------------------
# MCP Resources — cacheable sphere metadata
# ---------------------------------------------------------------------------

@mcp.resource(
    "sphere://info",
    name="sphere_info",
    description="Sphere schema: lines, patterns, aliases. Cached by client after first read.",
)
def sphere_info_resource() -> str:
    """Return sphere schema as JSON. Called on-demand by the MCP client."""
    if _state["sphere"] is None:
        return _json.dumps({"error": "No sphere open. Call open_sphere(path) first."})
    s = _state["sphere"]._sphere
    return _json.dumps(
        {
            "sphere_id": s.sphere_id,
            "name": s.name,
            "lines": {
                lid: {
                    "role": line.line_role,
                    "columns": (
                        [{"name": c.name, "type": c.type} for c in line.columns]
                        if line.columns is not None
                        else []
                    ),
                }
                for lid, line in s.lines.items()
            },
            "patterns": {
                pid: {
                    "pattern_type": p.pattern_type,
                    "entity_line": s.entity_line(pid),
                    "has_temporal": getattr(p, "has_temporal", False),
                }
                for pid, p in s.patterns.items()
            },
            "aliases": list(s.aliases.keys()),
        },
        indent=2,
    )


@mcp.resource(
    "sphere://capabilities",
    name="sphere_capabilities",
    description="Current sphere capabilities (temporal, multi-pattern, trajectory index).",
)
def sphere_capabilities_resource() -> str:
    """Return detected capabilities as JSON. Called on-demand by the MCP client."""
    if _sphere_capabilities is None:
        return _json.dumps({"error": "No sphere open or capabilities not detected yet."})
    return _json.dumps(_sphere_capabilities, indent=2)


# ---------------------------------------------------------------------------
# MCP Prompts — investigation workflow templates
# ---------------------------------------------------------------------------


@mcp.prompt()
def investigate(entity_key: str, line_id: str) -> str:
    """Deep investigation of a single entity across all patterns."""
    return (
        f"Investigate entity '{entity_key}' on line '{line_id}':\n"
        f"1. open_sphere if not already open\n"
        f"2. goto('{entity_key}', '{line_id}') to navigate to entity\n"
        f"3. get_polygon for each pattern the entity participates in\n"
        f"4. If anomalous in any pattern: explain_anomaly\n"
        f"5. find_similar_entities to check if neighbors are also anomalous\n"
        f"6. If temporal data: dive_solid to check trajectory\n"
        f"7. Summarize: is this entity genuinely anomalous or a false positive?"
    )


@mcp.prompt()
def scan(pattern_id: str = "") -> str:
    """Population-level anomaly scan across one or all patterns."""
    target = f"pattern '{pattern_id}'" if pattern_id else "all patterns"
    return (
        f"Run a full population scan on {target}:\n"
        f"1. sphere_overview for population health\n"
        f"2. find_anomalies (top 20) for each pattern\n"
        f"3. detect_segment_shift to find over-represented segments\n"
        f"4. find_hubs to identify most connected entities\n"
        f"5. If temporal: find_regime_changes for structural shifts\n"
        f"6. Report: anomaly rate, top findings, segment insights, temporal events"
    )


@mcp.prompt()
def compare(key_a: str, key_b: str, pattern_id: str) -> str:
    """Compare two entities geometrically."""
    return (
        f"Compare entities '{key_a}' and '{key_b}' in pattern '{pattern_id}':\n"
        f"1. goto each entity and get_polygon\n"
        f"2. compare_entities('{key_a}', '{key_b}', '{pattern_id}')\n"
        f"3. find_common_relations to see shared connections\n"
        f"4. contrast_populations between the two (if both anomalous)\n"
        f"5. Report: which dimensions differ most, shared relations, risk assessment"
    )


# ---------------------------------------------------------------------------
# MCP Sampling helpers
# ---------------------------------------------------------------------------

async def _sample_llm(
    ctx: object,
    prompt: str,
    system_prompt: str = "You are a data analysis assistant. Be concise.",
    max_tokens: int = 400,
    temperature: float = 0.1,
) -> str | None:
    """Request LLM reasoning via MCP sampling. Returns text or None on failure.

    Tries two paths in order:
    1. MCP sampling via ctx.session.create_message (if client supports it).
    2. Anthropic API direct call via AsyncAnthropic (if ``anthropic`` package
       is installed and ANTHROPIC_API_KEY is set).

    Returns None when both paths are unavailable or fail. Caller must handle
    None as "proceed without LLM filtering".
    """
    # Primary path: MCP sampling via client
    try:
        if isinstance(ctx, Context):
            caps = (
                ctx.session.client_params.capabilities
                if hasattr(ctx, "session")
                else None
            )
            if caps is not None and getattr(caps, "sampling", None) is not None:
                result = await ctx.session.create_message(
                    messages=[SamplingMessage(
                        role="user",
                        content=TextContent(type="text", text=prompt),
                    )],
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    include_context="thisServer",
                )
                if hasattr(result.content, "text"):
                    return result.content.text
                return str(result.content)
    except Exception:
        pass

    # Fallback: Anthropic API direct (when client doesn't support sampling)
    client = _get_anthropic_client()
    if client is not None:
        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return response.content[0].text
        except Exception:
            return None
    return None
