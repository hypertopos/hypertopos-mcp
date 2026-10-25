"""List-returning tools answer with a ``{results: [...]}`` object, never a bare list.

A bare JSON list cannot carry ``elapsed_ms`` / ``failed_parts`` and breaks the
contract every other tool follows.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import hypertopos_mcp.tools.analysis as analysis
import hypertopos_mcp.tools.observability as observability
from hypertopos_mcp.server import _record_timing, _state

_ROWS = [{"primary_key": "A", "score": 1.0}, {"primary_key": "B", "score": 0.5}]


@pytest.fixture
def fake_nav():
    nav = MagicMock()
    saved = {k: _state.get(k) for k in ("navigator", "sphere")}
    _state["navigator"] = nav
    _state["sphere"] = MagicMock()
    yield nav
    _state.update(saved)


@pytest.mark.parametrize(
    ("call", "nav_method"),
    [
        (
            lambda: analysis.simulate_counterparty_removal("A", "p", "l"),
            "simulate_counterparty_removal",
        ),
        (lambda: analysis.combine_anomaly_pvalues("p"), "combine_anomaly_pvalues"),
        (lambda: analysis.classify_detector_consensus("p"), "classify_detector_consensus"),
        (lambda: analysis.find_topological_anomalies("p"), "find_topological_anomalies"),
        (lambda: analysis.find_novel_entities("p"), "find_novel_entities"),
        (lambda: observability.find_regime_changes("p"), "π12_attract_regime_change"),
    ],
)
def test_list_tool_returns_results_envelope(fake_nav, call, nav_method) -> None:
    getattr(fake_nav, nav_method).return_value = list(_ROWS)

    data = json.loads(call())

    assert isinstance(data, dict)
    assert data["results"] == _ROWS
    assert "elapsed_ms" in data


def test_timing_wraps_a_bare_list_so_elapsed_ms_is_never_lost() -> None:
    data = json.loads(_record_timing("any_tool", 1.5, json.dumps([1, 2])))

    assert data == {"results": [1, 2], "elapsed_ms": 1.5}
