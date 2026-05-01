"""MCP wrapper tests for find_motif_by_hops."""
from __future__ import annotations

import json

import pytest


def test_returns_valid_json_with_motifs_block(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    # Berka tx_pattern has no edge_table — should fail gracefully.
    # We exercise the MCP serializer + validation paths against a real
    # navigator without paying full enumeration cost.
    out = find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[{"amount_min": 1.0}],
        max_results=3,
    )
    parsed = json.loads(out)
    assert "motifs" in parsed
    assert "n_results" in parsed
    assert parsed["pattern_id"] == "tx_pattern"


def test_invalid_amount_ratio_raises_before_edge_table_check(open_berka_sphere):
    """Predicate validation must fire BEFORE the navigator's
    'pattern has no edge table' early-return — otherwise bad predicates on
    sphere-state-less paths get silently accepted as 'no results'."""
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    # Bad ratio (out of (0, 1] bounds). Berka tx_pattern has no edge table,
    # so previously this returned {n_results: 0} silently.
    with pytest.raises(Exception, match="amount_ratio_to_prev"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[{}, {"amount_ratio_to_prev": 1.5}],
            max_results=3,
        )


def test_invalid_hop0_amount_ratio_raises_before_edge_table_check(open_berka_sphere):
    """First-hop ratio must be None — engine validation must fire even
    when sphere has no edge table."""
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match=r"hops\[0\]\.amount_ratio_to_prev"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[{"amount_ratio_to_prev": 0.5}, {}],
            max_results=3,
        )


def test_validation_anchor_pattern(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="event pattern"):
        find_motif_by_hops(
            pattern_id="account_behavior_pattern",
            hops=[{"amount_min": 1.0}],
        )


def test_validation_empty_hops(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="hops"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[],
        )


def test_validation_too_many_hops(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="hop count"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[{}] * 9,
        )


def test_amount_ratio_to_prev_passthrough(open_berka_sphere, monkeypatch):
    """MCP layer must thread amount_ratio_to_prev through to engine.

    Berka's tx_pattern has no edge table so engine validation does not
    fire — to verify the passthrough we monkeypatch the navigator's
    find_motif_by_hops to capture the parsed HopPredicate instances and
    assert the ratio field is correctly threaded.
    """
    from hypertopos_mcp.tools import analysis as analysis_mod
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    # 1. Sanity: passing amount_ratio_to_prev does not break the call,
    #    and the response is valid JSON of the expected shape.
    out = find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[{}, {"amount_ratio_to_prev": 0.5}],
        max_results=3,
    )
    parsed = json.loads(out)
    assert "motifs" in parsed
    assert "n_results" in parsed
    assert parsed["pattern_id"] == "tx_pattern"

    # 2. Capture parsed HopPredicate instances by monkeypatching the
    #    navigator method. Verify the ratio field is correctly passed.
    nav = analysis_mod._state["navigator"]
    captured: dict = {}

    original = nav.find_motif_by_hops

    def _capture(pattern_id, hops, **kwargs):
        captured["hops"] = list(hops)
        return original(pattern_id, hops, **kwargs)

    monkeypatch.setattr(nav, "find_motif_by_hops", _capture)

    find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[
            {"amount_min": 100.0},
            {"amount_ratio_to_prev": 0.5, "time_delta_max_hours": 24.0},
            {"amount_ratio_to_prev": 0.6, "direction": "forward"},
        ],
        max_results=5,
    )

    assert "hops" in captured
    parsed_hops = captured["hops"]
    assert len(parsed_hops) == 3
    # hop[0]: no ratio
    assert parsed_hops[0].amount_ratio_to_prev is None
    assert parsed_hops[0].amount_min == 100.0
    # hop[1]: ratio=0.5
    assert parsed_hops[1].amount_ratio_to_prev == 0.5
    assert parsed_hops[1].time_delta_max_hours == 24.0
    # hop[2]: ratio=0.6
    assert parsed_hops[2].amount_ratio_to_prev == 0.6
    assert parsed_hops[2].direction == "forward"


def test_time_window_hours_passthrough(open_berka_sphere, monkeypatch):
    """MCP layer must thread time_window_hours through to the navigator.

    Berka's tx_pattern has no edge table so engine validation does not
    fire — to verify the passthrough we monkeypatch the navigator's
    find_motif_by_hops to capture the kwarg and assert correct value.
    """
    from hypertopos_mcp.tools import analysis as analysis_mod
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    nav = analysis_mod._state["navigator"]
    captured: dict = {}

    original = nav.find_motif_by_hops

    def _capture(pattern_id, hops, **kwargs):
        captured["time_window_hours"] = kwargs.get("time_window_hours")
        return original(pattern_id, hops, **kwargs)

    monkeypatch.setattr(nav, "find_motif_by_hops", _capture)

    find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[{"amount_min": 100.0}, {}],
        max_results=5,
        time_window_hours=24.0,
    )

    assert captured["time_window_hours"] == 24.0


def test_require_anomalous_entity_passthrough(
    open_berka_sphere, monkeypatch,
):
    """MCP layer must thread require_anomalous_entity through to engine
    HopPredicate. Berka has no edge_table so engine validation does not
    fire — verify by capturing parsed HopPredicates."""
    from hypertopos_mcp.tools import analysis as analysis_mod
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    nav = analysis_mod._state["navigator"]
    captured: dict = {}

    original = nav.find_motif_by_hops

    def _capture(pattern_id, hops, **kwargs):
        captured["hops"] = list(hops)
        return original(pattern_id, hops, **kwargs)

    monkeypatch.setattr(nav, "find_motif_by_hops", _capture)

    find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[
            {"amount_min": 100.0, "require_anomalous_entity": True},
            {"amount_min": 100.0, "require_anomalous_entity": False},
            {"amount_min": 100.0},
        ],
        max_results=5,
    )

    parsed = captured["hops"]
    assert len(parsed) == 3
    assert parsed[0].require_anomalous_entity is True
    assert parsed[1].require_anomalous_entity is False
    assert parsed[2].require_anomalous_entity is False


def test_score_response_serializes_anchor_pattern_id(
    open_berka_sphere, monkeypatch,
):
    """MCP serializer must thread anchor_pattern_id provenance through
    JSON when score=True returns motifs scored against an anchor companion."""
    from hypertopos_mcp.tools import analysis as analysis_mod
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    nav = analysis_mod._state["navigator"]

    def _fake_navigator(pattern_id, hops, **kwargs):
        return {
            "pattern_id": pattern_id,
            "n_results": 2,
            "motifs": [
                {
                    "nodes": ["A", "B"],
                    "edges": ["TX1"],
                    "timestamps": [0.0],
                    "amounts": [100.0],
                    "score": 0.42,
                    "score_breakdown": [{"edge": ("A", "B"), "edge_potential": 0.42}],
                    "anchor_pattern_id": "account_pattern",
                },
                {
                    "nodes": ["C", "D"],
                    "edges": ["TX2"],
                    "timestamps": [1.0],
                    "amounts": [200.0],
                },
            ],
        }

    monkeypatch.setattr(nav, "find_motif_by_hops", _fake_navigator)

    out = find_motif_by_hops(
        pattern_id="tx_pattern",
        hops=[{"amount_min": 1.0}],
        max_results=5,
        score=True,
    )
    parsed = json.loads(out)
    assert parsed["n_results"] == 2
    scored = parsed["motifs"][0]
    unscored = parsed["motifs"][1]
    assert scored["anchor_pattern_id"] == "account_pattern"
    assert scored["score"] == 0.42
    assert "anchor_pattern_id" not in unscored
    assert "score" not in unscored


def test_invalid_time_window_hours_raises_before_edge_table_check(
    open_berka_sphere,
):
    """Validation must fire BEFORE the navigator's 'pattern has no edge
    table' early-return — defense-in-depth like the amount_ratio_to_prev
    case so bad inputs on edge-table-less spheres surface as errors, not
    silent {n_results: 0}."""
    from hypertopos_mcp.tools.analysis import find_motif_by_hops

    with pytest.raises(Exception, match="time_window_hours"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[{}, {}],
            max_results=3,
            time_window_hours=-1.0,
        )
    with pytest.raises(Exception, match="time_window_hours"):
        find_motif_by_hops(
            pattern_id="tx_pattern",
            hops=[{}, {}],
            max_results=3,
            time_window_hours=0.0,
        )
