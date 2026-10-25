# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Every tool response is strict JSON: non-finite floats become null."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest


def _strict_loads(text: str) -> object:
    def reject(literal: str) -> object:
        raise ValueError(f"non-strict JSON literal {literal}")

    return json.loads(text, parse_constant=reject)


def test_timed_response_turns_non_finite_floats_into_null() -> None:
    from hypertopos_mcp.server import timed

    @timed
    def tool() -> str:
        return json.dumps({"x": math.nan, "y": [math.inf, -math.inf, 1.5]})

    data = _strict_loads(tool())
    assert data["x"] is None
    assert data["y"] == [None, None, 1.5]


def test_timed_list_response_is_sanitized() -> None:
    from hypertopos_mcp.server import timed

    @timed
    def tool() -> str:
        return json.dumps([{"score": math.nan}])

    # A bare list is wrapped so it can carry elapsed_ms like every other response.
    assert _strict_loads(tool())["results"] == [{"score": None}]


@pytest.mark.parametrize(
    "value", [np.float32("nan"), np.float32("inf"), np.float64(-math.inf), math.nan]
)
def test_sanitize_for_json_nulls_numpy_and_python_non_finite(value: object) -> None:
    from hypertopos_mcp._format import sanitize_for_json

    assert sanitize_for_json({"v": [value], "t": (value,)}) == {"v": [None], "t": (None,)}


def test_sanitize_for_json_keeps_finite_and_non_float_values() -> None:
    from hypertopos_mcp._format import sanitize_for_json

    payload = {"a": 1.5, "b": 0, "c": "nan", "d": None, "e": True, "f": np.float32(2.5)}
    assert sanitize_for_json(payload) == payload
