# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Numpy scalars in tool responses are JSON numbers, not strings."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

_TOOLS = Path(__file__).resolve().parents[1] / "src" / "hypertopos_mcp" / "tools"


def test_json_default_turns_numpy_scalars_into_json_values() -> None:
    from hypertopos_mcp._format import json_default

    payload = {
        "f32": np.float32(0.5),
        "i64": np.int64(7),
        "flag": np.bool_(True),
        "nan32": np.float32("nan"),
        "when": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        "other": Path("a"),
    }
    out = json.loads(json.dumps(payload, default=json_default))
    assert out == {
        "f32": 0.5,
        "i64": 7,
        "flag": True,
        "nan32": None,
        "when": "2024-01-02T03:04:05+00:00",
        "other": "a",
    }


def test_json_default_stringifies_objects_that_answer_every_attribute() -> None:
    # A duck-typed isoformat check would return a fresh mock each call and
    # recurse until memory runs out.
    from hypertopos_mcp._format import json_default

    mock = MagicMock()
    assert isinstance(json_default(mock), str)
    assert isinstance(json.loads(json.dumps({"m": mock}, default=json_default))["m"], str)


def test_no_tool_serialises_with_default_str() -> None:
    offenders = []
    for path in sorted(_TOOLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "dumps":
                for kw in node.keywords:
                    if (
                        kw.arg == "default"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id == "str"
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
