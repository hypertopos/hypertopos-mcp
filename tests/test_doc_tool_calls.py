# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tool-call examples in the docs and skills use parameters the tools declare.

Tools reject unknown parameters, so an example with a wrong keyword teaches an
agent a call that fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401

_PACKAGES = Path(__file__).resolve().parents[2]
_KEYWORD = re.compile(r"(?<![\w.\"'])([a-z_][a-z0-9_]*)\s*=(?!=)")
_NESTED = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]")


def _doc_files() -> list[Path]:
    mcp_docs = [
        _PACKAGES / "hypertopos-mcp" / "README.md",
        *(_PACKAGES / "hypertopos-mcp" / "docs").glob("*.md"),
    ]
    skills = _PACKAGES / "hypertopos-skills"
    skill_docs = [*skills.glob("*/SKILL.md"), *skills.glob("*/references/*.md")]
    return sorted(set(mcp_docs + skill_docs))


def _mismatches() -> list[str]:
    from hypertopos_mcp.server import mcp

    params = {
        t.name: {f.alias or n for n, f in t.fn_metadata.arg_model.model_fields.items()}
        for t in mcp._tool_manager.list_tools()
    }
    call = re.compile(r"(?<![\w.])(" + "|".join(sorted(params, key=len, reverse=True)) + r")\(")
    found = []
    for path in _doc_files():
        text = path.read_text(encoding="utf-8")
        for m in call.finditer(text):
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            for keyword in _KEYWORD.findall(_NESTED.sub("", text[m.end() : i - 1])):
                if keyword not in params[m.group(1)]:
                    line = text.count("\n", 0, m.start()) + 1
                    found.append(f"{path.relative_to(_PACKAGES)}:{line}: {m.group(1)}({keyword}=)")
    return found


def test_doc_examples_use_declared_tool_parameters() -> None:
    assert _mismatches() == []


_SECTION = re.compile(r"^### `([a-z_]+)`\s*$")
_ROW = re.compile(r"^\|\s*`([a-z_][a-z0-9_]*)`\s*\|")


def _table_mismatches() -> list[str]:
    """Rows of each ``### `tool``` section's Parameter table that the tool does not declare."""
    from hypertopos_mcp.server import mcp

    params = {
        t.name: {f.alias or n for n, f in t.fn_metadata.arg_model.model_fields.items()}
        for t in mcp._tool_manager.list_tools()
    }
    found = []
    tool: str | None = None
    in_params = False
    path = _PACKAGES / "hypertopos-mcp" / "docs" / "tools.md"
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith(("## ", "### ")):
            m = _SECTION.match(line)
            tool = m.group(1) if m and m.group(1) in params else None
            in_params = False
            continue
        if tool is None:
            continue
        if line.startswith("|") and "Parameter" in line.split("|")[1]:
            in_params = True
            continue
        if in_params and not line.startswith("|"):
            in_params = False
        if in_params:
            row = _ROW.match(line)
            if row and row.group(1) not in params[tool]:
                found.append(f"tools.md:{no}: {tool}: `{row.group(1)}`")
    return found


def test_tools_md_parameter_tables_list_only_declared_parameters() -> None:
    assert _table_mismatches() == []


def test_every_tool_and_parameter_is_documented_in_tools_md() -> None:
    from hypertopos_mcp.server import mcp

    params = {
        t.name: {f.alias or n for n, f in t.fn_metadata.arg_model.model_fields.items()}
        for t in mcp._tool_manager.list_tools()
    }
    documented: dict[str, set[str]] = {}
    tool: str | None = None
    in_params = False
    path = _PACKAGES / "hypertopos-mcp" / "docs" / "tools.md"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("## ", "### ")):
            m = _SECTION.match(line)
            tool = m.group(1) if m and m.group(1) in params else None
            if tool is not None:
                documented.setdefault(tool, set())
            in_params = False
            continue
        if tool is None:
            continue
        if line.startswith("|") and "Parameter" in line.split("|")[1]:
            in_params = True
            continue
        if in_params and not line.startswith("|"):
            in_params = False
        if in_params and (row := _ROW.match(line)):
            documented[tool].add(row.group(1))
    gaps = {
        name: sorted(declared - documented.get(name, set()))
        for name, declared in params.items()
        if name not in documented or declared - documented[name]
    }
    assert gaps == {}
