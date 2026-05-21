# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Tool-count consistency — documented counts in user-facing files must match runtime.

Locks every `\\d+ tools` / phase-count string in `packages/hypertopos-mcp/` against
the runtime registry (`_TOOL_TIERS` + `mcp._tool_manager._tools`). Drift in any
documented count fails this test with a message naming the file, the documented
value, the expected runtime value, and the runtime expression that derives the
expectation.

Coverage:
* `docs/mcp-spec.md` — phase counts, per-tier counts, token-cost table
* `docs/images/mcp-lifecycle.svg` — phase boxes + manual-mode total
* `docs/tools.md`, `README.md`, `src/hypertopos_mcp/__init__.py` — defensive
  guard. These files have no count strings today; any future `\\d+ tools`
  introduced there must match runtime.

Runtime canonical source: `_TOOL_TIERS` in `hypertopos_mcp.server`. Equivalent
to `len(mcp._tool_manager._tools)` after all tool modules import. We use
`_TOOL_TIERS` because it is the registry that the lifecycle helpers
(`_unregister_phase2_tools`, `_register_manual_tools`) iterate.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

# Import tool modules to register them with the FastMCP instance, mirroring main.py.
import hypertopos_mcp.tools.aggregation  # noqa: F401
import hypertopos_mcp.tools.analysis  # noqa: F401
import hypertopos_mcp.tools.detection  # noqa: F401
import hypertopos_mcp.tools.geometry  # noqa: F401
import hypertopos_mcp.tools.navigation  # noqa: F401
import hypertopos_mcp.tools.observability  # noqa: F401
import hypertopos_mcp.tools.session  # noqa: F401
import hypertopos_mcp.tools.smart  # noqa: F401
from hypertopos_mcp.server import _TOOL_TIERS, mcp

_PKG_ROOT = Path(__file__).resolve().parent.parent


def _tier_counts() -> dict[str, int]:
    return dict(collections.Counter(_TOOL_TIERS.values()))


def _runtime_counts() -> dict[str, int]:
    """Named expressions every documented count must match."""
    c = _tier_counts()
    always = c.get("always", 0)
    gateway = c.get("gateway", 0)
    edge = c.get("edge", 0)
    base = c.get("base", 0)
    phase2_total = always + gateway + edge
    phase3_min = phase2_total + base  # no optional capabilities
    phase3_max = sum(c.values())  # all capabilities present
    return {
        "always": always,
        "gateway": gateway,
        "edge": edge,
        "base": base,
        "phase2_total": phase2_total,
        "phase3_min": phase3_min,
        "phase3_max": phase3_max,
        "total": sum(c.values()),
    }


# (file_relpath, regex, expected_key_or_tuple) — every entry is locked.
# When a regex captures one group, expected is a single key; when it captures two
# (range like "86-101"), expected is a (min_key, max_key) tuple.
_EXPECTATIONS: list[tuple[str, str, object]] = [
    # docs/mcp-spec.md — phase intros
    ("docs/mcp-spec.md", r"Only (\d+) tools are visible", "always"),
    ("docs/mcp-spec.md", r"tier: `edge`, (\d+) tools", "edge"),
    ("docs/mcp-spec.md",
     r"\| \*\*base\*\* \| Always after sphere_overview \| (\d+) tools",
     "base"),
    # docs/mcp-spec.md — lifecycle ASCII diagram
    ("docs/mcp-spec.md", r"Server start → Phase 1 \((\d+) tools", "always"),
    ("docs/mcp-spec.md",
     r"open_sphere\(path\) → Phase 2 \((\d+) tools",
     "phase2_total"),
    ("docs/mcp-spec.md",
     r"sphere_overview\(\)\s+→ Phase 3 \((\d+)-(\d+) tools",
     ("phase3_min", "phase3_max")),
    ("docs/mcp-spec.md",
     r"close_sphere\(\) → Phase 1 \((\d+) tools",
     "always"),
    ("docs/mcp-spec.md",
     r"open_sphere\(other_path\) → Phase 2 \((\d+) tools",
     "phase2_total"),
    # docs/mcp-spec.md — Token Cost per Phase table
    ("docs/mcp-spec.md", r"Phase 1 — before open_sphere \| (\d+) ", "always"),
    ("docs/mcp-spec.md",
     r"Phase 2 — after open_sphere \| (\d+) ",
     "phase2_total"),
    ("docs/mcp-spec.md",
     r"Phase 3 — full sphere \(all capabilities\) \| ~(\d+) ",
     "phase3_max"),
    ("docs/mcp-spec.md",
     r"Phase 3 — simple sphere \(base only\) \| ~(\d+) ",
     "phase3_min"),
    ("docs/mcp-spec.md",
     r"all (\d+) tool schemas would be in context",
     "phase3_max"),
    # docs/mcp-spec.md — readOnlyHint annotation count is the base-tier count
    ("docs/mcp-spec.md",
     r"`readOnlyHint=True` on (\d+) read-only tools",
     "base"),
    # docs/images/mcp-lifecycle.svg — phase boxes
    ("docs/images/mcp-lifecycle.svg",
     r'<text [^>]*>(\d+) tools</text>\s*<text [^>]*>open_sphere</text>',
     "always"),
    ("docs/images/mcp-lifecycle.svg",
     r'<text [^>]*>(\d+) tools</text>\s*<text [^>]*>detect_pattern \(smart mode\)',
     "phase2_total"),
    ("docs/images/mcp-lifecycle.svg",
     r'<text [^>]*>(\d+)[–-](\d+) tools</text>\s*<text [^>]*>navigation',
     ("phase3_min", "phase3_max")),
    ("docs/images/mcp-lifecycle.svg",
     r'<text [^>]*>(\d+) tools · agent-driven exploration</text>',
     "phase3_max"),
]


def _read(rel: str) -> str:
    return (_PKG_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_runtime_registry_matches_tier_map() -> None:
    """`_TOOL_TIERS` must enumerate every registered tool."""
    # Restore everything so all tools live in _tool_manager._tools, then revert
    # to Phase 1 to keep the state consistent with what other tests expect.
    from hypertopos_mcp.server import (
        _restore_tool,
        _tool_stash,
        _unregister_phase2_tools,
    )

    for name in list(_tool_stash.keys()):
        _restore_tool(name)

    try:
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        tiered = set(_TOOL_TIERS.keys())
        assert registered == tiered, (
            f"Runtime tool registry != _TOOL_TIERS: "
            f"only_in_runtime={registered - tiered}, "
            f"only_in_tiers={tiered - registered}"
        )
        assert len(_TOOL_TIERS) == len(mcp._tool_manager._tools)
    finally:
        _unregister_phase2_tools()


def test_documented_counts_match_runtime() -> None:
    """Every documented tool-count string under packages/hypertopos-mcp/ matches runtime."""
    rc = _runtime_counts()
    failures: list[str] = []

    for rel, pattern, expected in _EXPECTATIONS:
        text = _read(rel)
        match = re.search(pattern, text)
        if match is None:
            failures.append(
                f"{rel}: pattern not found, lock obsolete or content removed — "
                f"pattern={pattern!r}"
            )
            continue
        if isinstance(expected, tuple):
            lo_key, hi_key = expected
            doc_lo, doc_hi = int(match.group(1)), int(match.group(2))
            exp_lo, exp_hi = rc[lo_key], rc[hi_key]
            if (doc_lo, doc_hi) != (exp_lo, exp_hi):
                failures.append(
                    f"{rel}: documented range {doc_lo}-{doc_hi} != "
                    f"runtime {exp_lo}-{exp_hi} ({lo_key}-{hi_key}) — "
                    f"matched text: {match.group(0)!r}"
                )
        else:
            doc_n = int(match.group(1))
            exp_n = rc[expected]
            if doc_n != exp_n:
                failures.append(
                    f"{rel}: documented {doc_n} != runtime {exp_n} ({expected}) — "
                    f"matched text: {match.group(0)!r}"
                )

    assert not failures, (
        "Documented tool counts drifted from runtime "
        f"(_TOOL_TIERS = {sum(_tier_counts().values())} total):\n  - "
        + "\n  - ".join(failures)
    )


def test_no_unlocked_tool_counts_in_user_facing_files() -> None:
    """Files explicitly listed in the M2.4 plan must not carry unlocked \\d+ tools strings.

    `tools.md`, `README.md`, and `__init__.py` currently have no tool-count strings.
    If a future edit introduces a `\\d+ tools` phrase in these files, this test
    fails and the author must add a lock to `_EXPECTATIONS`.
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
        for m in pattern.finditer(text):
            # Skip prose like "4-6 tool workflow" — those are not registry counts.
            # Heuristic: registry-count phrases like "23 tools" are immediately
            # preceded by whitespace and followed by punctuation or end of line.
            # Loose narrative ("a 4-6 tool workflow") gets a pass.
            start = max(0, m.start() - 20)
            context = text[start:m.end() + 20]
            if "workflow" in context.lower() or "-" in m.group(0):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            rogue.append(f"{rel}:{line_no}: {context.strip()!r}")
    assert not rogue, (
        "User-facing files now contain unlocked tool-count strings — "
        "add a lock to _EXPECTATIONS in this test:\n  - " + "\n  - ".join(rogue)
    )
