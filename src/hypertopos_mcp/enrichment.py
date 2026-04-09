# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Shared enrichment — resolve business keys to human-readable properties."""

from __future__ import annotations

from typing import Any

_META_COLUMNS = frozenset({"primary_key", "version", "status", "created_at", "changed_at"})


def build_entity_lookups(
    reader: Any,
    sphere: Any,
    line_ids: set[str],
) -> dict[str, dict[str, dict]]:
    lookups: dict[str, dict[str, dict]] = {}
    for line_id in line_ids:
        line = sphere.lines.get(line_id)
        if line is None:
            continue
        try:
            table = reader.read_points(line_id, line.versions[-1])
        except Exception:
            continue
        keep = [c for c in table.schema.names if c not in _META_COLUMNS]
        if not keep:
            continue
        keys = table["primary_key"].to_pylist()
        col_data = {c: table[c].to_pylist() for c in keep}
        lookups[line_id] = {keys[i]: {c: col_data[c][i] for c in keep} for i in range(len(keys))}
    return lookups


def build_batch_lookups(
    reader: Any,
    sphere: Any,
    keys_by_line: dict[str, set[str]],
) -> dict[str, dict[str, dict]]:
    """Batch lookup — fetches only specified keys per line (O(k) vs O(n) full scan)."""
    lookups: dict[str, dict[str, dict]] = {}
    for line_id, keys in keys_by_line.items():
        if not keys:
            continue
        line = sphere.lines.get(line_id)
        if line is None:
            continue
        try:
            table = reader.read_points_batch(line_id, line.versions[-1], list(keys))
        except Exception:
            continue
        keep = [c for c in table.schema.names if c not in _META_COLUMNS]
        if not keep or len(table) == 0:
            continue
        pk_list = table["primary_key"].to_pylist()
        col_data = {c: table[c].to_pylist() for c in keep}
        lookups[line_id] = {
            pk_list[i]: {c: col_data[c][i] for c in keep} for i in range(len(pk_list))
        }
    return lookups


def lookup_entity(
    reader: Any,
    sphere: Any,
    line_id: str,
    primary_key: str,
) -> dict | None:
    """Fetch properties for a single entity by key. Returns None if not found."""
    result = build_batch_lookups(reader, sphere, {line_id: {primary_key}})
    props = result.get(line_id, {}).get(primary_key)
    return {k: v for k, v in props.items() if v is not None} if props else None


def enrich_polygon(
    serialized: dict,
    lookups: dict[str, dict[str, dict]],
    entity_line_id: str | None = None,
) -> dict:
    bk = serialized.get("primary_key")
    if entity_line_id and bk:
        props = lookups.get(entity_line_id, {}).get(bk, {})
        if props:
            serialized["properties"] = {k: v for k, v in props.items() if v is not None}
    for edge in serialized.get("edges", []):
        lid = edge["line_id"]
        pk = edge["point_key"]
        edge_props = lookups.get(lid, {}).get(pk, {})
        for k, v in edge_props.items():
            if v is not None:
                edge[k] = v
    return serialized


def _collect_required_keys(
    serialized_polygons: list[dict],
    entity_line_id: str | None,
) -> dict[str, set[str]]:
    """Collect {line_id: {point_key, ...}} for all keys needed to enrich polygons."""
    required: dict[str, set[str]] = {}
    for sp in serialized_polygons:
        pk = sp.get("primary_key")
        if entity_line_id and pk:
            required.setdefault(entity_line_id, set()).add(pk)
        for edge in sp.get("edges", []):
            required.setdefault(edge["line_id"], set()).add(edge["point_key"])
    return required


def enrich_polygons(
    serialized_polygons: list[dict],
    reader: Any,
    sphere: Any,
    entity_line_id: str | None = None,
) -> list[dict]:
    """Enrich polygons with point properties.

    Uses read_points_batch — fetches only the specific keys referenced by the
    polygon edges instead of loading entire tables. Critical for large event
    lines (e.g. gl_values with 1M rows).
    """
    required_keys = _collect_required_keys(serialized_polygons, entity_line_id)
    lookups: dict[str, dict[str, dict]] = {}
    for line_id, keys in required_keys.items():
        line = sphere.lines.get(line_id)
        if line is None:
            continue
        try:
            table = reader.read_points_batch(line_id, line.versions[-1], list(keys))
        except Exception:
            continue
        keep = [c for c in table.schema.names if c not in _META_COLUMNS]
        if not keep:
            continue
        pk_list = table["primary_key"].to_pylist()
        col_data = {c: table[c].to_pylist() for c in keep}
        lookups[line_id] = {
            pk_list[i]: {c: col_data[c][i] for c in keep} for i in range(len(pk_list))
        }
    return [enrich_polygon(sp, lookups, entity_line_id) for sp in serialized_polygons]


def resolve_entity_line_id(sphere: Any, pattern_id: str) -> str | None:
    """Delegate to Sphere.entity_line() — kept for backward compatibility."""
    return sphere.entity_line(pattern_id)
