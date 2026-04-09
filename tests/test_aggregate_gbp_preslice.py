# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Test that aggregate() is a thin wrapper delegating to navigator.aggregate()."""

from __future__ import annotations

from pathlib import Path


def test_aggregate_delegates_to_navigator():
    """Verify aggregate() delegates to nav.aggregate() (thin wrapper pattern)."""
    src = Path("packages/hypertopos-mcp/src/hypertopos_mcp/tools/aggregation.py").read_text()
    assert "nav.aggregate(" in src, "aggregate() should delegate to navigator.aggregate()"
    # Ensure no numpy/pyarrow computation remains
    assert "import numpy" not in src, "numpy should not be imported in thin wrapper"
    assert "import pyarrow" not in src, "pyarrow should not be imported in thin wrapper"
