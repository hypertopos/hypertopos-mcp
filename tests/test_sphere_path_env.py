# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""HYPERTOPOS_SPHERE_PATH is the default path of open_sphere()."""

from __future__ import annotations

import json

import pytest


def test_open_sphere_without_path_uses_the_env_default(open_berka_sphere, monkeypatch) -> None:
    from hypertopos_mcp.server import session_scope
    from hypertopos_mcp.tools.session import close_sphere, open_sphere

    monkeypatch.setenv("HYPERTOPOS_SPHERE_PATH", open_berka_sphere)
    with session_scope("http:env-default"):
        out = json.loads(open_sphere())
        assert out["status"] == "open"
        assert out["path"] == open_berka_sphere
        close_sphere()


def test_open_sphere_without_path_or_env_says_what_to_do(monkeypatch) -> None:
    from hypertopos_mcp.server import session_scope
    from hypertopos_mcp.tools.session import open_sphere

    monkeypatch.delenv("HYPERTOPOS_SPHERE_PATH", raising=False)
    with session_scope("http:no-path"), pytest.raises(ValueError, match="HYPERTOPOS_SPHERE_PATH"):
        open_sphere()


def test_tools_do_not_open_the_env_sphere_behind_the_agents_back(
    open_berka_sphere, monkeypatch
) -> None:
    from hypertopos_mcp.server import _require_sphere, _state, session_scope

    monkeypatch.setenv("HYPERTOPOS_SPHERE_PATH", open_berka_sphere)
    with session_scope("http:no-auto-open"), pytest.raises(RuntimeError, match="open_sphere"):
        _require_sphere()
    with session_scope("http:no-auto-open"):
        assert _state["sphere"] is None
