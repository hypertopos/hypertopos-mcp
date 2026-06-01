# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""extract_chains error-contract: bad input returns an {error:...} body, not a raise.

extract_chains previously raised ValueError for an unrecognised event_pattern_id
but returned an {error:...} JSON body for a missing from_col/to_col — two failure
surfaces for the same bad-argument class in one tool. Both now return error-JSON
so an agent has a single failure shape to handle.
"""
from __future__ import annotations

import json


def test_bad_pattern_returns_error_json_not_raise(open_berka_sphere):
    from hypertopos_mcp.tools.analysis import extract_chains

    payload = extract_chains(
        event_pattern_id="NO_SUCH_PATTERN_OR_LINE",
        from_col="from_account",
        to_col="to_account",
    )
    # Must be a parseable JSON body carrying an error, NOT a thrown exception.
    report = json.loads(payload)
    assert "error" in report
    assert "neither a pattern nor a line" in report["error"]


def test_bad_column_returns_error_json(open_berka_sphere):
    """The sibling branch (bad column) already returned error-JSON — pin it so
    both bad-argument branches stay on the same contract."""
    from hypertopos_mcp.tools.analysis import extract_chains
    from hypertopos_mcp.server import _state

    # Pick a real event line/pattern, then pass a column that does not exist.
    sphere = _state["sphere"]._sphere
    event_pattern = next(
        (pid for pid, p in sphere.patterns.items() if p.pattern_type == "event"),
        None,
    )
    if event_pattern is None:
        import pytest

        pytest.skip("Berka exposes no event pattern for the bad-column path")

    payload = extract_chains(
        event_pattern_id=event_pattern,
        from_col="NO_SUCH_COLUMN",
        to_col="NO_SUCH_COLUMN",
    )
    report = json.loads(payload)
    assert "error" in report
    assert "not found in line schema" in report["error"]
