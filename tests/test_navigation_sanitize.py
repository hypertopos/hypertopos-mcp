# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Navigation Point-serialization tools must sanitize non-finite property
floats to JSON null — strict MCP parsers reject the Infinity / NaN literals
json.dumps emits. get_polygon already sanitizes the same enriched entity
properties; the position tools (goto / get_position / walk_line /
jump_polygon / emerge) must match."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import hypertopos_mcp.tools.navigation as navmod
import pytest
from hypertopos_mcp.server import _state


@pytest.fixture
def fake_nav():
    saved_nav = _state.get("navigator")
    saved_sphere = _state.get("sphere")
    _state["navigator"] = MagicMock()
    _state["sphere"] = MagicMock()
    yield
    _state["navigator"] = saved_nav
    _state["sphere"] = saved_sphere


def test_get_position_sanitises_non_finite_property(fake_nav, monkeypatch):
    """A position whose serialized entity properties contain a NaN / inf float
    must serialize those as JSON null, not the bare NaN / Infinity literal."""
    monkeypatch.setattr(
        navmod,
        "_serialize_position",
        lambda pos: {
            "type": "Point",
            "primary_key": "E1",
            "properties": {"balance": float("nan"), "ratio": float("inf")},
        },
    )
    body = navmod.get_position()
    parsed = json.loads(body)  # would raise if Infinity / NaN leaked
    assert parsed["properties"]["balance"] is None
    assert parsed["properties"]["ratio"] is None
    assert "NaN" not in body
    assert "Infinity" not in body
