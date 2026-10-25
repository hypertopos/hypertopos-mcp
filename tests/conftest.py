# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest
from filelock import FileLock

# Add hypertopos-mcp/src/ to path so tests can import hypertopos_mcp package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Snapshot of _state after the sphere is opened — used by _restore_berka_state.
_berka_state_snapshot: dict | None = None

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# Synthetic Berka-shaped sphere, generated + built once (gitignored output).
_BERKA_SYNTH_SPHERE = _FIXTURES_DIR / "gds" / "berka_synth"
_BERKA_SYNTH_GENERATOR = _FIXTURES_DIR / "berka_synth" / "generate.py"
# Real full Berka benchmark sphere — optional, only for ``berka_full`` tests.
_BERKA_FULL_SPHERE = (
    Path(__file__).resolve().parents[3] / "benchmark" / "berka" / "sphere" / "gds_berka_banking"
)


def _ensure_berka_synth_sphere() -> Path:
    """Generate + build the synthetic sphere once (xdist-safe via file lock)."""
    lock_path = _BERKA_SYNTH_SPHERE.parent / ".berka_synth.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path)):
        if not (_BERKA_SYNTH_SPHERE / "_gds_meta" / "sphere.json").exists():
            spec = importlib.util.spec_from_file_location(
                "_berka_synth_generate", _BERKA_SYNTH_GENERATOR
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.build(_BERKA_SYNTH_SPHERE)
    # Opening a sphere purges its temporal agent state on disk, so under
    # pytest-xdist each worker gets a private copy of the built sphere.
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        return _BERKA_SYNTH_SPHERE
    worker_copy = _BERKA_SYNTH_SPHERE.with_name(f"{_BERKA_SYNTH_SPHERE.name}_{worker}")
    shutil.rmtree(worker_copy, ignore_errors=True)
    shutil.copytree(_BERKA_SYNTH_SPHERE, worker_copy)
    return worker_copy


@pytest.fixture(scope="session")
def open_berka_sphere():
    """Open the synthetic Berka-shaped sphere once per session (read-only).

    Yields the absolute sphere path so tests that reopen the sphere use the
    same one.
    """
    global _berka_state_snapshot
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    path = str(_ensure_berka_synth_sphere())
    open_sphere(path)
    _berka_state_snapshot = dict(_state)
    yield path
    for k in list(_state.keys()):
        _state[k] = None


@pytest.fixture
def open_berka_full_sphere(open_berka_sphere):
    """Open the real full Berka benchmark sphere for one test.

    The benchmark data is gitignored and never shipped; skip when absent.
    Reopens the synthetic sphere afterwards so later tests see it again.
    """
    global _berka_state_snapshot
    if not (_BERKA_FULL_SPHERE / "_gds_meta" / "sphere.json").exists():
        pytest.skip(f"full Berka benchmark sphere unavailable at {_BERKA_FULL_SPHERE}")
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    open_sphere(str(_BERKA_FULL_SPHERE))
    yield str(_BERKA_FULL_SPHERE)
    open_sphere(open_berka_sphere)
    _berka_state_snapshot = dict(_state)


@pytest.fixture
def writable_berka_sphere(open_berka_sphere, tmp_path):
    """A private copy of the synthetic sphere for tests that write to it.

    The test opens it itself; afterwards the shared synthetic sphere is
    reopened so later tests do not run against the mutated copy.
    """
    global _berka_state_snapshot
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    dst = tmp_path / "berka_copy"
    shutil.copytree(open_berka_sphere, dst)
    yield str(dst)
    open_sphere(open_berka_sphere)
    _berka_state_snapshot = dict(_state)


@pytest.fixture
def reopening_berka_sphere(open_berka_sphere):
    """The synthetic sphere path for tests that reopen it (closing the shared session).

    Afterwards the sphere is opened again and the restore snapshot re-taken, so
    later tests never get the closed session back.
    """
    global _berka_state_snapshot
    from hypertopos_mcp.server import _state
    from hypertopos_mcp.tools.session import open_sphere

    yield open_berka_sphere
    open_sphere(open_berka_sphere)
    _berka_state_snapshot = dict(_state)


@pytest.fixture(autouse=True)
def _restore_berka_state():
    """Restore _state after tests that replace it with mocks."""
    yield
    if _berka_state_snapshot is not None:
        from hypertopos_mcp.server import _state

        # A test that clears only the navigator (keeping the sphere) must not
        # leave later tests with a sphere and no navigator.
        missing = _state.get("sphere") is None or _state.get("navigator") is None
        if missing and _berka_state_snapshot.get("sphere") is not None:
            _state.update(_berka_state_snapshot)
