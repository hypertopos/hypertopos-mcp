# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Lossless response restructuring shared by MCP tools."""

from __future__ import annotations

import math
from collections import Counter
from contextvars import ContextVar
from datetime import date, time
from typing import Any

import numpy as np

# open_sphere(force_reload=True) reloads this module in the middle of a timed
# call; keeping the existing ContextVar lets the wrapper close the collection
# it opened before the reload.
_failed_parts: ContextVar[dict[tuple[str, str], None] | None] = globals().get(
    "_failed_parts"
) or ContextVar("failed_parts", default=None)


def record_failure(part: str, exc: BaseException) -> None:
    """Name a response part that could not be loaded; the tool still answers.

    The timed wrapper attaches the parts recorded during a call as a top-level
    ``failed_parts`` list, so an agent can tell "absent" from "failed".
    Outside a tool call this does nothing.
    """
    parts = _failed_parts.get()
    if parts is not None:
        parts[(part, f"{type(exc).__name__}: {exc}")] = None


def collect_failures() -> Any:
    """Start collecting failed parts for one call; returns the reset token."""
    return _failed_parts.set({})


def take_failures(token: Any) -> list[dict[str, str]]:
    """Stop collecting and return the parts recorded since ``collect_failures``."""
    parts = _failed_parts.get() or {}
    _failed_parts.reset(token)
    return [{"part": part, "error": error} for part, error in parts]


def json_default(obj: Any) -> Any:
    """``json.dumps(default=...)`` for tool responses.

    Numpy scalars become JSON numbers / booleans (a non-finite float becomes
    null), dates and datetimes ISO strings, anything else its ``str``.
    ``default=str`` would turn ``np.float32(0.5)`` into the string ``"0.5"``.
    """
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, (date, time)):
        return obj.isoformat()
    return str(obj)


def sanitize_for_json(obj: Any) -> Any:
    """Replace non-finite floats (``±inf`` / ``NaN``) with ``None`` recursively.

    ``json.dumps`` writes ``Infinity`` / ``NaN`` literals, which strict parsers
    reject. ``_record_timing`` nulls them for every timed response; call this
    before a ``json.dumps`` whose output is not timed. ``json_default`` never
    sees Python floats, so it cannot null them.
    """
    if isinstance(obj, (float, np.floating)) and not math.isfinite(float(obj)):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_for_json(v) for v in obj)
    return obj


_DIM_EDGE_CONSTANTS = {"point_key": "", "status": "alive", "direction": "out", "is_jumpable": False}


def compact_dim_edges(item: dict[str, Any]) -> None:
    """Replace dim-line edges (no point key) by one ``dim_edges`` list of line ids.

    Lossless: applied only when every such edge carries the same constant fields
    (empty ``point_key``, alive, out, not jumpable); entity edges stay in ``edges``.
    """
    edges = item.get("edges") or []
    dim = [e for e in edges if not e.get("point_key")]
    if not dim or any(
        set(e) != {"line_id", *_DIM_EDGE_CONSTANTS}
        or any(e[k] != v for k, v in _DIM_EDGE_CONSTANTS.items())
        for e in dim
    ):
        return
    item["dim_edges"] = [e["line_id"] for e in dim]
    rest = [e for e in edges if e.get("point_key")]
    if rest:
        item["edges"] = rest
    else:
        del item["edges"]


def tabulate_properties(items: list[dict[str, Any]], key_field: str) -> dict[str, Any] | None:
    """Pop ``properties`` off each item into one key-first table.

    Returns ``{"columns": [key_field, ...], "rows": [[key, ...], ...]}`` with the union
    of property names (first-seen order) and ``None`` for absent cells, or ``None``
    when no item carried properties.
    """
    keyed = [(item[key_field], item.pop("properties")) for item in items if "properties" in item]
    if not keyed:
        return None
    columns: dict[str, None] = {}
    for _, props in keyed:
        columns.update(dict.fromkeys(props))
    return {
        "columns": [key_field, *columns],
        "rows": [[key, *(props.get(c) for c in columns)] for key, props in keyed],
    }


def project_descriptive(
    table: dict[str, Any], keep: set[str] | frozenset[str] = frozenset()
) -> list[str]:
    """Compact mode: keep the key, text columns and ``keep``; drop the rest in place.

    A column is descriptive when every non-null cell is a string. Returns the dropped
    column names (empty when nothing was dropped).
    """
    columns, rows = table["columns"], table["rows"]
    kept = [0] + [
        i
        for i, name in enumerate(columns[1:], start=1)
        if name in keep or all(r[i] is None or isinstance(r[i], str) for r in rows)
    ]
    dropped = [name for i, name in enumerate(columns) if i not in kept]
    if dropped:
        table["columns"] = [columns[i] for i in kept]
        table["rows"] = [[r[i] for i in kept] for r in rows]
    return dropped


def truncate_long_lists(entries: list[dict[str, Any]], limit: int = 10) -> dict[str, int]:
    """Compact mode: cut list values longer than ``limit`` to their first ``limit`` items.

    Returns ``{"<seq>.<field>": full_length}`` for every cut list (``seq`` is the
    entry's own ``seq`` when it has one, else its 1-based position).
    """
    cut: dict[str, int] = {}
    for pos, entry in enumerate(entries, start=1):
        for field, value in entry.items():
            if isinstance(value, list) and len(value) > limit:
                cut[f"{entry.get('seq', pos)}.{field}"] = len(value)
                entry[field] = value[:limit]
    return cut


def drop_positional_deltas(
    polygons: list[dict[str, Any]], clusters: list[dict[str, Any]] | None = None
) -> bool:
    """Compact mode: drop ``delta`` where a labelled form exists (``anomaly_dimensions`` on a
    polygon, ``label`` on a cluster). Returns True when anything was dropped.
    """
    dropped = False
    for item, labelled in [(p, "anomaly_dimensions") for p in polygons] + [
        (c, "label") for c in clusters or []
    ]:
        if labelled in item and item.pop("delta", None) is not None:
            dropped = True
    return dropped


_EDGE_FIELDS = frozenset({"line_id", "point_key", "status", "direction", "is_jumpable"})


def tabulate_edge_properties(polygons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pop endpoint properties off every edge into one ``point_key``-first table per line.

    Each endpoint appears once per line, however many edges reference it.
    """
    per_line: dict[str, dict[str, dict[str, Any]]] = {}
    for poly in polygons:
        for edge in poly.get("edges", []):
            props = {k: edge.pop(k) for k in list(edge) if k not in _EDGE_FIELDS}
            if props:
                per_line.setdefault(edge["line_id"], {}).setdefault(edge["point_key"], props)
    return {
        line_id: table
        for line_id, by_key in per_line.items()
        if (
            table := tabulate_properties(
                [{"point_key": k, "properties": p} for k, p in by_key.items()], "point_key"
            )
        )
    }


def slim_polygons(polygons: list[dict[str, Any]], pattern_id: str) -> None:
    """Drop the constant ``type`` and a ``pattern_id`` equal to the response's own."""
    for poly in polygons:
        poly.pop("type", None)
        if poly.get("pattern_id") == pattern_id:
            del poly["pattern_id"]


def labelled(values: list[Any], labels: list[str]) -> dict[str, Any] | None:
    """``{label: value}`` when the labels are unique and match ``values`` one to one.

    Returns ``None`` otherwise (e.g. two relations to the same line share a label):
    the caller keeps the positional form, which stays unambiguous.
    """
    if len(labels) != len(values) or len(set(labels)) != len(labels):
        return None
    return dict(zip(labels, values, strict=True))


def drop_numeric_properties(item: dict[str, Any]) -> list[str]:
    """Compact mode: keep only the text values of ``item["properties"]``; return dropped names."""
    props = item.get("properties")
    if not isinstance(props, dict):
        return []
    dropped = [k for k, v in props.items() if v is not None and not isinstance(v, str)]
    for k in dropped:
        del props[k]
    return dropped


def compact_polygon(
    item: dict[str, Any], labels: list[str], pattern_id: str, *, text_properties_only: bool = True
) -> dict[str, Any]:
    """Compact mode for one serialised polygon, in place; returns its ``omitted`` entries.

    Drops the constant ``type`` and the echoed ``pattern_id``, folds dim-line edges
    into ``dim_edges``, replaces the positional ``delta`` with a labelled
    ``delta_by_dim`` map (when labels are unique) and, with ``text_properties_only``,
    keeps only text properties (an event's own amounts stay: they are its data).
    """
    slim_polygons([item], pattern_id)
    compact_dim_edges(item)
    omitted: dict[str, Any] = {}
    by_dim = labelled(item.get("delta") or [], labels)
    if by_dim is not None:
        del item["delta"]
        item["delta_by_dim"] = by_dim
    elif item.get("delta"):
        item["dim_labels"] = labels  # positional delta: name its components
    dropped = drop_numeric_properties(item) if text_properties_only else []
    if dropped:
        omitted["properties"] = dropped
    return omitted


def compact_solid(solid: dict[str, Any], labels: list[str]) -> dict[str, Any]:
    """Compact mode for a serialised Solid, in place; returns its ``omitted`` entries.

    The base polygon is compacted like ``compact_polygon``; slices lose their null
    ``changed_*`` fields, and their positional ``delta_snapshot`` vectors get one
    shared ``slice_dim_labels`` header (labels repeat when two relations share a
    line; slices may carry fewer dims than the pattern: the leading relation dims).
    """
    omitted: dict[str, Any] = {}
    base = solid.get("base_polygon")
    if base:
        omitted = compact_polygon(base, labels, solid.get("pattern_id", ""))
    slices = solid.get("slices") or []
    for sl in slices:
        for key in ("changed_property", "changed_line_id"):
            if sl.get(key) is None:
                sl.pop(key, None)
    widths = {len(sl.get("delta_snapshot") or []) for sl in slices}
    if len(widths) == 1:
        width = widths.pop()
        header = labels[:width]
        if width and len(header) == width:
            solid["slice_dim_labels"] = header
    return omitted


def hoist_constant_fields(items: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
    """Move each of ``fields`` that every item carries with the same value out of the items.

    Returns ``{field: value}`` for the hoisted fields; fields that differ stay inline.
    """
    hoisted: dict[str, Any] = {}
    if not items:
        return hoisted
    for field in fields:
        if all(field in item for item in items) and all(
            item[field] == items[0][field] for item in items
        ):
            hoisted[field] = items[0][field]
            for item in items:
                del item[field]
    return hoisted


def drop_none_fields(items: list[dict[str, Any]]) -> None:
    """Remove ``None``-valued keys from each item (an unset field is simply absent)."""
    for item in items:
        for key in [k for k, v in item.items() if v is None]:
            del item[key]


def dedupe_nested_errors(obj: Any) -> dict[str, str]:
    """Replace ``error`` texts repeated anywhere in ``obj`` by ``error_ref`` ids.

    Walks nested dicts / lists in place; returns ``{ref: text}`` for the texts
    that occurred more than once (a text seen once stays inline).
    """
    holders: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("error"), str):
                holders.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(obj)
    return dedupe_text(holders, "error")


def dedupe_text(items: list[dict[str, Any]], field: str) -> dict[str, str]:
    """Replace texts repeated across items with ``<field>_ref`` ids into a returned map.

    A text that occurs once stays inline.
    """
    counts = Counter(item[field] for item in items if field in item)
    refs = {text: f"t{i}" for i, text in enumerate((t for t, n in counts.items() if n > 1), 1)}
    for item in items:
        ref = refs.get(item.get(field))
        if ref is not None:
            del item[field]
            item[f"{field}_ref"] = ref
    return {ref: text for text, ref in refs.items()}


def hoist_repeated_text(
    items: list[dict[str, Any]], key_field: str, text_field: str
) -> dict[str, str]:
    """Move ``text_field`` into a ``{key: text}`` map when every item of a key shares it.

    Keys whose items carry two or more distinct texts keep the text inline.
    """
    texts: dict[str, set[str]] = {}
    for item in items:
        if text_field in item:
            texts.setdefault(item[key_field], set()).add(item[text_field])
    hoisted = {key: next(iter(values)) for key, values in texts.items() if len(values) == 1}
    for item in items:
        if item.get(key_field) in hoisted:
            item.pop(text_field, None)
    return hoisted
