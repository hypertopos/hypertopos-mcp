# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""MCP surface of sphere branches: manage_branch, compare_branches, branch-aware tools."""

from __future__ import annotations

import json
from pathlib import Path

import lance
import pytest


def _anchor_pattern(state) -> str:
    sphere = state["sphere"]._sphere
    return next(pid for pid, p in sphere.patterns.items() if p.pattern_type == "anchor")


def _keys(path: str, pid: str, n: int) -> list[str]:
    geo = Path(path) / "geometry" / pid / "data.lance"
    table = lance.dataset(str(geo)).to_table(columns=["primary_key", "delta_norm"])
    order = table["delta_norm"].to_numpy().argsort()[::-1][:n]
    return [table["primary_key"][int(i)].as_py() for i in order]


def _main_versions(path: str) -> dict[str, int]:
    from hypertopos.storage._lance_branches import dataset_inventory

    return {k: lance.dataset(str(p)).latest_version for k, p in dataset_inventory(path).items()}


def test_branch_workflow_leaves_main_untouched(writable_berka_sphere):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import analysis, session

    path = writable_berka_sphere
    opened = json.loads(session.open_sphere(path))
    assert opened["ref"] == "main"
    pid = _anchor_pattern(_state)
    before = _main_versions(path)

    result = json.loads(
        session.recalibrate(pid, branch="what-if", exclude_keys=_keys(path, pid, 3))
    )
    assert result["branch"] == "what-if" and result["n_excluded"] == 3
    assert _main_versions(path) == before

    listed = json.loads(analysis.manage_branch("list"))["branches"]
    assert [b["name"] for b in listed] == ["what-if"]
    assert listed[0]["n_recipes"] == 1

    report = json.loads(analysis.compare_branches(pid, "what-if"))
    assert report["to_ref"] == "what-if" and report["from_ref"] == "main"
    assert report["calibration_drift"]["overall_drift_rms"] > 0.0
    assert [r["op"] for r in report["recipe"]] == ["recalibrate"]

    on_branch = json.loads(session.open_sphere(path, branch="what-if"))
    assert on_branch["ref"] == "what-if"
    assert _state["session"]._reader.branch == "what-if"
    with pytest.raises(ValueError, match="open main"):
        analysis.manage_branch("delete", name="what-if")

    session.open_sphere(path)
    assert json.loads(analysis.manage_branch("delete", name="what-if"))["deleted"] == "what-if"
    assert json.loads(analysis.manage_branch("list"))["branches"] == []
    assert _main_versions(path) == before


def test_manage_branch_create_and_promote(writable_berka_sphere):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import analysis, session

    path = writable_berka_sphere
    session.open_sphere(path)
    pid = _anchor_pattern(_state)
    created = json.loads(analysis.manage_branch("create", name="b1", purpose="test"))
    assert created["created"]["name"] == "b1" and created["created"]["purpose"] == "test"

    branch_result = json.loads(
        session.recalibrate(pid, branch="b1", exclude_keys=_keys(path, pid, 2))
    )
    promoted = json.loads(analysis.manage_branch("promote", name="b1"))["promoted"]
    assert promoted["name"] == "b1" and promoted["recipes_replayed"] == 1
    assert json.loads(analysis.manage_branch("list"))["branches"] == []
    assert _state["sphere"]._sphere.patterns[pid].theta_norm == pytest.approx(
        branch_result["new_theta_norm"], abs=1e-4
    )


def test_manage_branch_requires_a_name(writable_berka_sphere):
    from hypertopos_mcp.tools import analysis, session

    session.open_sphere(writable_berka_sphere)
    with pytest.raises(ValueError, match="needs name"):
        analysis.manage_branch("create")


def test_compare_branches_output_is_strict_json(writable_berka_sphere):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import analysis, session

    path = writable_berka_sphere
    session.open_sphere(path)
    pid = _anchor_pattern(_state)
    analysis.manage_branch("create", name="same")
    body = analysis.compare_branches(pid, "same")
    json.loads(body, parse_constant=lambda c: pytest.fail(f"non-strict JSON constant {c}"))


def test_compare_branches_compact_truncates_long_recipe_lists(writable_berka_sphere):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools import analysis, session

    path = writable_berka_sphere
    session.open_sphere(path)
    pid = _anchor_pattern(_state)
    session.recalibrate(pid, branch="wide", exclude_keys=_keys(path, pid, 15))

    compact = json.loads(analysis.compare_branches(pid, "wide"))
    assert len(compact["recipe"][0]["exclude_keys"]) == 10
    assert compact["omitted"]["recipe"] == {"1.exclude_keys": 15}

    session.open_sphere(path, response_format="full")
    full = json.loads(analysis.compare_branches(pid, "wide"))
    assert len(full["recipe"][0]["exclude_keys"]) == 15 and "omitted" not in full
    session.open_sphere(path)


def test_promote_refuses_the_branch_this_session_is_on(writable_berka_sphere):
    from hypertopos_mcp.tools import analysis, session

    path = writable_berka_sphere
    session.open_sphere(path)
    analysis.manage_branch("create", name="b2")
    session.open_sphere(path, branch="b2")
    with pytest.raises(ValueError, match="open main"):
        analysis.manage_branch("promote", name="b2")
    session.open_sphere(path)
