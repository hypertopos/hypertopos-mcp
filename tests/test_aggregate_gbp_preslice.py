# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Test that aggregate() is a thin wrapper delegating to navigator.aggregate()."""

from __future__ import annotations

from pathlib import Path

import hypertopos_mcp.tools.aggregation as aggregation_module


def test_aggregate_delegates_to_navigator():
    """Verify aggregate() delegates to nav.aggregate() (thin wrapper pattern)."""
    # Read the module's own source file, so the test works from the monorepo
    # root and from the published package's repository alike.
    src = Path(aggregation_module.__file__).read_text(encoding="utf-8")
    assert "nav.aggregate(" in src, "aggregate() should delegate to navigator.aggregate()"
    # Ensure no numpy/pyarrow computation remains
    assert "import numpy" not in src, "numpy should not be imported in thin wrapper"
    assert "import pyarrow" not in src, "pyarrow should not be imported in thin wrapper"
