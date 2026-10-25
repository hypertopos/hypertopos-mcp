"""open_sphere(force_reload=True) must serve the reloaded tool bodies."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from hypertopos_mcp.server import mcp

_MODULE = "_hypertopos_reload_probe"
_TOOL = "reload_probe_tool"

_SOURCE = """
import json

from hypertopos_mcp.server import mcp, timed


@mcp.tool()
@timed
def reload_probe_tool(top_n: int = 1) -> str:
    return json.dumps({{"version": {version}}})
"""


@pytest.fixture
def probe_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    path = tmp_path / f"{_MODULE}.py"
    path.write_text(_SOURCE.format(version=1), encoding="utf-8")
    importlib.invalidate_caches()
    module = importlib.import_module(_MODULE)
    yield path, module
    mcp._tool_manager._tools.pop(_TOOL, None)
    sys.modules.pop(_MODULE, None)


def _served(**kwargs: object) -> dict:
    tool = mcp._tool_manager.get_tool(_TOOL)
    assert tool is not None
    return json.loads(tool.fn(**kwargs))


def test_reloaded_module_replaces_the_served_tool_body(probe_module) -> None:
    path, module = probe_module
    assert _served()["version"] == 1

    path.write_text(_SOURCE.format(version=22), encoding="utf-8")
    importlib.reload(module)

    assert _served()["version"] == 22


def test_reloaded_tool_keeps_title_stripping_and_unknown_arg_check(probe_module) -> None:
    path, module = probe_module
    path.write_text(_SOURCE.format(version=22), encoding="utf-8")
    importlib.reload(module)

    tool = mcp._tool_manager.get_tool(_TOOL)
    assert "title" not in tool.parameters
    assert all("title" not in p for p in tool.parameters["properties"].values())
    with pytest.raises(ValueError, match="did you mean 'top_n'"):
        tool.fn_metadata.arg_model.model_validate({"top_m": 2})


def test_same_name_from_another_module_does_not_replace(probe_module) -> None:
    def reload_probe_tool() -> str:
        return json.dumps({"version": -1})

    mcp.tool()(reload_probe_tool)

    assert _served()["version"] == 1
