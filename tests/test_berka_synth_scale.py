# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""The synthetic Berka generator scales without changing the default fixture."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

_GENERATE = Path(__file__).parent / "fixtures" / "berka_synth" / "generate.py"


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("berka_synth_generate", _GENERATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(data_dir: Path) -> dict[str, object]:
    return {p.stem: pq.read_table(p) for p in sorted(data_dir.glob("*.parquet"))}


def test_scale_one_is_unchanged(gen: ModuleType, tmp_path: Path) -> None:
    default_dir = tmp_path / "default"
    scaled_dir = tmp_path / "scale1"
    gen.generate(default_dir)
    gen.generate(scaled_dir, scale=1)
    default_tables = _tables(default_dir)
    scaled_tables = _tables(scaled_dir)
    assert default_tables.keys() == scaled_tables.keys()
    for name, table in default_tables.items():
        assert table.equals(scaled_tables[name]), name


def test_scale_multiplies_entities(gen: ModuleType, tmp_path: Path) -> None:
    base = _tables(_generate(gen, tmp_path / "base", scale=1))
    big = _tables(_generate(gen, tmp_path / "big", scale=3))
    keys = big["accounts"].column("primary_key").to_pylist()
    assert len(keys) == 363
    assert {"1", "2", "3", "741"} <= set(keys)
    assert {str(k) for k in range(1000, 1242)} <= set(keys)
    assert big["transactions"].num_rows >= 2.5 * base["transactions"].num_rows

    # The original 121 accounts and their transactions are unchanged by scaling
    # (transaction ids shift because extra rows interleave in date order).
    originals = pa.array(gen.ACCOUNT_KEYS)
    big_accounts = big["accounts"].filter(pc.is_in(big["accounts"]["primary_key"], originals))
    assert big_accounts.equals(base["accounts"])
    base_tx = base["transactions"].drop_columns(["primary_key"])
    big_tx = big["transactions"].filter(pc.is_in(big["transactions"]["account_id"], originals))
    assert big_tx.drop_columns(["primary_key"]).equals(base_tx)


def _generate(gen: ModuleType, data_dir: Path, *, scale: int) -> Path:
    gen.generate(data_dir, scale=scale)
    return data_dir
