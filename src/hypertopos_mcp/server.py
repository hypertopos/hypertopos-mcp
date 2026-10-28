# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Core MCP server instance, global state, and sphere lifecycle helpers."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import difflib
import importlib
import json as _json
import math
import os
import sys
import time
from collections.abc import Callable, Iterator, MutableMapping
from functools import wraps
from typing import Any, cast

import anyio
from hypertopos.sphere import HyperSphere  # eager import — avoids first-call latency
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.models import InitializationOptions
from mcp.shared.exceptions import MCPError
from mcp.types import SamplingMessage, TextContent
from mcp_types import INVALID_PARAMS
from pydantic import create_model, model_validator

import hypertopos_mcp._format as _response_format

# ---------------------------------------------------------------------------
# Anthropic API fallback for sampling when client doesn't support it
# ---------------------------------------------------------------------------
_anthropic_client: object | None = None  # None=untried, False=unavailable


def _get_anthropic_client() -> Any:
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
def _round_float(x: float) -> float:
    """Keep max(7, integer digits + 2) significant digits.

    Drops float32 noise (0.10000000149011612 -> 0.1) without touching the
    integer digits or cents of large amounts, or the magnitude of tiny
    p-values.
    """
    if x == 0.0 or not math.isfinite(x):
        return x
    integer_digits = max(0, math.floor(math.log10(abs(x))) + 1)
    return float(f"{x:.{max(7, integer_digits + 2)}g}")


def _compact_numbers(node: Any) -> Any:
    """Round floats; non-finite ones become null so the response is strict JSON."""
    if isinstance(node, float):
        return _round_float(node) if math.isfinite(node) else None
    if isinstance(node, dict):
        return {k: _compact_numbers(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_compact_numbers(v) for v in node]
    return node


def _record_timing(
    fn_name: str,
    elapsed_ms: float,
    result: str,
    failed_parts: list[dict[str, str]] | None = None,
) -> str:
    """Record call stats; inject elapsed_ms and any failed_parts into the JSON result."""
    _call_stats["call_count"] += 1
    _call_stats["total_elapsed_ms"] += elapsed_ms
    if fn_name not in _call_stats["per_tool"]:
        _call_stats["per_tool"][fn_name] = {"count": 0, "total_ms": 0.0}
    _call_stats["per_tool"][fn_name]["count"] += 1
    _call_stats["per_tool"][fn_name]["total_ms"] += elapsed_ms
    with contextlib.suppress(Exception):
        data = _json.loads(result)
        if isinstance(data, list):
            # A bare list has nowhere to carry elapsed_ms / failed_parts.
            data = {"results": data}
        if isinstance(data, dict):
            data["elapsed_ms"] = elapsed_ms
            if failed_parts:
                data["failed_parts"] = failed_parts
        if isinstance(data, (dict, list)):
            return _json.dumps(_compact_numbers(data), separators=(",", ":"), ensure_ascii=False)
    return result


_TOOL_ERROR_TYPES: dict[type[Exception], type[ToolError] | None] = {}


def _as_tool_error(exc: Exception) -> ToolError:
    """Report a tool failure as a ToolError that keeps its message.

    The SDK passes only a ToolError's text to the model; any other exception
    reaches it as a bare "Error executing tool X", losing hints such as
    "unknown pattern" or "call sphere_overview() first". The returned error is
    also an instance of the original exception type, so code calling a tool
    function directly can still catch ValueError, GDSError and the like.
    """
    cls = type(exc)
    if cls not in _TOOL_ERROR_TYPES:
        try:
            combined: type[ToolError] | None = type(
                cls.__name__, (ToolError, cls), {"__module__": cls.__module__}
            )
        except TypeError:  # incompatible exception layouts
            combined = None
        _TOOL_ERROR_TYPES[cls] = combined
    message = f"{cls.__name__}: {exc}"
    combined = _TOOL_ERROR_TYPES[cls]
    if combined is None:
        return ToolError(message)
    error = combined.__new__(combined)
    Exception.__init__(error, message)
    return error


def timed[F: Callable[..., Any]](fn: F) -> F:
    """Inject elapsed_ms into the JSON response and accumulate call stats."""

    if asyncio.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> str:
            t0 = time.perf_counter()
            token = _response_format.collect_failures()
            try:
                result = await fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as exc:
                raise _as_tool_error(exc) from exc
            finally:
                failed_parts = _response_format.take_failures(token)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            return _record_timing(fn.__name__, elapsed_ms, result, failed_parts)

        return cast(F, async_wrapper)

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        t0 = time.perf_counter()
        token = _response_format.collect_failures()
        try:
            result = fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise _as_tool_error(exc) from exc
        finally:
            failed_parts = _response_format.take_failures(token)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return _record_timing(fn.__name__, elapsed_ms, result, failed_parts)

    return cast(F, wrapper)


# The SDK runs synchronous tools on worker threads, so calls could overlap.
# Each client session holds one sphere/session/navigator in ``_state`` and
# shares its Lance handles, so a session's tool calls run one at a time;
# calls of different sessions run concurrently.
_SERIALIZED_METHODS = frozenset({"tools/call", "resources/read", "prompts/get"})
_SESSIONLESS_METHODS = frozenset({"server/discover"})


async def _serialize_tool_calls(ctx: Any, call_next: Callable[[Any], Any]) -> Any:
    """Server middleware: bind the request to its client session; show and allow
    only the tools of the session's phase; run a session's requests that read its
    state one at a time."""
    if ctx.method in _SESSIONLESS_METHODS:
        # Capability discovery reads no session state; refusing it would break
        # the client's connect instead of answering its first real request.
        return await call_next(ctx)
    key = session_key(ctx)
    evict_idle_header_sessions()
    if key.startswith("http:"):
        connection = ctx.session._connection
        if not connection.state.get("hypertopos_evict"):
            # The SDK closes this stack when the HTTP session ends (DELETE or
            # idle timeout): release that session's sphere with it.
            connection.state["hypertopos_evict"] = True
            connection.exit_stack.push_async_callback(_evict_session_when_idle, key)
    with session_scope(key) as state:
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                shown = visible_tools()
                result["tools"] = [t for t in result["tools"] if t.get("name") in shown]
            return result
        if ctx.method == "tools/call":
            name = (getattr(ctx, "params", None) or {}).get("name")
            if name is not None and name not in visible_tools():
                raise MCPError(
                    INVALID_PARAMS,
                    f"Tool {name!r} is not available yet: call open_sphere(path), "
                    "then sphere_overview() to unlock the full toolset.",
                )
        if ctx.method not in _SERIALIZED_METHODS:
            return await call_next(ctx)
        async with state.lock:
            return await call_next(ctx)


mcp = MCPServer(
    "hypertopos",
    instructions=(
        "Navigate a Geometric Data Sphere (GDS). "
        "Start with open_sphere(path). Use detect_pattern(query) with "
        "descriptive sentences — include entity type and dimension name. "
        "After each result, check follow_up suggestions before moving on. "
        "When 0 results, rephrase with different dimensions not same words. "
        "For manual drill-down: call sphere_overview() to unlock all tools."
    ),
    middleware=[_serialize_tool_calls],
)

# Every tool returns a JSON string; without this the SDK also attaches the same
# JSON as structuredContent and an outputSchema, doubling each response.
_register_tool = mcp.tool


def _strip_schema_titles(node: Any) -> None:
    """Drop the generated ``title`` keywords from a JSON schema in place.

    Only string-valued ``title`` keys are schema keywords; a parameter that
    is itself named "title" maps to a dict and stays.
    """
    if isinstance(node, dict):
        if isinstance(node.get("title"), str):
            del node["title"]
        for value in node.values():
            _strip_schema_titles(value)
    elif isinstance(node, list):
        for value in node:
            _strip_schema_titles(value)


def _reject_unknown_args(tool: Any) -> None:
    """Make an argument the tool does not declare an error naming the closest one.

    Pydantic ignores unknown keys by default, so a misspelled parameter
    (``limit`` for ``top_n``) would silently fall back to its default.
    """
    base = tool.fn_metadata.arg_model
    names = sorted(field.alias or name for name, field in base.model_fields.items())
    tool_name = tool.name

    def check(cls: Any, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                if key not in names:
                    close = difflib.get_close_matches(key, names, n=1)
                    hint = f"; did you mean '{close[0]}'?" if close else "."
                    raise ValueError(
                        f"unknown parameter '{key}' for tool '{tool_name}'{hint} "
                        f"Valid parameters: {', '.join(names)}"
                    )
        return data

    tool.fn_metadata.arg_model = create_model(
        base.__name__,
        __base__=base,
        __validators__={
            # pydantic types the decorated validator as a descriptor proxy
            "reject_unknown_args": cast("Callable[..., Any]", model_validator(mode="before")(check))
        },
    )


def _tool(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("structured_output", False)
    register = _register_tool(*args, **kwargs)

    def decorator(fn: Any) -> Any:
        name = kwargs.get("name") or (args[0] if args and isinstance(args[0], str) else None)
        name = name or fn.__name__
        # The SDK keeps the first Tool registered under a name. A module that
        # registers the name again is being reloaded (open_sphere(force_reload=True)):
        # serve its new body. A same-name tool from another module stays a duplicate.
        existing = mcp._tool_manager._tools.get(name)
        if existing is not None and getattr(existing.fn, "__module__", None) == getattr(
            fn, "__module__", None
        ):
            mcp._tool_manager.remove_tool(name)
        registered = register(fn)
        # The generated titles repeat each parameter name; they are sent with
        # every tool schema in every conversation.
        tool = mcp._tool_manager._tools.get(name)
        if tool is not None:
            _strip_schema_titles(tool.parameters)
            _reject_unknown_args(tool)
        return registered

    return decorator


mcp.tool = _tool  # type: ignore[method-assign]

# Advertise tools/list_changed capability — dynamic tool loading changes
# the tool list after open_sphere / close_sphere.
_orig_create_init_opts = mcp._lowlevel_server.create_initialization_options


def _create_init_opts_with_tools_changed(**kwargs: Any) -> InitializationOptions:
    return _orig_create_init_opts(
        notification_options=NotificationOptions(tools_changed=True),
        **kwargs,
    )


mcp._lowlevel_server.create_initialization_options = _create_init_opts_with_tools_changed  # type: ignore[assignment]  # monkeypatch: advertise tools/list_changed


# ---------------------------------------------------------------------------
# Session state — one sphere / session / navigator per client session.
# stdio and in-memory clients use the process's own "local" session; HTTP
# clients get one per Mcp-Session-Id (or X-Hypertopos-Session header).
# ---------------------------------------------------------------------------
def _new_state() -> dict[str, Any]:
    return {
        "sphere": None,
        "session": None,
        "navigator": None,
        "engine": None,
        "manifest": None,
        "path": None,
        "manual_mode": False,
        "response_format": None,
        "phase": 1,
        "capabilities": None,
    }


def _new_stats() -> dict[str, Any]:
    return {"call_count": 0, "total_elapsed_ms": 0.0, "per_tool": {}, "session_start": None}


@dataclasses.dataclass
class SessionState:
    """Everything one client session reads and writes through ``_state``."""

    key: str
    data: dict[str, Any] = dataclasses.field(default_factory=_new_state)
    stats: dict[str, Any] = dataclasses.field(default_factory=_new_stats)
    lock: anyio.Lock = dataclasses.field(default_factory=anyio.Lock)
    last_used: float = dataclasses.field(default_factory=time.monotonic)


_LOCAL_KEY = "local"
_sessions: dict[str, SessionState] = {_LOCAL_KEY: SessionState(_LOCAL_KEY)}
_current_session: contextvars.ContextVar[SessionState | None] = contextvars.ContextVar(
    "hypertopos_session", default=None
)


def current_session() -> SessionState:
    """The session the running request belongs to (the local one outside requests)."""
    return _current_session.get() or _sessions[_LOCAL_KEY]


def session_key(ctx: Any) -> str:
    """Key of the client session a request belongs to.

    Stateful HTTP connections carry ``Mcp-Session-Id``; duplex transports
    (stdio, in-memory clients) have no HTTP request and map to the process's
    own session; HTTP without a session id (a stateless embedding, or any
    client on the stateless protocol) must send ``X-Hypertopos-Session``.
    Such a request without the header is refused: two clients would
    otherwise share one open sphere.
    """
    connection = getattr(getattr(ctx, "session", None), "_connection", None)
    session_id = getattr(connection, "session_id", None)
    if session_id:
        return f"http:{session_id}"
    request = getattr(ctx, "request", None)
    if request is None:
        return _LOCAL_KEY
    header = (getattr(request, "headers", None) or {}).get("x-hypertopos-session")
    if not header:
        raise MCPError(
            INVALID_PARAMS,
            "This HTTP request carries no Mcp-Session-Id (stateless protocol): send an "
            "X-Hypertopos-Session header naming your session, or connect with the "
            "legacy (initialize) protocol.",
        )
    return f"header:{header}"


def live_session_keys() -> list[str]:
    """Keys of the client sessions that have a sphere open."""
    return [key for key, state in _sessions.items() if state.data.get("session") is not None]


def forget_session(key: str) -> None:
    """Drop a client session from the registry (the local session always stays)."""
    if key != _LOCAL_KEY:
        _sessions.pop(key, None)


# Sessions keyed by X-Hypertopos-Session have no
# connection whose end evicts them; they go after this many idle seconds, the
# same timeout the HTTP transport applies to its own sessions.
HEADER_SESSION_IDLE_SECONDS = 1800.0


def set_header_session_idle_timeout(seconds: float) -> None:
    """Idle seconds after which a header-keyed or anonymous session is evicted."""
    global HEADER_SESSION_IDLE_SECONDS
    HEADER_SESSION_IDLE_SECONDS = seconds


def evict_idle_header_sessions() -> None:
    """Evict header-keyed sessions idle past the timeout.

    A session with a call in progress (its lock held) stays.
    """
    cutoff = time.monotonic() - HEADER_SESSION_IDLE_SECONDS
    for key, state in list(_sessions.items()):
        header_keyed = key.startswith("header:")
        if header_keyed and state.last_used < cutoff and not state.lock.locked():
            _evict_session(key)


def _evict_session(key: str) -> None:
    """Close a client session's sphere and drop the session from the registry."""
    state = _sessions.pop(key, None)
    session = state.data.get("session") if state is not None else None
    if session is not None:
        with contextlib.suppress(Exception):
            session.close(purge_temporal=True)


# The SDK gives a connection's exit stack a few seconds to unwind; eviction
# waits a little less than that for a running call before closing anyway.
_EVICT_WAIT_SECONDS = 4.0


async def _evict_session_when_idle(key: str) -> None:
    """Evict ``key`` once its running call (if any) has finished."""
    state = _sessions.get(key)
    if state is not None:
        with anyio.move_on_after(_EVICT_WAIT_SECONDS):
            async with state.lock:
                _evict_session(key)
                return
    _evict_session(key)


@contextlib.contextmanager
def session_scope(key: str) -> Iterator[SessionState]:
    """Run the enclosed block in session ``key`` (created on first use)."""
    state = _sessions.setdefault(key, SessionState(key))
    state.last_used = time.monotonic()
    token = _current_session.set(state)
    try:
        yield state
    finally:
        _current_session.reset(token)


class _SessionMapping(MutableMapping[str, Any]):
    """Module-level name that forwards to one dict of the current session."""

    def __init__(self, attr: str) -> None:
        self._attr = attr

    def _target(self) -> dict[str, Any]:
        target: dict[str, Any] = getattr(current_session(), self._attr)
        return target

    def __getitem__(self, key: str) -> Any:
        return self._target()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._target()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._target()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._target())

    def __len__(self) -> int:
        return len(self._target())

    def __repr__(self) -> str:
        return f"<{self._attr} of session {current_session().key!r}: {self._target()!r}>"


_state: MutableMapping[str, Any] = _SessionMapping("data")

RESPONSE_FORMATS = ("compact", "full")


def active_response_format() -> str:
    """Session override (``open_sphere(response_format=)``) > ``HYPERTOPOS_MCP_FORMAT`` > compact.

    ``compact`` drops subtrees an agent rarely needs (positional delta vectors when a
    labelled form is present, numeric properties columns) and says so in ``omitted``;
    ``full`` returns everything.
    """
    session = _state.get("response_format")
    if session in RESPONSE_FORMATS:
        return str(session)
    env = os.environ.get("HYPERTOPOS_MCP_FORMAT", "compact").strip().lower()
    return env if env in RESPONSE_FORMATS else "compact"


def resolve_verbose(verbose: bool | None) -> bool:
    """Explicit per-call value wins; ``None`` follows the session format (full → True)."""
    return verbose if verbose is not None else active_response_format() == "full"


# ---------------------------------------------------------------------------
# Call stats — per-session MCP tool call counting (separate from _state so
# close_sphere can read stats after clearing _state)
# ---------------------------------------------------------------------------
_call_stats: MutableMapping[str, Any] = _SessionMapping("stats")


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

    Note: after reload, tool module re-import re-registers tools via @mcp.tool(),
    which replaces the registered Tool objects (see ``_tool``); tool visibility is
    per-session state.
    """
    global HyperSphere

    def _is_reloadable(n: str) -> bool:
        return (
            n == "hypertopos"
            or n.startswith("hypertopos.")
            or (n.startswith("hypertopos_mcp.") and n != "hypertopos_mcp.server")
        )

    # hypertopos.* first (library), then hypertopos_mcp.* (MCP layer). Package
    # roots go last within their layer: a root re-imports names from its
    # submodules, so those must already be reloaded.
    to_reload = sorted(
        [n for n in sys.modules if _is_reloadable(n)],
        key=lambda n: (
            0 if (n == "hypertopos" or n.startswith("hypertopos.")) else 1,
            n in ("hypertopos", "hypertopos_mcp"),
            n.count("."),
        ),
    )
    for mod_name in to_reload:
        importlib.reload(sys.modules[mod_name])
    from hypertopos.sphere import HyperSphere  # noqa: F811


def _require_sphere() -> None:
    if _state["sphere"] is None:
        raise RuntimeError(
            "No sphere open. Call open_sphere(path) first "
            "(open_sphere() without a path opens HYPERTOPOS_SPHERE_PATH when it is set)."
        )


def _require_navigator() -> None:
    _require_sphere()
    if _state["navigator"] is None:
        raise RuntimeError("Navigator not available.")


def _do_open_sphere(path: str, branch: str | None = None) -> None:
    """Load a GDS sphere (main, or ``branch``) into global state.

    The previous session stays open and in place if loading the new one fails.
    """
    hs = HyperSphere.open(path, branch=branch)
    key = current_session().key
    if key == _LOCAL_KEY:
        # Other server processes (and HTTP sessions) may share the sphere; clear only ours.
        agent_id = f"mcp-{os.getpid()}"
    else:
        # Other client sessions may be open on the same sphere; clear only ours.
        agent_id = "mcp-" + "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    hs._writer.purge_agent_temporal(agent_id)
    session = hs.session(agent_id)
    # Tools that read the open-time sphere see the session's snapshot.
    hs._sphere = session.sphere()
    navigator = session.navigator()

    if _state["session"] is not None:
        with contextlib.suppress(Exception):
            _state["session"].close()
    _state["sphere"] = hs
    _state["session"] = session
    _state["navigator"] = navigator
    _state["engine"] = session._engine
    _state["manifest"] = session._manifest
    _state["path"] = path
    _state["branch"] = branch
    _state.pop("explored_steps", None)
    _state.pop("investigation_hints", None)
    _state["manual_mode"] = False
    _reset_call_stats()


def _refresh_state() -> None:
    """Point the open-time sphere at the session's current snapshot.

    Call after any tool that writes sphere state through the session.
    """
    _state["sphere"]._sphere = _state["session"].sphere()


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
    "manage_branch": "base",
    "compare_branches": "base",
    "goto": "base",
    "get_position": "base",
    "walk_line": "base",
    "jump_polygon": "base",
    "emerge": "base",
    "find_anomalies": "base",
    "anomaly_summary": "base",
    "aggregate_anomalies": "base",
    "audit_pattern_dims": "base",
    "audit_label_alignment": "base",
    "attract_boundary": "base",
    "get_polygon": "base",
    "get_event_polygons": "base",
    "compare_entities": "base",
    "compare_calibrations": "base",
    "decompose_drift": "base",
    "find_calibration_influencers": "base",
    "calibration_influencer_history": "base",
    "find_group_influence": "base",
    "find_lead_lag": "base",
    "find_density_gaps": "base",
    "find_motif_by_hops": "base",
    "find_similar_entities": "base",
    "search_entities_hybrid": "base",
    "find_common_relations": "base",
    "find_counterparties": "base",
    "find_chains_for_entity": "base",
    "contrast_populations": "base",
    "find_hubs": "base",
    "find_clusters": "base",
    "extract_chains": "base",
    "find_chains_with_coherent_anomaly": "base",
    "anomaly_propagation_in_chain": "base",
    "classify_chain_typology": "base",
    "chain_witness_intersection": "base",
    "chain_drift_trajectory": "base",
    "chain_investigation_summary": "base",
    "investigate_chain": "base",
    "chain_full_loop_summary": "base",
    "generate_sar_rationale": "base",
    "extend_chain": "base",
    "theta_sensitivity": "base",
    "check_anomaly_batch": "base",
    "explain_anomaly": "base",
    "find_diverse_explanations": "base",
    "assess_anomaly_certainty": "base",
    "calibration_drift_report": "base",
    "diverse_explanations": "base",
    "theta_sensitivity_report": "base",
    "find_conformance_violations": "base",
    "trace_root_cause": "base",
    "score_edge": "base",
    "find_high_potential_edges": "base",
    "score_motif": "base",
    "find_high_potential_motifs": "base",
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
    "find_topological_anomalies": "base",
    "find_graph_geometry_tension": "edge",
    "simulate_edge_removal": "edge",
    "simulate_dimension_change": "edge",
    "simulate_counterparty_removal": "edge",
    "select_minimal_joint_edge_removal": "edge",
    "investigate_entity": "base",
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
    "combine_anomaly_pvalues": "multi_pattern",
    "classify_detector_consensus": "multi_pattern",
    "consensus_classification": "multi_pattern",
    "detect_cross_pattern_discrepancy": "multi_pattern",
    # trajectory_index — visible after sphere_overview IF trajectory ANN index exists
    "find_drifting_similar": "trajectory_index",
    "detect_trajectory_anomaly": "trajectory_index",
    "classify_trajectory": "trajectory_index",
}


def sphere_capabilities() -> dict[str, bool] | None:
    """Capabilities of the current session's open sphere (None before open_sphere)."""
    caps: dict[str, bool] | None = _state.get("capabilities")
    return caps


def visible_tools() -> set[str]:
    """Tools the current session sees: its phase and its sphere's capabilities.

    Phase 1 (no sphere): ``always``. Phase 2 (after open_sphere): plus
    ``gateway`` and ``edge``. Phase 3 (after sphere_overview): every tier the
    sphere can serve. Tools without a tier are always visible.
    """
    phase = _state.get("phase") or 1
    caps = sphere_capabilities()
    visible = set()
    for tool in mcp._tool_manager.list_tools():
        tier = _TOOL_TIERS.get(tool.name, "always")
        if (
            tier == "always"
            or (phase >= 2 and tier in ("gateway", "edge"))
            or (phase >= 3 and _tier_available(tier, caps))
        ):
            visible.add(tool.name)
    return visible


def _detect_capabilities() -> dict[str, bool]:
    """Derive sphere capabilities from loaded sphere metadata."""
    import pathlib

    sphere = _state["sphere"]._sphere
    patterns = sphere.patterns
    sphere_path = pathlib.Path(_state["path"])
    has_temporal = any(
        (sphere_path / "temporal" / pid / "data.lance").exists()
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
                    n_cross = sum(1 for kind in pm.values() if kind in _CROSS_PATTERN_TYPES)
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


def _notify_tools_changed(ctx: Context | None = None) -> None:
    """Send notifications/tools/list_changed to the connected client.

    ``ctx`` is the request context of the tool that changed the tool list;
    without one (startup, direct calls) this is a no-op. Synchronous tools
    run in a worker thread, so the notification is sent through the event
    loop from there.
    """
    if ctx is None:
        return
    # Handshake-era clients get notifications/tools/list_changed on the
    # session; 2026-era clients only through subscriptions/listen.
    for send in (ctx.session.send_tool_list_changed, ctx.notify_tools_changed):
        try:
            anyio.from_thread.run(send)
        except RuntimeError:
            # Already on the event loop (an async tool): schedule it.
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(send())


def _unregister_phase2_tools(ctx: Context | None = None) -> None:
    """Back to Phase 1 for this session. Called at startup and after close_sphere."""
    _state["phase"] = 1
    _state["capabilities"] = None
    _notify_tools_changed(ctx)


def _register_phase2_tools(ctx: Context | None = None) -> None:
    """Phase 2 for this session: gateway + edge tools. Called after open_sphere.

    Exposes detect_pattern + sphere_overview + edge table tools — agent can
    traverse edges immediately without manual mode unlock.
    Full manual toolset is unlocked by _register_manual_tools() after sphere_overview.
    """
    _state["capabilities"] = _detect_capabilities()
    _state["manual_mode"] = False
    _state["phase"] = 2
    _notify_tools_changed(ctx)


def _register_manual_tools(ctx: Context | None = None) -> None:
    """Phase 3 for this session: every capability-matched tool. Called after sphere_overview.

    No-op if already in manual mode.
    """
    if _state.get("manual_mode"):
        return
    _state["manual_mode"] = True
    _state["phase"] = 3
    _notify_tools_changed(ctx)


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
    caps = sphere_capabilities()
    if caps is None:
        return _json.dumps({"error": "No sphere open or capabilities not detected yet."})
    return _json.dumps(caps, indent=2)


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
            caps = ctx.session.client_capabilities
            if caps is not None and getattr(caps, "sampling", None) is not None:
                result = await ctx.session.create_message(
                    messages=[
                        SamplingMessage(
                            role="user",
                            content=TextContent(type="text", text=prompt),
                        )
                    ],
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    include_context="thisServer",
                )
                if hasattr(result.content, "text"):
                    return cast(str, result.content.text)
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
            return cast(str, response.content[0].text)
        except Exception:
            return None
    return None
