"""Tool profiles: what each server configuration lists, in which order, and what it refuses."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import hypertopos_mcp.main  # noqa: F401  (registers every tool)
from hypertopos_mcp import server
from hypertopos_mcp.server import (
    _PROFILES,
    _TOOL_TIERS,
    mcp,
    profile_names,
    select_profile,
    selected_profile,
    visible_tools,
)

# Owner decision 2026-11-01: the developer / data-inspection surface is permanent.
_INSPECTOR_MANDATED = {
    "goto",
    "walk_line",
    "jump_polygon",
    "emerge",
    "get_position",
    "get_polygon",
    "get_event_polygons",
    "search_entities",
    "get_line_schema",
    "get_line_profile",
    "aggregate",
}


@pytest.fixture(autouse=True)
def _restore_profile():
    saved = server._selected
    yield
    server._selected = saved


def _registered() -> dict:
    return {t.name: t for t in mcp._tool_manager.list_tools()}


def test_every_profile_tool_is_registered() -> None:
    registered = _registered()
    for name, tools in _PROFILES.items():
        assert set(tools) <= set(registered), name
        assert len(tools) == len(set(tools)), f"{name} repeats a tool"


def test_every_registered_tool_has_a_tier() -> None:
    assert set(_registered()) == set(_TOOL_TIERS)


def test_profile_sizes_are_pinned() -> None:
    assert {n: len(t) for n, t in _PROFILES.items()} == {
        "minimal": 5,
        "investigator": 34,
        "inspector": 23,
    }


def test_inspector_keeps_the_mandated_navigation_surface() -> None:
    assert set(_PROFILES["inspector"]) >= _INSPECTOR_MANDATED


def test_default_profile_is_investigator() -> None:
    assert select_profile(None) == _PROFILES["investigator"]
    assert select_profile("") == _PROFILES["investigator"]


def test_union_keeps_order_and_dedupes() -> None:
    sel = select_profile("investigator,inspector")
    assert sel[:4] == ("open_sphere", "close_sphere", "get_session_stats", "sphere_overview")
    assert sel[: len(_PROFILES["investigator"])] == _PROFILES["investigator"]
    assert (
        len(sel)
        == len(set(sel))
        == len(set(_PROFILES["investigator"]) | set(_PROFILES["inspector"]))
    )


def test_full_is_every_registered_tool() -> None:
    assert set(select_profile("full")) == set(_TOOL_TIERS)


def test_unknown_profile_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown tool profile"):
        select_profile("power")
    assert "full" in profile_names()


def test_whole_profile_visible_before_open_sphere(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    select_profile("investigator")
    monkeypatch.setattr(server, "sphere_capabilities", lambda: None)
    assert visible_tools() == set(selected_profile())


def test_capabilities_hide_tiers_after_open_sphere(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    select_profile("inspector")
    caps = {"has_temporal": False, "multi_pattern": False, "has_trajectory_index": False}
    monkeypatch.setattr(server, "sphere_capabilities", lambda: caps)
    shown = visible_tools()
    assert "dive_solid" not in shown  # temporal tier
    assert "goto" in shown
    caps["has_temporal"] = True
    assert "dive_solid" in visible_tools()


def test_investigator_schema_budget() -> None:
    by_name = _registered()
    sizes = {
        n: len(json.dumps(by_name[n].parameters, separators=(",", ":")))
        + len(by_name[n].description or "")
        for n in _PROFILES["investigator"]
    }
    assert max(sizes.values()) <= 2500, {n: s for n, s in sizes.items() if s > 2500}
    assert sum(sizes.values()) <= 34_000, sum(sizes.values())


def test_main_selects_the_profile_and_refuses_an_unknown_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from hypertopos_mcp import main

    monkeypatch.setattr(main.mcp, "run", lambda **kw: None)
    main.main(["--profile", "inspector,minimal"])
    assert selected_profile()[: len(_PROFILES["inspector"])] == _PROFILES["inspector"]
    with pytest.raises(SystemExit):
        main.main(["--profile", "power"])


def test_profile_env_variable_is_the_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from hypertopos_mcp import main

    monkeypatch.setenv("HYPERTOPOS_MCP_PROFILE", "minimal")
    monkeypatch.setattr(main.mcp, "run", lambda **kw: None)
    main.main([])
    assert selected_profile() == _PROFILES["minimal"]


def _runtime_strings(func: ast.AST) -> list[str]:
    """String constants a tool can return: docstrings excluded, f-string parts joined."""
    doc = ast.get_docstring(func, clean=False)  # type: ignore[arg-type]
    out: list[str] = []
    seen: set[int] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.JoinedStr):
            parts = [v for v in node.values if isinstance(v, ast.Constant)]
            seen.update(id(v) for v in parts)
            out.append("".join(str(v.value) for v in parts))
    for node in ast.walk(func):
        is_text = isinstance(node, ast.Constant) and isinstance(node.value, str)
        if is_text and id(node) not in seen and node.value != doc:  # type: ignore[attr-defined]
            out.append(node.value)  # type: ignore[attr-defined]
    return out


def _json_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _json_strings(v)]
    return []


def _untagged(text: str, outside: set[str]) -> list[str]:
    """Tools outside the default profile that ``text`` names without their profile.

    The tag must follow the name (or its call) directly; a one-word name is also
    an English word, so it counts only as a call.
    """
    found = []
    for name in outside:
        pattern = rf"\b{name}\b(\([^)]*\))?" if "_" in name else rf"\b{name}(\([^)]*\))"
        for hit in re.finditer(pattern, text):
            tag = re.match(r"\s*\((\w+) profile\)", text[hit.end() :])
            if tag is None or (
                tag.group(1) != "full" and name not in _PROFILES.get(tag.group(1), ())
            ):
                found.append(name)
    return found


def test_default_profile_advice_names_the_profile_of_tools_outside_it() -> None:
    """Advice returned by a default-profile tool must not send the agent to a refused tool."""
    default = set(_PROFILES[server._DEFAULT_PROFILE])
    outside = set(_registered()) - default
    untagged: list[str] = []
    tools_dir = Path(server.__file__).parent / "tools"
    for path in sorted(tools_dir.glob("*.py")):
        for func in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) and func.name in default:
                # a bare tool name is a step identifier, not advice
                for text in (t for t in _runtime_strings(func) if " " in t):
                    untagged += [
                        f"{path.name}:{func.name} -> {n}" for n in _untagged(text, outside)
                    ]
    # Text built below the tool layer (navigator messages) shows up in the goldens.
    golden_dir = Path(__file__).parent / "golden"
    for path in sorted(golden_dir.glob("*.json")):
        if any(path.stem.startswith(tool) for tool in default):
            for text in _json_strings(json.loads(path.read_text(encoding="utf-8"))):
                untagged += [f"{path.name} -> {n}" for n in _untagged(text, outside)]
    assert not untagged, sorted(set(untagged))


def test_name_profiles_tags_calls_outside_the_default_profile() -> None:
    tagged = server.name_profiles("Call recalibrate('p') to refit; then find_anomalies('p').")
    assert tagged == "Call recalibrate('p') (full profile) to refit; then find_anomalies('p')."
    assert server.name_profiles(tagged) == tagged
    assert "aggregate(x) (inspector profile)" in server.name_profiles("Run aggregate(x) twice")
    across_fields = '{"a": "aggregate(", "b": "data)"}'
    assert server.name_profiles(across_fields) == across_fields
