# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Streamable HTTP: two clients get separate sessions, dropped when they disconnect."""

from __future__ import annotations

import contextlib
import json
import socket
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anyio


def _leaves(exc: BaseException) -> list[BaseException]:
    """The innermost exceptions of a (nested) exception group."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_two_http_clients_get_isolated_sessions_evicted_on_disconnect(
    open_berka_sphere: str,
) -> None:
    import uvicorn
    from mcp.client.client import Client

    from hypertopos_mcp.server import _sessions, mcp

    seen: dict[str, dict] = {}

    async def client_run(name: str, url: str) -> None:
        async with Client(url, mode="legacy") as client:
            await client.call_tool("open_sphere", {"path": open_berka_sphere})
            stats = await client.call_tool("get_session_stats", {})
            seen[name] = json.loads(stats.content[0].text)

    async def main() -> None:
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"
            )
        )
        url = f"http://127.0.0.1:{port}/mcp"
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            async with anyio.create_task_group() as clients:
                clients.start_soon(client_run, "a", url)
                clients.start_soon(client_run, "b", url)
            with anyio.fail_after(5):
                while any(
                    k in _sessions for k in (seen["a"]["session_key"], seen["b"]["session_key"])
                ):
                    await anyio.sleep(0.05)
            server.should_exit = True

    anyio.run(main)

    key_a, key_b = seen["a"]["session_key"], seen["b"]["session_key"]
    assert key_a.startswith("http:") and key_b.startswith("http:")
    assert key_a != key_b
    assert seen["a"]["sessions_live"] >= 1 and seen["b"]["sessions_live"] >= 1


def test_idle_http_session_is_evicted() -> None:
    import urllib.request

    import uvicorn

    from hypertopos_mcp.server import _sessions, mcp

    async def main() -> str:
        port = _free_port()
        app = mcp.streamable_http_app(session_idle_timeout=0.5, json_response=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        url = f"http://127.0.0.1:{port}/mcp"
        headers = {"accept": "application/json, text/event-stream"}
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "idle", "version": "0"},
            },
        }
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            request = urllib.request.Request(  # a bare POST; no DELETE ever follows
                url,
                data=json.dumps(init).encode(),
                headers={**headers, "content-type": "application/json"},
            )
            response = await anyio.to_thread.run_sync(urllib.request.urlopen, request)
            key = "http:" + response.headers["mcp-session-id"]
            response.close()
            assert key in _sessions
            with anyio.fail_after(5):
                while key in _sessions:
                    await anyio.sleep(0.05)
            server.should_exit = True
        return key

    assert anyio.run(main) not in _sessions


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "raw", "version": "0"},
    },
}


def _call(name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _send(
    url: str, body: dict | None, headers: dict[str, str], method: str = "POST"
) -> tuple[int, str | None, str]:
    """One raw HTTP request; returns (status, Mcp-Session-Id, body)."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            **headers,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.headers["mcp-session-id"], response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, None, error.read().decode()


def _serve(app: object, client: Callable[[str], object]) -> None:
    """Serve ``app`` on a free port and run the blocking ``client(url)`` against it."""
    import uvicorn

    async def main() -> None:
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            try:
                await anyio.to_thread.run_sync(client, f"http://127.0.0.1:{port}/mcp")
            finally:
                server.should_exit = True

    anyio.run(main)


def test_session_over_max_sessions_is_refused_until_one_ends() -> None:
    from hypertopos_mcp.server import _sessions, mcp

    app = mcp.streamable_http_app(max_sessions=1, json_response=True)
    seen: dict[str, Any] = {}

    def client(url: str) -> None:
        status, first, _ = _send(url, _INIT, {})
        assert status == 200 and first
        keys_before = set(_sessions)
        seen["refused"] = _send(url, _INIT, {})
        seen["no_state_for_refused"] = set(_sessions) == keys_before
        headers = {"mcp-session-id": first, "mcp-protocol-version": "2025-06-18"}
        seen["first_still_serves"] = _send(url, _call("get_session_stats", {}), headers)
        seen["deleted"] = _send(url, None, headers, method="DELETE")[0]
        seen["after_delete"] = _send(url, _INIT, {})

    _serve(app, client)

    status, session_id, body = seen["refused"]
    assert status == 503 and session_id is None
    assert "Too many open sessions" in body
    assert seen["no_state_for_refused"]
    status, _, body = seen["first_still_serves"]
    assert status == 200 and '\\"session_key\\":\\"http:' in body
    assert seen["deleted"] == 200
    status, session_id, _ = seen["after_delete"]
    assert status == 200 and session_id


def test_x_hypertopos_session_header_keys_sessions_without_a_session_id(
    open_berka_sphere: str,
) -> None:
    """Stateless HTTP sends no Mcp-Session-Id: the header picks the session."""
    from hypertopos_mcp.server import _evict_session, _sessions, mcp

    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    stats: dict[str, dict] = {}

    def client(url: str) -> None:
        status, _, _ = _send(
            url, _call("open_sphere", {"path": open_berka_sphere}), {"x-hypertopos-session": "a"}
        )
        assert status == 200
        for name, header in (("a_again", "a"), ("b", "b")):
            status, _, body = _send(
                url, _call("get_session_stats", {}), {"x-hypertopos-session": header}
            )
            assert status == 200
            stats[name] = json.loads(json.loads(body)["result"]["content"][0]["text"])

    try:
        _serve(app, client)
        assert stats["a_again"]["session_key"] == "header:a"
        assert stats["b"]["session_key"] == "header:b"
        # The second request under "a" reached the sphere the first one opened.
        assert _sessions["header:a"].data["session"] is not None
        assert stats["a_again"]["total_tool_calls"] == 1
        assert _sessions["header:b"].data["session"] is None
        assert stats["b"]["total_tool_calls"] == 0
    finally:
        _evict_session("header:a")
        _evict_session("header:b")


def test_closing_the_connection_closes_its_sphere(open_berka_sphere: str) -> None:
    from hypertopos_mcp.server import _serialize_tool_calls, _sessions, session_scope
    from hypertopos_mcp.tools.session import open_sphere

    exit_stack = contextlib.AsyncExitStack()
    connection = SimpleNamespace(session_id="evict-me", state={}, exit_stack=exit_stack)
    ctx = SimpleNamespace(
        session=SimpleNamespace(_connection=connection), request=None, method="ping"
    )

    async def noop(_ctx: object) -> dict:
        return {}

    anyio.run(_serialize_tool_calls, ctx, noop)
    anyio.run(_serialize_tool_calls, ctx, noop)  # one registration per connection
    with session_scope("http:evict-me") as state:
        open_sphere(open_berka_sphere)
        session = state.data["session"]

    anyio.run(exit_stack.aclose)

    assert "http:evict-me" not in _sessions
    assert session._manifest.status == "expired"


def test_eviction_waits_for_a_running_call(open_berka_sphere: str) -> None:
    """A session ends while one of its calls runs: the sphere closes after the call."""
    from hypertopos_mcp.server import _serialize_tool_calls, _sessions, session_scope
    from hypertopos_mcp.tools.session import open_sphere

    exit_stack = contextlib.AsyncExitStack()
    connection = SimpleNamespace(session_id="busy", state={}, exit_stack=exit_stack)
    ctx = SimpleNamespace(
        session=SimpleNamespace(_connection=connection), request=None, method="ping"
    )

    async def noop(_ctx: object) -> dict:
        return {}

    anyio.run(_serialize_tool_calls, ctx, noop)
    with session_scope("http:busy") as state:
        open_sphere(open_berka_sphere)
        session = state.data["session"]
    seen_during_call = []

    async def main() -> None:
        async with anyio.create_task_group() as tg:

            async def long_call() -> None:
                async with state.lock:
                    await anyio.sleep(0.3)
                    seen_during_call.append(session._manifest.status)

            tg.start_soon(long_call)
            await anyio.sleep(0.05)
            await exit_stack.aclose()  # the client disconnects mid-call

    anyio.run(main)
    assert seen_during_call == ["active"], "the sphere must stay open until the call ends"
    assert session._manifest.status == "expired"
    assert "http:busy" not in _sessions


def test_idle_header_sessions_are_evicted() -> None:
    """Sessions keyed by X-Hypertopos-Session have no connection that ends, so the
    server evicts them after the same idle timeout as HTTP sessions."""
    import anyio

    from hypertopos_mcp import server

    closed: list[str] = []

    class FakeSession:
        def __init__(self, key: str) -> None:
            self.key = key

        def close(self, purge_temporal: bool = False) -> None:
            closed.append(self.key)

    def add(key: str, idle: float) -> server.SessionState:
        state = server.SessionState(key)
        state.data["session"] = FakeSession(key)
        state.last_used -= idle
        server._sessions[key] = state
        return state

    old = server.HEADER_SESSION_IDLE_SECONDS
    server.set_header_session_idle_timeout(60.0)
    try:
        add("header:stale", 120.0)
        add("header:fresh", 5.0)
        busy = add("header:busy", 120.0)
        add("http:abc", 120.0)

        async def sweep_while_busy() -> None:
            async with busy.lock:
                server.evict_idle_header_sessions()

        anyio.run(sweep_while_busy)
        assert "header:stale" not in server._sessions
        assert sorted(closed) == ["header:stale"]
        assert "header:fresh" in server._sessions
        assert "header:busy" in server._sessions  # a running call keeps its session
        assert "http:abc" in server._sessions  # ended by the SDK, not by this sweep
        assert server._LOCAL_KEY in server._sessions
    finally:
        server.set_header_session_idle_timeout(old)
        for key in ("header:fresh", "header:busy", "http:abc"):
            server._sessions.pop(key, None)


def test_stateless_protocol_clients_without_a_key_are_refused(open_berka_sphere: str) -> None:
    """A client on the stateless protocol sends no Mcp-Session-Id; without
    X-Hypertopos-Session two such clients would share one open sphere. Their
    first session request is refused with the header named."""
    import uvicorn
    from mcp.client.client import Client
    from mcp.shared.exceptions import MCPError

    from hypertopos_mcp.server import mcp

    outcome: dict[str, object] = {}

    async def main() -> None:
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"
            )
        )
        url = f"http://127.0.0.1:{port}/mcp"
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                await anyio.sleep(0.05)
            try:
                async with Client(url) as client:
                    outcome["auto_protocol"] = client.protocol_version
                    stats = await client.call_tool("get_session_stats", {})
                    outcome["auto_key"] = json.loads(stats.content[0].text)["session_key"]
            except* MCPError as group:
                outcome["auto_error"] = " ".join(str(e) for e in _leaves(group))
            try:
                async with Client(url, mode="2026-07-28") as client:
                    await client.call_tool("get_session_stats", {})
            except* MCPError as group:
                outcome["pinned_error"] = " ".join(str(e) for e in _leaves(group))
            server.should_exit = True

    anyio.run(main)

    # Never a shared session: both are refused with the header named.
    assert "auto_key" not in outcome
    assert "X-Hypertopos-Session" in str(outcome["auto_error"])
    assert "X-Hypertopos-Session" in str(outcome.get("pinned_error"))


def test_stateless_protocol_in_memory_client_uses_the_local_session(
    open_berka_sphere: str,
) -> None:
    """stdio-like duplex clients on the stateless protocol keep the process's session."""
    from mcp.client.client import Client

    from hypertopos_mcp.server import mcp

    seen: dict[str, object] = {}

    async def main() -> None:
        async with Client(mcp) as client:
            seen["protocol"] = client.protocol_version
            stats = await client.call_tool("get_session_stats", {})
            seen["key"] = json.loads(stats.content[0].text)["session_key"]

    anyio.run(main)

    assert seen["protocol"] == "2026-07-28"
    assert seen["key"] == "local"
