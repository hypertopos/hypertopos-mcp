# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Entity-list tools carry properties in one key-first table, not per item."""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _ensure_berka_navigator(open_berka_sphere):
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    if _state.get("navigator") is None:
        open_sphere(open_berka_sphere)
    yield


def _assert_tabled(result: dict[str, Any], list_key: str, key_field: str) -> None:
    items = result[list_key]
    assert items, f"expected {list_key} for assertion"
    assert all("properties" not in item for item in items)
    table = result["properties"]
    assert table["columns"][0] == key_field
    assert {row[0] for row in table["rows"]} <= {item[key_field] for item in items}


def test_find_hubs_tables_properties():
    from hypertopos_mcp.tools.analysis import find_hubs

    result = json.loads(find_hubs("account_stress_pattern", top_n=5))
    _assert_tabled(result, "results", "key")


def test_find_similar_entities_tables_properties():
    from hypertopos_mcp.tools.analysis import find_similar_entities

    result = json.loads(find_similar_entities("741", "account_stress_pattern", top_n=5))
    _assert_tabled(result, "similar", "primary_key")


def test_find_drifting_entities_tables_properties_and_omits_unset_params():
    from hypertopos_mcp.tools.analysis import find_drifting_entities

    result = json.loads(find_drifting_entities("account_stress_pattern", top_n=5))
    assert "filters" not in result and "forecast_horizon" not in result
    if result["results"]:
        _assert_tabled(result, "results", "primary_key")
