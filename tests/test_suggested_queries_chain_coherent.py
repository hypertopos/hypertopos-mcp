"""Tests for chain-coherent entry in suggested_queries (D2, 0.6.6 Theme D).

When the sphere has a chain anchor pattern alongside an entity anchor
pattern, _suggest_queries surfaces the chain-coherent investigative loop
entry point so agents discover it via open_sphere's response without
having to read sphere_overview's chain-anchor section.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypertopos_mcp.tools.session import _suggest_queries


@dataclass
class _FakeRelation:
    line_id: str


@dataclass
class _FakePattern:
    pattern_type: str
    relations: list[_FakeRelation]


class _FakeSphere:
    def __init__(
        self,
        patterns: dict[str, _FakePattern],
        entity_lines: dict[str, str],
        aliases: dict[str, Any] | None = None,
    ):
        self.patterns = patterns
        self._entity_lines = entity_lines
        self.aliases = aliases or {}

    def entity_line(self, pid: str) -> str | None:
        return self._entity_lines.get(pid)


def test_no_chain_pattern_no_chain_query():
    sphere = _FakeSphere(
        patterns={
            "account_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_tx_count")],
            ),
        },
        entity_lines={"account_pattern": "accounts"},
    )
    queries = _suggest_queries(sphere)
    assert not any("individually anomalous" in q for q in queries)


def test_chain_pattern_alone_no_chain_query():
    """Chain pattern without entity anchor — can't form the cascade query
    since we need an entity_line for the natural-language template."""
    sphere = _FakeSphere(
        patterns={
            "tx_chains_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_hop_count")],
            ),
        },
        entity_lines={"tx_chains_pattern": "tx_chains"},
    )
    queries = _suggest_queries(sphere)
    assert not any("individually anomalous" in q for q in queries)


def test_chain_plus_entity_anchor_surfaces_query():
    sphere = _FakeSphere(
        patterns={
            "account_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_tx_count")],
            ),
            "tx_chains_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_hop_count")],
            ),
        },
        entity_lines={
            "account_pattern": "accounts",
            "tx_chains_pattern": "tx_chains",
        },
    )
    queries = _suggest_queries(sphere)
    chain_q = next(
        (q for q in queries if "individually anomalous" in q), None,
    )
    assert chain_q is not None
    assert "tx_chains_pattern" in chain_q
    assert "accounts" in chain_q


def test_chain_query_uses_first_non_chain_entity_anchor():
    """When multiple non-chain anchors exist, pick the first one as the
    entity-line reference for the cascade query."""
    sphere = _FakeSphere(
        patterns={
            "account_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_tx_count")],
            ),
            "currency_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_count")],
            ),
            "tx_chains_pattern": _FakePattern(
                pattern_type="anchor",
                relations=[_FakeRelation("_d_hop_count")],
            ),
        },
        entity_lines={
            "account_pattern": "accounts",
            "currency_pattern": "currencies",
            "tx_chains_pattern": "tx_chains",
        },
    )
    queries = _suggest_queries(sphere)
    chain_q = next(
        (q for q in queries if "individually anomalous" in q), None,
    )
    assert chain_q is not None
    # First non-chain anchor in iteration order is account_pattern → accounts
    assert "accounts" in chain_q


def test_query_cap_increased_to_six():
    """With a chain anchor + 5 other anchor patterns, suggest_queries
    should still return up to 6 entries (cap raised from 5 to 6 to fit
    the new chain-coherent suggestion)."""
    patterns = {
        f"pattern_{i}": _FakePattern(
            pattern_type="anchor",
            relations=[_FakeRelation(f"_d_dim_{i}")],
        )
        for i in range(5)
    }
    patterns["tx_chains_pattern"] = _FakePattern(
        pattern_type="anchor",
        relations=[_FakeRelation("_d_hop_count")],
    )
    entity_lines = {
        f"pattern_{i}": f"line_{i}" for i in range(5)
    }
    entity_lines["tx_chains_pattern"] = "tx_chains"
    sphere = _FakeSphere(patterns=patterns, entity_lines=entity_lines)
    queries = _suggest_queries(sphere)
    assert len(queries) <= 6
