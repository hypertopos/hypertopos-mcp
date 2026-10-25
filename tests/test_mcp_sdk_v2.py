# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Server behaviour that depends on the MCP Python SDK 2.x runtime."""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import pytest


def test_tool_calls_never_overlap() -> None:
    from hypertopos_mcp.server import _serialize_tool_calls

    running = 0
    peak = 0

    async def handler(_ctx: object) -> dict:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await anyio.sleep(0.01)
        running -= 1
        return {}

    async def main() -> None:
        ctx = SimpleNamespace(method="tools/call")
        async with anyio.create_task_group() as tg:
            for _ in range(5):
                tg.start_soon(_serialize_tool_calls, ctx, handler)

    anyio.run(main)
    assert peak == 1


def test_other_requests_are_not_serialized() -> None:
    from hypertopos_mcp.server import _serialize_tool_calls

    peak = 0
    running = 0

    async def handler(_ctx: object) -> dict:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await anyio.sleep(0.01)
        running -= 1
        return {}

    async def main() -> None:
        ctx = SimpleNamespace(method="tools/list")
        async with anyio.create_task_group() as tg:
            for _ in range(3):
                tg.start_soon(_serialize_tool_calls, ctx, handler)

    anyio.run(main)
    assert peak == 3


def test_tools_changed_is_sent_from_a_worker_thread() -> None:
    from hypertopos_mcp.server import _notify_tools_changed

    sent: list[str] = []

    async def send_tool_list_changed() -> None:
        sent.append("tools/list_changed")

    async def notify_tools_changed() -> None:
        pass

    ctx = SimpleNamespace(
        session=SimpleNamespace(send_tool_list_changed=send_tool_list_changed),
        notify_tools_changed=notify_tools_changed,
    )

    async def main() -> None:
        await anyio.to_thread.run_sync(_notify_tools_changed, ctx)

    anyio.run(main)
    assert sent == ["tools/list_changed"]


@pytest.mark.parametrize("ctx", [None])
def test_tools_changed_without_a_request_is_a_no_op(ctx: object) -> None:
    from hypertopos_mcp.server import _notify_tools_changed

    _notify_tools_changed(ctx)  # must not raise


def test_tool_errors_reach_the_model_with_their_text() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from hypertopos_mcp.server import timed

    @timed
    def failing() -> str:
        raise ValueError("pattern 'x' not found; call sphere_overview() first")

    with pytest.raises(ToolError, match="pattern 'x' not found"):
        failing()


def test_async_tool_errors_reach_the_model_with_their_text() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from hypertopos_mcp.server import timed

    @timed
    async def failing() -> str:
        raise RuntimeError("No sphere open")

    with pytest.raises(ToolError, match="No sphere open"):
        anyio.run(failing)


def test_tools_do_not_send_a_second_structured_copy() -> None:
    import hypertopos_mcp.main  # noqa: F401
    from hypertopos_mcp.server import mcp

    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    with_schema = [name for name, t in tools.items() if t.fn_metadata.output_schema is not None]
    assert with_schema == []


def test_tools_changed_is_published_on_both_protocol_channels() -> None:
    from hypertopos_mcp.server import _notify_tools_changed

    sent: list[str] = []

    async def send_tool_list_changed() -> None:
        sent.append("session")

    async def notify_tools_changed() -> None:
        sent.append("subscriptions")

    ctx = SimpleNamespace(
        session=SimpleNamespace(send_tool_list_changed=send_tool_list_changed),
        notify_tools_changed=notify_tools_changed,
    )

    async def main() -> None:
        await anyio.to_thread.run_sync(_notify_tools_changed, ctx)

    anyio.run(main)
    assert sorted(sent) == ["session", "subscriptions"]


@pytest.mark.parametrize("method", ["tools/call", "resources/read", "prompts/get"])
def test_state_reading_requests_are_serialized(method: str) -> None:
    from hypertopos_mcp.server import _serialize_tool_calls

    running = peak = 0

    async def handler(_ctx: object) -> dict:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await anyio.sleep(0.01)
        running -= 1
        return {}

    async def main() -> None:
        ctx = SimpleNamespace(method=method)
        async with anyio.create_task_group() as tg:
            for _ in range(4):
                tg.start_soon(_serialize_tool_calls, ctx, handler)

    anyio.run(main)
    assert peak == 1


def test_writing_branch_tools_are_marked_destructive() -> None:
    import hypertopos_mcp.main  # noqa: F401
    from hypertopos_mcp.server import mcp

    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    for name in ("manage_branch", "recalibrate"):
        annotations = tools[name].annotations
        assert annotations is not None and annotations.destructive_hint is True, name


def test_sampling_reads_the_negotiated_client_capabilities() -> None:
    from mcp.server.mcpserver import Context

    from hypertopos_mcp import server

    calls: list[str] = []

    class FakeSession:
        client_params = None  # absent on 2026-era requests
        client_capabilities = SimpleNamespace(sampling=object())

        async def create_message(self, **_kwargs: object) -> object:
            calls.append("sampled")
            return SimpleNamespace(content=SimpleNamespace(text="ok"))

    class Probe(Context):
        @property
        def session(self):  # type: ignore[override]
            return FakeSession()

    probe = Probe.__new__(Probe)
    result = anyio.run(lambda: server._sample_llm(probe, "q"))
    assert result == "ok" and calls == ["sampled"]


def test_tool_errors_keep_their_original_type_for_direct_callers() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from hypertopos_mcp.server import timed

    @timed
    def failing() -> str:
        raise KeyError("no such line")

    with pytest.raises(KeyError) as info:
        failing()
    assert isinstance(info.value, ToolError)
    assert "no such line" in str(info.value)
