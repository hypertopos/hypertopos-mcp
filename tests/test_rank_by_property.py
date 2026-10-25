"""find_anomalies(rank_by_property=...) keeps the property order, pages it,
and ranks ascending on request."""

from __future__ import annotations

import json


def _ranked_values(out: dict) -> list[float]:
    cols = out["properties"]["columns"]
    key_i, val_i = cols.index("primary_key"), cols.index("min_balance")
    by_key = {row[key_i]: row[val_i] for row in out["properties"]["rows"]}
    return [by_key[p["primary_key"]] for p in out["polygons"]]


def _call(**kw: object) -> dict:
    from hypertopos_mcp.tools.navigation import find_anomalies

    return json.loads(
        find_anomalies("account_stress_pattern", rank_by_property="min_balance", **kw)
    )


def test_property_ranking_is_descending_by_default(open_berka_sphere: str) -> None:
    values = _ranked_values(_call(top_n=8))
    assert len(values) > 1
    assert values == sorted(values, reverse=True)


def test_property_ranking_can_be_ascending(open_berka_sphere: str) -> None:
    values = _ranked_values(_call(top_n=8, rank_order="asc"))
    assert len(values) > 1
    assert values == sorted(values)


def test_property_ranking_pages_with_offset(open_berka_sphere: str) -> None:
    first = _call(top_n=4)
    second = _call(top_n=4, offset=4)
    keys_first = [p["primary_key"] for p in first["polygons"]]
    keys_second = [p["primary_key"] for p in second["polygons"]]
    assert keys_second, "the second page must not be empty"
    assert not set(keys_first) & set(keys_second)
    assert min(_ranked_values(first)) >= max(_ranked_values(second))
