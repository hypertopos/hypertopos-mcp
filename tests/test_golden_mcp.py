# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Golden-output regression tests for the main MCP tools.

Each test snapshots the JSON returned by one tool on the synthetic
Berka-shaped sphere (built by the builder from seeded rows, so builder
output is under test too) and fails with a path-level diff when it
changes. Regenerate with ``HYPERTOPOS_UPDATE_GOLDEN=1``
(see ``tests/golden/README.md``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from _golden_mcp import canonicalize, check_golden

# Top anomaly of account_behavior_pattern on the synthetic sphere. Pinned
# (not looked up) so per-entity snapshots stay comparable if ranking moves.
_ENTITY = "85"


@pytest.fixture(scope="module")
def sphere(open_berka_sphere: str) -> Iterator[str]:
    """The opened synthetic sphere; sphere_overview runs first, as agents do.

    Goldens pin the full response format so they guard every value, including the
    subtrees compact mode leaves out.
    """
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    # Reopen so state left by earlier modules in the same worker cannot leak in.
    open_sphere(open_berka_sphere, response_format="full")
    from hypertopos_mcp.tools.observability import sphere_overview

    sphere_overview()
    yield open_berka_sphere
    _state["response_format"] = None


def _call(fn: Callable[..., str], *args: Any, **kwargs: Any) -> Any:
    out = json.loads(fn(*args, **kwargs))
    assert not (isinstance(out, dict) and "error" in out), out
    return out


def _snap(name: str, payload: Any, sphere_path: str) -> None:
    tokens = {str(Path(sphere_path)): "<SPHERE>"}
    check_golden(name, canonicalize(payload, path_tokens=tokens))


def test_calibration(sphere: str) -> None:
    meta = json.loads((Path(sphere) / "_gds_meta" / "sphere.json").read_text(encoding="utf-8"))
    keep = ("pattern_type", "mu", "sigma_diag", "theta", "population_size", "relations")
    payload = {pid: {k: pat.get(k) for k in keep} for pid, pat in sorted(meta["patterns"].items())}
    _snap("calibration", payload, sphere)


def test_sphere_overview(sphere: str) -> None:
    from hypertopos_mcp.tools.observability import sphere_overview

    _snap("sphere_overview", _call(sphere_overview), sphere)


def test_find_anomalies_anchor(sphere: str) -> None:
    from hypertopos_mcp.tools.navigation import find_anomalies

    out = _call(find_anomalies, "account_behavior_pattern", top_n=10)
    assert out["polygons"]
    _snap("find_anomalies_anchor", out, sphere)


def test_find_anomalies_event(sphere: str) -> None:
    from hypertopos_mcp.tools.navigation import find_anomalies

    out = _call(find_anomalies, "tx_pattern", top_n=5)
    assert out["polygons"]
    _snap("find_anomalies_event", out, sphere)


def test_explain_anomaly(sphere: str) -> None:
    from hypertopos_mcp.tools.analysis import explain_anomaly

    _snap("explain_anomaly", _call(explain_anomaly, _ENTITY, "account_behavior_pattern"), sphere)


def test_passive_scan(sphere: str) -> None:
    from hypertopos_mcp.tools.analysis import passive_scan

    out = _call(passive_scan, "accounts", threshold=1, top_n=10)
    assert out["hits"]
    _snap("passive_scan", out, sphere)


def test_find_drifting_entities(sphere: str) -> None:
    from hypertopos_mcp.tools.analysis import find_drifting_entities

    out = _call(find_drifting_entities, "account_behavior_pattern", top_n=5)
    assert out["results"]
    _snap("find_drifting_entities", out, sphere)


def test_aggregate(sphere: str) -> None:
    from hypertopos_mcp.tools.aggregation import aggregate

    payload = {
        "count_by_account": _call(aggregate, "tx_pattern", "accounts", "count", top_n=5),
        "sum_amount_by_tx_type": _call(aggregate, "tx_pattern", "tx_types", "sum:amount"),
        "avg_amount_by_frequency": _call(
            aggregate,
            "tx_pattern",
            "accounts",
            "avg:amount",
            group_by_property="accounts:frequency",
        ),
    }
    _snap("aggregate", payload, sphere)


def test_find_counterparties(sphere: str) -> None:
    from hypertopos_mcp.tools.analysis import find_counterparties

    # top_n=4 cuts inside the tx_count == 1 tie: the tie-break by key decides
    # which counterparties make the cut.
    out = _call(find_counterparties, _ENTITY, "transactions", "account_id", "bank", top_n=4)
    _snap("find_counterparties", out, sphere)


def test_extract_chains_disjoint_hint(sphere: str) -> None:
    from hypertopos_mcp.tools.analysis import extract_chains

    out = _call(
        extract_chains,
        "tx_pattern",
        "account_id",
        "bank",
        time_col="date",
        amount_col="amount",
        top_n=5,
    )
    _snap("extract_chains_disjoint_hint", out, sphere)


def test_check_alerts(sphere: str) -> None:
    from hypertopos_mcp.tools.observability import check_alerts

    _snap("check_alerts", _call(check_alerts), sphere)


def test_investigate_entity(sphere: str) -> None:
    from hypertopos_mcp.tools.analysis import investigate_entity

    out = _call(
        investigate_entity,
        _ENTITY,
        "account_behavior_pattern",
        "accounts",
        include_chains=False,
        top_n_witnesses=3,
    )
    _snap("investigate_entity", out, sphere)
