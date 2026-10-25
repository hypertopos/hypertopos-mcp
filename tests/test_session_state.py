# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Per-client session state: the _state proxy, session keys, context propagation."""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest


def _ctx(*, session_id: str | None = None, request: object = None, headers: dict | None = None):
    connection = SimpleNamespace(session_id=session_id, state={}, exit_stack=AsyncExitStack())
    req = request
    if headers is not None:
        req = SimpleNamespace(headers=headers)
    return SimpleNamespace(session=SimpleNamespace(_connection=connection), request=req)


@pytest.mark.parametrize(
    ("ctx_kwargs", "expected"),
    [
        ({}, "local"),  # stdio / in-memory duplex: the process's own session
        ({"session_id": "abc"}, "http:abc"),  # stateful HTTP
        ({"headers": {"x-hypertopos-session": "agent-7"}}, "header:agent-7"),
        ({"headers": {}}, "anonymous"),  # HTTP without any key
    ],
)
def test_session_key_resolution(ctx_kwargs: dict, expected: str) -> None:
    from hypertopos_mcp.server import session_key

    assert session_key(_ctx(**ctx_kwargs)) == expected


def test_sdk_still_exposes_the_connection_session_id() -> None:
    """Canary: session_key reads ServerSession._connection.session_id (private in SDK 2.3)."""
    import inspect

    from mcp.server.connection import Connection
    from mcp.server.session import ServerSession

    assert "self._connection" in inspect.getsource(ServerSession.__init__)
    assert "session_id" in inspect.getsource(Connection)
    # Session eviction registers on the connection's per-connection scratch and exit stack.
    assert "self.state" in inspect.getsource(Connection)
    assert "self.exit_stack" in inspect.getsource(Connection)


def test_state_proxy_reads_and_writes_the_current_session() -> None:
    from hypertopos_mcp.server import _state, session_scope

    default_path = _state.get("path")
    with session_scope("http:one"):
        assert _state["sphere"] is None and _state["path"] is None
        _state["path"] = "one"
        assert dict(_state)["path"] == "one" and "path" in list(_state.keys())
        with session_scope("http:two"):
            assert _state["path"] is None
            _state.update(path="two")
        assert _state["path"] == "one"
    assert _state.get("path") == default_path


def test_call_stats_are_per_session() -> None:
    from hypertopos_mcp.server import _call_stats, _record_timing, session_scope

    with session_scope("http:stats"):
        before = _call_stats["call_count"]
        _record_timing("t", 1.0, "{}")
        assert _call_stats["call_count"] == before + 1
    with session_scope("http:other-stats"):
        assert _call_stats["call_count"] == 0


def test_client_sessions_open_spheres_independently(open_berka_sphere: str) -> None:
    from hypertopos_mcp.server import _state, session_scope
    from hypertopos_mcp.tools.session import close_sphere, open_sphere

    with session_scope("http:a"):
        open_sphere(open_berka_sphere)
        agent_a = _state["session"]._manifest.agent_id
    with session_scope("http:b"):
        assert _state["session"] is None
        open_sphere(open_berka_sphere)
        agent_b = _state["session"]._manifest.agent_id
        close_sphere()
        assert _state["session"] is None
    with session_scope("http:a"):
        assert _state["session"] is not None, "closing b must not close a"
        close_sphere()

    assert agent_a == "mcp-http_a" and agent_b == "mcp-http_b"


def test_session_stats_name_the_session_and_count_live_ones(open_berka_sphere: str) -> None:
    import json

    from hypertopos_mcp.tools.session import close_sphere, get_session_stats, open_sphere

    with session_scope_for("http:s1"):
        open_sphere(open_berka_sphere)
    with session_scope_for("http:s2"):
        open_sphere(open_berka_sphere)
        stats = json.loads(get_session_stats())
        assert stats["session_key"] == "http:s2"
        assert stats["sessions_live"] >= 2
        close_sphere()
    with session_scope_for("http:s1"):
        close_sphere()


def test_close_sphere_forgets_a_client_session(open_berka_sphere: str) -> None:
    from hypertopos_mcp.server import _sessions
    from hypertopos_mcp.tools.session import close_sphere, open_sphere

    with session_scope_for("http:gone"):
        open_sphere(open_berka_sphere)
        close_sphere()
    assert "http:gone" not in _sessions


def test_force_reload_refused_while_another_session_is_open(open_berka_sphere: str) -> None:
    from hypertopos_mcp.tools.session import close_sphere, open_sphere

    with session_scope_for("http:holder"):
        open_sphere(open_berka_sphere)
    try:
        with session_scope_for("http:reloader"), pytest.raises(RuntimeError, match="force_reload"):
            open_sphere(open_berka_sphere, force_reload=True)
    finally:
        with session_scope_for("http:holder"):
            close_sphere()


def _peak_concurrency(session_ids: list[str | None]) -> int:
    import anyio

    from hypertopos_mcp.server import _serialize_tool_calls

    running = peak = 0

    async def slow_call(_ctx: object) -> dict:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await anyio.sleep(0.05)
        running -= 1
        return {}

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            for sid in session_ids:
                ctx = _ctx(session_id=sid)
                ctx.method = "tools/call"
                tg.start_soon(_serialize_tool_calls, ctx, slow_call)

    anyio.run(main)
    return peak


def test_calls_of_different_sessions_run_concurrently() -> None:
    assert _peak_concurrency(["lock-a", "lock-b"]) == 2


def test_calls_of_one_session_run_one_at_a_time() -> None:
    assert _peak_concurrency(["lock-c", "lock-c", "lock-c"]) == 1


def session_scope_for(key: str):
    from hypertopos_mcp.server import session_scope

    return session_scope(key)


def test_parallel_steps_run_in_the_callers_session() -> None:
    from hypertopos_mcp.server import _state, session_scope
    from hypertopos_mcp.tools.smart import _run_independent_steps

    def run(spec: dict) -> tuple[str, dict]:
        return spec["name"], {"path": _state["path"]}

    with session_scope("http:steps"):
        _state["path"] = "mine"
        results = _run_independent_steps([{"name": "a"}, {"name": "b"}, {"name": "c"}], run)

    assert results == {n: {"path": "mine"} for n in ("a", "b", "c")}
