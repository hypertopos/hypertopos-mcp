# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

import pytest

# Add hypertopos-mcp/src/ to path so tests can import hypertopos_mcp package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Snapshot of _state after the sphere is opened — used by _restore_berka_state.
_berka_state_snapshot: dict | None = None


@pytest.fixture(scope="session")
def open_berka_sphere():
    """Open the Berka sphere once per session (read-only)."""
    global _berka_state_snapshot
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    open_sphere("benchmark/berka/sphere/gds_berka_banking")
    _berka_state_snapshot = dict(_state)
    yield
    for k in list(_state.keys()):
        _state[k] = None


@pytest.fixture(autouse=True)
def _restore_berka_state():
    """Restore _state after tests that replace it with mocks."""
    yield
    if _berka_state_snapshot is not None:
        from hypertopos_mcp.server import _state

        if _state.get("sphere") is None and _berka_state_snapshot.get("sphere") is not None:
            _state.update(_berka_state_snapshot)
