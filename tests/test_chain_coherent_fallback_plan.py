"""Tests for chain-coherent intent recognition in `detect_pattern` fallback.

Smart-mode chain integration (D1, 0.6.6 Theme D): when LLM sampling is
unavailable, the keyword fallback recognises chain-coherent investigation
language and routes to the right primitive with chain anchor pattern
+ entity anchor pattern auto-detected from sphere.
"""
from __future__ import annotations

from hypertopos_mcp.tools.smart import (
    _detect_chain_pattern_pair,
    _extract_chain_id,
    _fallback_plan,
)


def _patterns_info_with_chain(
    chain_pid: str = "tx_chains_pattern",
    chain_line: str = "tx_chains",
    entity_pid: str = "account_pattern",
    entity_line: str = "accounts",
) -> dict:
    return {
        chain_pid: {
            "type": "anchor",
            "entity_line": chain_line,
            "dimensions": [],
            "dimension_ids": [],
        },
        entity_pid: {
            "type": "anchor",
            "entity_line": entity_line,
            "dimensions": [],
            "dimension_ids": [],
        },
    }


def _patterns_info_no_chain() -> dict:
    return {
        "account_pattern": {
            "type": "anchor",
            "entity_line": "accounts",
            "dimensions": [],
            "dimension_ids": [],
        },
        "tx_pattern": {
            "type": "event",
            "entity_line": "transactions",
            "dimensions": [],
            "dimension_ids": [],
        },
    }


# ---------------------------------------------------------------------------
# _detect_chain_pattern_pair
# ---------------------------------------------------------------------------


def test_detect_chain_pair_basic():
    pair = _detect_chain_pattern_pair(_patterns_info_with_chain())
    assert pair == ("tx_chains_pattern", "account_pattern")


def test_detect_chain_pair_prefers_account_anchor():
    info = {
        "tx_chains_pattern": {
            "type": "anchor", "entity_line": "tx_chains",
            "dimensions": [], "dimension_ids": [],
        },
        "account_pattern": {
            "type": "anchor", "entity_line": "accounts",
            "dimensions": [], "dimension_ids": [],
        },
        "currency_pattern": {
            "type": "anchor", "entity_line": "currencies",
            "dimensions": [], "dimension_ids": [],
        },
    }
    pair = _detect_chain_pattern_pair(info)
    assert pair == ("tx_chains_pattern", "account_pattern")


def test_detect_chain_pair_returns_none_when_no_chain():
    pair = _detect_chain_pattern_pair(_patterns_info_no_chain())
    assert pair is None


def test_detect_chain_pair_returns_none_when_only_chain_no_anchor():
    info = {
        "tx_chains_pattern": {
            "type": "anchor", "entity_line": "tx_chains",
            "dimensions": [], "dimension_ids": [],
        },
    }
    pair = _detect_chain_pattern_pair(info)
    assert pair is None


# ---------------------------------------------------------------------------
# _extract_chain_id
# ---------------------------------------------------------------------------


def test_extract_chain_id_uppercase():
    assert _extract_chain_id("classify chain CHAIN-109852") == "CHAIN-109852"


def test_extract_chain_id_lowercase():
    assert _extract_chain_id("trace chain-001 hop by hop") == "chain-001"


def test_extract_chain_id_returns_none_when_absent():
    assert _extract_chain_id("find chains where consecutive accounts cascade") is None


def test_extract_chain_id_first_match_only():
    assert _extract_chain_id("compare CHAIN-001 with CHAIN-002") == "CHAIN-001"


def test_extract_chain_id_strict_digit_format():
    """Production chain_ids are CHAIN-<digits>. Multi-word lookalikes
    like CHAIN-AML-001 should NOT match — regex is intentionally strict
    to avoid false positives that would feed garbage into chain primitives.
    """
    assert _extract_chain_id("trace CHAIN-AML-001") is None
    assert _extract_chain_id("compare CHAIN-XYZ_2026 with CHAIN-001") == "CHAIN-001"
    assert _extract_chain_id("trace CHAIN-109852") == "CHAIN-109852"


# ---------------------------------------------------------------------------
# _fallback_plan routing for chain-coherent intents
# ---------------------------------------------------------------------------


def _available_full() -> list[str]:
    return [
        "find_anomalies",
        "find_chains_with_coherent_anomaly",
        "anomaly_propagation_in_chain",
        "classify_chain_typology",
        "extend_chain",
        "find_chains_for_entity",
        "extract_chains",
    ]


def test_fallback_routes_coherent_anomaly_query():
    plan = _fallback_plan(
        "find chains where consecutive accounts cascade through structuring",
        _available_full(),
        _patterns_info_with_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "find_chains_with_coherent_anomaly" in step_names
    cs = next(s for s in plan["steps"] if s["name"] == "find_chains_with_coherent_anomaly")
    assert cs["params"]["pattern_id"] == "tx_chains_pattern"
    assert cs["params"]["anchor_pattern_id"] == "account_pattern"


def test_fallback_routes_classify_chain_with_id():
    plan = _fallback_plan(
        "classify chain CHAIN-109852",
        _available_full(),
        _patterns_info_with_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "classify_chain_typology" in step_names
    cs = next(s for s in plan["steps"] if s["name"] == "classify_chain_typology")
    assert cs["params"]["chain_id"] == "CHAIN-109852"
    assert cs["params"]["pattern_id"] == "tx_chains_pattern"
    assert cs["params"]["anchor_pattern_id"] == "account_pattern"


def test_fallback_routes_anomaly_propagation_with_id():
    plan = _fallback_plan(
        "trace chain CHAIN-109852 hop by hop",
        _available_full(),
        _patterns_info_with_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "anomaly_propagation_in_chain" in step_names


def test_fallback_routes_extend_chain_with_id():
    plan = _fallback_plan(
        "extend chain CHAIN-109852 forward",
        _available_full(),
        _patterns_info_with_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "extend_chain" in step_names


def test_fallback_skips_chain_id_required_steps_without_id():
    """When the query matches an intent that needs chain_id but no
    CHAIN-X token is in the query, the step is skipped (don't crash
    with empty chain_id)."""
    plan = _fallback_plan(
        "classify chain shape",
        _available_full(),
        _patterns_info_with_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "classify_chain_typology" not in step_names


def test_fallback_skips_chain_steps_when_no_chain_pattern():
    plan = _fallback_plan(
        "find chains where consecutive accounts cascade",
        _available_full(),
        _patterns_info_no_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "find_chains_with_coherent_anomaly" not in step_names


def test_fallback_chain_coherent_takes_precedence_over_extract_chains():
    """A query mentioning 'chain' AND 'cascade' should route to
    find_chains_with_coherent_anomaly only — extract_chains must NOT
    also fire. The precedence holds because (a) the specific cascade
    intent appears first in the _kw dict iteration order, and (b)
    extract_chains is in _NEEDS_ENTITY_CONTEXT which the loop skips
    when no primary_key is supplied. The negative assertion below
    future-proofs against either invariant changing silently.
    """
    plan = _fallback_plan(
        "find chains where consecutive accounts cascade through structuring",
        _available_full(),
        _patterns_info_with_chain(),
    )
    step_names = [s["name"] for s in plan["steps"]]
    assert "find_chains_with_coherent_anomaly" in step_names
    assert "extract_chains" not in step_names
