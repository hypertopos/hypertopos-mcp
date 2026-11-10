# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tool-count consistency — documented counts in user-facing files must match runtime.

Locks every `\\d+ tools` / profile-count string in `packages/hypertopos-mcp/` against
the runtime registry (`_TOOL_TIERS`, `_PROFILES` and `mcp._tool_manager._tools`). Drift
in any documented count fails this test with a message naming the file, the documented
value, the expected runtime value, and the runtime expression that derives the
expectation.

Coverage:
* `docs/mcp-spec.md` — the profile table (count and tool list per profile), the union
  and single-pattern counts, the schema-size table, the read-only annotation count
* `docs/images/mcp-lifecycle.svg` — one box per profile + the combined-profile line
* `README.md` — the profile paragraph
* `docs/tools.md`, `README.md`, `src/hypertopos_mcp/__init__.py` — defensive guard:
  any `\\d+ tools` string there that no lock covers fails the test.

Runtime canonical sources: `_TOOL_TIERS` (every registered tool; the `full` profile is
all of them) and `_PROFILES` (the named profiles) in `hypertopos_mcp.server`.
"""

from __future__ import annotations

import re
from pathlib import Path

# Import tool modules to register them with the MCPServer instance, mirroring main.py.
import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401
from hypertopos_mcp.server import _PROFILES, _TOOL_TIERS, mcp

_PKG_ROOT = Path(__file__).resolve().parent.parent


def _runtime_counts() -> dict[str, int]:
    """Named expressions every documented count must match."""
    investigator = _PROFILES["investigator"]
    union = dict.fromkeys((*investigator, *_PROFILES["inspector"]))
    read_only = sum(
        1
        for t in mcp._tool_manager.list_tools()
        if t.annotations is not None and t.annotations.read_only_hint
    )
    return {
        "read_only": read_only,
        "minimal": len(_PROFILES["minimal"]),
        "investigator": len(investigator),
        "inspector": len(_PROFILES["inspector"]),
        "investigator_inspector": len(union),
        # a sphere with one pattern per entity line hides the multi_pattern tier
        "investigator_single_pattern": sum(
            1 for name in investigator if _TOOL_TIERS.get(name) != "multi_pattern"
        ),
        "total": len(_TOOL_TIERS),
    }


def _svg_box(name: str) -> str:
    """A profile box in the lifecycle SVG: the name text, then its count text."""
    return rf"<text [^>]*>{name}</text>\s*<text [^>]*>(\d+) tools</text>"


# (file_relpath, regex, expected_key) — every entry is locked; regexes run multiline.
_EXPECTATIONS: list[tuple[str, str, str]] = [
    # docs/mcp-spec.md — the profile table: | `name` | count | purpose | list |
    ("docs/mcp-spec.md", r"^\| `minimal` \| (\d+) \| [A-Z]", "minimal"),
    ("docs/mcp-spec.md", r"^\| `investigator` \| (\d+) \| [A-Z]", "investigator"),
    ("docs/mcp-spec.md", r"^\| `inspector` \| (\d+) \| [A-Z]", "inspector"),
    ("docs/mcp-spec.md", r"^\| `full` \| (\d+) \| [A-Z]", "total"),
    # docs/mcp-spec.md — prose counts
    (
        "docs/mcp-spec.md",
        r"`--profile investigator,inspector` lists (\d+) tools",
        "investigator_inspector",
    ),
    (
        "docs/mcp-spec.md",
        r"one pattern per entity line,\s+`investigator` lists (\d+) tools",
        "investigator_single_pattern",
    ),
    # docs/mcp-spec.md — schema size table: | `name` | count | chars | tokens |
    ("docs/mcp-spec.md", r"^\| `minimal` \| (\d+) \| [\d,]+ \|", "minimal"),
    ("docs/mcp-spec.md", r"^\| `investigator` \| (\d+) \| [\d,]+ \|", "investigator"),
    ("docs/mcp-spec.md", r"^\| `inspector` \| (\d+) \| [\d,]+ \|", "inspector"),
    (
        "docs/mcp-spec.md",
        r"^\| `investigator,inspector` \| (\d+) \| [\d,]+ \|",
        "investigator_inspector",
    ),
    ("docs/mcp-spec.md", r"^\| `full` \| (\d+) \| [\d,]+ \|", "total"),
    # docs/mcp-spec.md — readOnlyHint annotation count, read from the registered tools
    ("docs/mcp-spec.md", r"`read_only_hint=True` on (\d+) read-only tools", "read_only"),
    # docs/images/mcp-lifecycle.svg — one box per profile + the combined line
    ("docs/images/mcp-lifecycle.svg", _svg_box("minimal"), "minimal"),
    ("docs/images/mcp-lifecycle.svg", _svg_box("investigator"), "investigator"),
    ("docs/images/mcp-lifecycle.svg", _svg_box("inspector"), "inspector"),
    ("docs/images/mcp-lifecycle.svg", _svg_box("full"), "total"),
    (
        "docs/images/mcp-lifecycle.svg",
        r"investigator,inspector</text>\s*<text [^>]*>(\d+) tools, each once",
        "investigator_inspector",
    ),
    # README.md — the profile paragraph
    ("README.md", r"`investigator`, the default, holds (\d+) tools", "investigator"),
    ("README.md", r"`full` is all (\d+)\.", "total"),
    (
        "README.md",
        r"`--profile investigator,inspector` lists (\d+) tools",
        "investigator_inspector",
    ),
]


def _read(rel: str) -> str:
    return (_PKG_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_runtime_registry_matches_tier_map() -> None:
    """`_TOOL_TIERS` must enumerate every registered tool (it is what `full` lists)."""
    registered = {t.name for t in mcp._tool_manager.list_tools()}
    tiered = set(_TOOL_TIERS.keys())
    assert registered == tiered, (
        f"Runtime tool registry != _TOOL_TIERS: "
        f"only_in_runtime={registered - tiered}, "
        f"only_in_tiers={tiered - registered}"
    )
    assert len(_TOOL_TIERS) == len(mcp._tool_manager._tools)


def test_documented_counts_match_runtime() -> None:
    """Every documented tool-count string under packages/hypertopos-mcp/ matches runtime."""
    rc = _runtime_counts()
    failures: list[str] = []

    for rel, pattern, expected in _EXPECTATIONS:
        match = re.search(pattern, _read(rel), re.MULTILINE)
        if match is None:
            failures.append(
                f"{rel}: pattern not found, lock obsolete or content removed — pattern={pattern!r}"
            )
            continue
        doc_n = int(match.group(1))
        if doc_n != rc[expected]:
            failures.append(
                f"{rel}: documented {doc_n} != runtime {rc[expected]} ({expected}) — "
                f"matched text: {match.group(0)!r}"
            )

    assert not failures, (
        f"Documented tool counts drifted from runtime ({rc}):\n  - " + "\n  - ".join(failures)
    )


def test_mcp_spec_profile_table_lists_match_code() -> None:
    """Each named profile's row in mcp-spec.md lists `_PROFILES[name]`, in order."""
    text = _read("docs/mcp-spec.md")
    for name, tools in _PROFILES.items():
        row = re.search(rf"^\| `{name}` \| \d+ \| [A-Z][^|]*\| (.+) \|$", text, re.MULTILINE)
        assert row is not None, f"mcp-spec.md: no profile table row for {name!r}"
        assert re.findall(r"`([a-z_]+)`", row.group(1)) == list(tools), name
    full_row = re.search(r"^\| `full` \| \d+ \| [A-Z][^|]*\| (.+) \|$", text, re.MULTILINE)
    assert full_row is not None, "mcp-spec.md: no profile table row for 'full'"
    assert "`" not in full_row.group(1), "the full row names no tools (it is every tool)"


def test_total_tool_counts_in_library_and_skill_docs_match_runtime() -> None:
    """A whole-surface count ("N tools", N >= 50) in the library docs or skills is the total.

    Released CHANGELOG sections and benchmark reports are historical records and
    are not checked.
    """
    total = _runtime_counts()["total"]
    packages = _PKG_ROOT.parent
    pattern = re.compile(r"(?<!\d)(\d{2,3})\s+(?:MCP\s+)?tools\b")
    stale: list[str] = []
    for root in ("hypertopos-py", "hypertopos-skills"):
        for path in sorted((packages / root).rglob("*.md")):
            if path.name == "CHANGELOG.md" or "benchmark" in path.parts:
                continue
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in pattern.finditer(line):
                    if int(m.group(1)) >= 50 and int(m.group(1)) != total:
                        stale.append(f"{path.relative_to(packages)}:{no}: {m.group(0)!r}")
    assert not stale, f"total tool count is {total}:\n  - " + "\n  - ".join(stale)


def test_no_unlocked_tool_counts_in_user_facing_files() -> None:
    """`tools.md`, `README.md` and `__init__.py` carry no tool-count string without a lock.

    A `\\d+ tools` phrase in these files must be covered by an `_EXPECTATIONS` entry;
    a new one fails this test until the author adds the lock.
    """
    candidates = [
        "docs/tools.md",
        "README.md",
        "src/hypertopos_mcp/__init__.py",
    ]
    pattern = re.compile(r"\b(\d+)\s+(?:MCP\s+)?tools?\b", re.IGNORECASE)
    rogue: list[str] = []
    for rel in candidates:
        text = _read(rel)
        locked = [
            m.span(1)
            for r, lock, _ in _EXPECTATIONS
            if r == rel
            for m in re.finditer(lock, text, re.MULTILINE)
        ]
        for m in pattern.finditer(text):
            if any(lo <= m.start(1) < hi for lo, hi in locked):
                continue
            # Skip prose like "4-6 tool workflow" — those are not registry counts.
            start = max(0, m.start() - 20)
            context = text[start : m.end() + 20]
            if "workflow" in context.lower() or "-" in m.group(0):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            rogue.append(f"{rel}:{line_no}: {context.strip()!r}")
    assert not rogue, (
        "User-facing files now contain unlocked tool-count strings — "
        "add a lock to _EXPECTATIONS in this test:\n  - " + "\n  - ".join(rogue)
    )
