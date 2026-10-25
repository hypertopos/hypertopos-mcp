# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""Generate and build the synthetic Berka-shaped sphere used by the MCP tests.

Every row is drawn from a seeded numpy generator — no real dataset rows are
read or embedded. Only the *shape* (line / column names, dtypes, categorical
vocabularies, a 1993-1998 date range) follows the Berka benchmark sphere so
the MCP tools exercise the same code paths.

Guarantees the tests rely on:

* account keys ``"1"`` .. ``"120"`` plus ``"741"``; transaction keys
  ``TX-0000000`` .. in global date order;
* all three ``frequency`` values, every ``loan_status`` (A-D), a ``Prague``
  region, all six ``operation`` values (including the empty one);
* multi-year activity per account (many 90-day temporal slices), activity
  drift for a subset of accounts, and injected outliers in every frequency.

Usage::

    python generate.py <sphere_out_dir>
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SEED = 20_261_021
YAML_PATH = Path(__file__).with_name("sphere.yaml")

START = np.datetime64("1993-01-01", "D")
END = np.datetime64("1998-12-31", "D")
N_DAYS = int((END - START).astype(int)) + 1

ACCOUNT_KEYS = [str(i) for i in range(1, 121)] + ["741"]
FREQUENCIES = ("POPLATEK MESICNE", "POPLATEK TYDNE", "POPLATEK PO OBRATU")
LOAN_STATUSES = ("A", "B", "C", "D")
CARD_TYPES = ("classic", "junior", "gold")
REGIONS = ("Prague",) + tuple(f"Region-{i}" for i in range(2, 9))
N_DISTRICTS = 16
BANKS = tuple(f"BK{i:02d}" for i in range(1, 14))
TX_TYPES = ("PRIJEM", "VYDAJ", "VYBER")
OPERATIONS = ("PREVOD NA UCET", "PREVOD Z UCTU", "VKLAD", "VYBER", "VYBER KARTOU")
ORDER_K_SYMBOLS = ("SIPO", "UVER", " ", "POJISTNE", "LEASING")


def _districts() -> pa.Table:
    ids = [str(i) for i in range(1, N_DISTRICTS + 1)]
    # District 1 is the capital-region district; the rest cycle the other regions.
    regions = ["Prague"] + [REGIONS[1 + (i % 7)] for i in range(N_DISTRICTS - 1)]
    rng = np.random.default_rng(SEED + 1)
    return pa.table(
        {
            "primary_key": ids,
            "district_id": ids,
            "name": [f"District-{i:02d}" for i in range(1, N_DISTRICTS + 1)],
            "region": regions,
            "population": rng.integers(40_000, 400_000, N_DISTRICTS).astype(np.int64),
            "avg_salary": rng.integers(8_000, 12_500, N_DISTRICTS).astype(np.int64),
        }
    )


def _account_attrs(rng: np.random.Generator, keys: list[str]) -> dict[str, list[object]]:
    n = len(keys)
    # Frequency mix ~70/18/12 with every value well represented.
    freq_cycle = [0] * 7 + [1] * 2 + [2]
    freq = rng.permutation(np.resize(freq_cycle, n))
    # ~15% of accounts in the Prague district; account "741" is pinned there.
    district = np.where(rng.random(n) < 0.15, 1, rng.integers(2, N_DISTRICTS + 1, n))
    if "741" in keys:
        district[keys.index("741")] = 1
    # ~45% hold a loan; statuses cycle so A/B/C/D are all present.
    has_loan = rng.random(n) < 0.45
    status_cycle = ["A", "C", "A", "C", "B", "D"]
    loan_idx = np.flatnonzero(has_loan)
    statuses: list[str | None] = [None] * n
    for j, i in enumerate(loan_idx):
        statuses[i] = status_cycle[j % len(status_cycle)]
    has_card = rng.random(n) < 0.25
    return {
        "frequency": [FREQUENCIES[f] for f in freq],
        "district_id": [str(d) for d in district],
        "has_loan": ["yes" if h else None for h in has_loan],
        "loan_status": list(statuses),
        "has_card": ["yes" if c else None for c in has_card],
        "card_type": [CARD_TYPES[int(rng.integers(0, 3))] if c else None for c in has_card],
        "owner_gender": [("F", "M")[int(g)] for g in rng.integers(0, 2, n)],
        "owner_birth_year": list(rng.integers(1925, 1982, n).astype(np.int64)),
        "n_clients": list(np.where(rng.random(n) < 0.2, 2, 1).astype(np.int64)),
    }


def _account_transactions(
    rng: np.random.Generator,
    frequency: str,
    has_card: bool,
    loan_status: str | None,
    order_k: list[str],
    order_amounts: list[float],
    outlier: bool,
) -> list[tuple[int, str, str, float, str, str]]:
    """Return (day, type, operation, signed amount, k_symbol, bank) rows."""
    open_day = int(rng.integers(0, 900))
    months = np.arange(open_day, N_DAYS, 30)
    rows: list[tuple[int, str, str, float, str, str]] = []
    salary = float(rng.lognormal(9.2, 0.4))
    # Monthly / quarterly fixed flows.
    for m_i, d in enumerate(months):
        day = int(d)
        if m_i % 3 == 2:
            rows.append((day + 29, "PRIJEM", "", float(rng.uniform(20, 300)), "UROK", ""))
        if salary > 9_000:
            bank = BANKS[int(rng.integers(0, len(BANKS)))]
            rows.append((day + 3, "PRIJEM", "PREVOD Z UCTU", salary, "DUCHOD", bank))
            # Most of the income is spent again, keeping balances bounded.
            spend = -salary * float(rng.uniform(0.75, 1.05))
            rows.append((day + 6, "VYDAJ", "VYBER", spend, "", ""))
        for k_sym, amt in zip(order_k, order_amounts, strict=True):
            bank = BANKS[int(rng.integers(0, len(BANKS)))]
            rows.append((day + 10, "VYDAJ", "PREVOD NA UCET", -amt, k_sym, bank))
        if frequency == "POPLATEK TYDNE" and m_i % 2 == 0:
            rows.append((day + 28, "VYDAJ", "VYBER", -15.0, "SLUZBY", ""))
    # Random activity: Poisson rate per month, with a regime shift for ~1/3 of accounts.
    rate = float(rng.lognormal(0.3, 0.6))
    shift_at = int(rng.integers(open_day + 200, N_DAYS)) if rng.random() < 0.35 else N_DAYS
    shift_factor = float(rng.choice([0.25, 3.0]))
    for d in months:
        day = int(d)
        lam = rate * (shift_factor if day >= shift_at else 1.0)
        for _ in range(int(rng.poisson(lam))):
            t = day + int(rng.integers(0, 30))
            u = rng.random()
            if u < 0.35:
                rows.append((t, "PRIJEM", "VKLAD", float(rng.lognormal(8.5, 0.9)), "", ""))
            elif u < 0.75:
                rows.append((t, "VYDAJ", "VYBER", -float(rng.lognormal(8.0, 0.9)), "", ""))
            elif u < 0.85 and has_card:
                rows.append((t, "VYDAJ", "VYBER KARTOU", -float(rng.lognormal(7.0, 0.7)), "", ""))
            elif u < 0.92:
                bank = BANKS[int(rng.integers(0, len(BANKS)))]
                amt = -float(rng.lognormal(8.0, 0.8))
                rows.append((t, "VYDAJ", "PREVOD NA UCET", amt, " ", bank))
            elif u < 0.96:
                rows.append((t, "VYBER", "VYBER", -float(rng.lognormal(8.3, 0.6)), "", ""))
            else:
                bank = BANKS[int(rng.integers(0, len(BANKS)))]
                amt = float(rng.lognormal(8.2, 0.7))
                rows.append((t, "PRIJEM", "PREVOD Z UCTU", amt, "", bank))
    # Bad loans (B / D) accumulate penalty interest; a few others get one.
    n_penalty = int(rng.integers(3, 15)) if loan_status in ("B", "D") else int(rng.random() < 0.1)
    for _ in range(n_penalty):
        t = int(rng.integers(open_day + 60, N_DAYS))
        rows.append((t, "PRIJEM", "", float(rng.uniform(5, 60)), "SANKC. UROK", ""))
    if outlier:
        # One burst day of large cash withdrawals followed by a large deposit.
        t = int(rng.integers(open_day + 100, N_DAYS - 2))
        for _ in range(int(rng.integers(12, 20))):
            rows.append((t, "VYDAJ", "VYBER", -float(rng.lognormal(10.0, 0.3)), "", ""))
        rows.append((t + 1, "PRIJEM", "VKLAD", float(rng.lognormal(11.0, 0.3)), "", ""))
    # Opening deposit — every account starts with one.
    rows.append((open_day, "PRIJEM", "VKLAD", float(rng.uniform(500, 2_000)), "", ""))
    return [r for r in rows if r[0] < N_DAYS]


def _account_block(
    rng: np.random.Generator,
    keys: list[str],
    order_rows: list[tuple[str, str, str, float]],
    tx_rows: list[tuple[int, str, str, str, float, str, str]],
    per_account: dict[str, tuple[list[str], list[float]]],
) -> dict[str, list[object]]:
    """Draw attributes, standing orders and transactions for ``keys``."""
    attrs = _account_attrs(rng, keys)
    n = len(keys)

    # Two outlier accounts per frequency value.
    outliers: set[int] = set()
    for f in FREQUENCIES:
        idx = [i for i in range(n) if attrs["frequency"][i] == f]
        outliers.update(int(i) for i in rng.choice(idx, size=2, replace=False))

    for i, key in enumerate(keys):
        status = attrs["loan_status"][i]
        n_orders = int(rng.integers(0, 2)) + (1 if status is not None else 0)
        ks: list[str] = []
        amts: list[float] = []
        for o in range(n_orders):
            k_sym = "UVER" if (status is not None and o == 0) else str(rng.choice(ORDER_K_SYMBOLS))
            amt = round(float(rng.lognormal(7.5, 0.7)), 0)
            bank = BANKS[int(rng.integers(0, len(BANKS)))]
            order_rows.append((key, bank, k_sym, amt))
            ks.append(k_sym)
            amts.append(amt)
        per_account[key] = (ks, amts)
        for day, typ, op, amt, k_sym, bank in _account_transactions(
            rng,
            str(attrs["frequency"][i]),
            attrs["has_card"][i] is not None,
            None if status is None else str(status),
            ks,
            amts,
            i in outliers,
        ):
            tx_rows.append((day, key, typ, op, amt, k_sym, bank))
    # Loan terms are drawn from this block's generator so extra blocks never
    # shift the draws of the original accounts.
    attrs["loan_amount"] = [
        int(rng.integers(5_000, 400_000)) if s is not None else 0 for s in attrs["loan_status"]
    ]
    attrs["loan_duration"] = [
        int(rng.choice([12, 24, 36, 48, 60])) if s is not None else 0 for s in attrs["loan_status"]
    ]
    return attrs


def generate(data_dir: Path, scale: int = 1) -> None:
    """Write the synthetic source parquet files into ``data_dir``.

    ``scale=1`` is the test fixture. ``scale=N`` adds ``121 * (N - 1)`` further
    accounts (keys ``"1000"`` upward) drawn from a separate generator, so the
    original accounts and their rows are unchanged — used for build timing.
    """
    if scale < 1:
        raise ValueError(f"scale must be >= 1, got {scale}")
    data_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    order_rows: list[tuple[str, str, str, float]] = []
    tx_rows: list[tuple[int, str, str, str, float, str, str]] = []
    per_account: dict[str, tuple[list[str], list[float]]] = {}
    keys = list(ACCOUNT_KEYS)
    attrs = _account_block(rng, keys, order_rows, tx_rows, per_account)
    if scale > 1:
        extra = [str(1000 + i) for i in range(len(ACCOUNT_KEYS) * (scale - 1))]
        extra_attrs = _account_block(
            np.random.default_rng(SEED + 2), extra, order_rows, tx_rows, per_account
        )
        keys += extra
        attrs = {name: attrs[name] + extra_attrs[name] for name in attrs}

    # Global date order defines TX-0000000, TX-0000001, ...
    tx_rows.sort(key=lambda r: (r[0], int(r[1])))
    days = np.array([r[0] for r in tx_rows], dtype=np.int64)
    acc = np.array([r[1] for r in tx_rows])
    signed = np.round(np.array([r[4] for r in tx_rows]), 1)
    is_penalty = np.array([r[5] == "SANKC. UROK" for r in tx_rows])
    balance = np.zeros(len(tx_rows))
    stats: dict[str, dict[str, float]] = {}
    # One stable sort groups each account's rows in date order (O(n log n)
    # instead of one full scan per account).
    order = np.argsort(acc, kind="stable")
    group_keys, starts = np.unique(acc[order], return_index=True)
    bounds = list(starts[1:]) + [len(order)]
    for key, lo, hi in zip(group_keys.tolist(), starts.tolist(), bounds, strict=True):
        idx = order[lo:hi]
        bal = np.round(np.cumsum(signed[idx]), 1)
        balance[idx] = bal
        credits = signed[idx][signed[idx] > 0]
        slope = np.polyfit(days[idx] / 30.0, bal, 1)[0] if len(idx) > 2 else 0.0
        stats[key] = {
            "min_balance": float(bal.min()),
            "mean_balance": float(bal.mean()),
            "volatility": float(bal.std() / max(abs(bal.mean()), 1.0)),
            "trend": float(slope),
            "monthly_income": float(credits.sum()) / max((np.ptp(days[idx]) + 1) / 30.0, 1.0),
            "penalties": float(is_penalty[idx].sum()),
        }

    dates = (START + days.astype("timedelta64[D]")).astype(str)
    transactions = pa.table(
        {
            "primary_key": [f"TX-{j:07d}" for j in range(len(tx_rows))],
            "account_id": acc.tolist(),
            "date": dates.tolist(),
            "type": [r[2] for r in tx_rows],
            "operation": [r[3] for r in tx_rows],
            "amount": np.abs(signed),
            "balance": balance,
            "k_symbol": [r[5] for r in tx_rows],
            "bank": [r[6] for r in tx_rows],
        }
    )

    loan_amount = [int(v) for v in attrs["loan_amount"]]
    loan_duration = [int(v) for v in attrs["loan_duration"]]
    accounts = pa.table(
        {
            "primary_key": keys,
            "account_id": keys,
            "frequency": attrs["frequency"],
            "district_id": attrs["district_id"],
            "region": [
                "Prague" if d == "1" else REGIONS[1 + ((int(d) - 2) % 7)]
                for d in attrs["district_id"]
            ],
            "has_loan": pa.array(attrs["has_loan"], pa.string()),
            "loan_status": pa.array(attrs["loan_status"], pa.string()),
            "has_card": pa.array(attrs["has_card"], pa.string()),
            "card_type": pa.array(attrs["card_type"], pa.string()),
            "owner_gender": attrs["owner_gender"],
            "owner_birth_year": pa.array(attrs["owner_birth_year"], pa.int64()),
            "n_clients": pa.array(attrs["n_clients"], pa.int64()),
            "n_standing_orders": pa.array([len(per_account[k][0]) for k in keys], pa.int64()),
            "loan_amount": pa.array(loan_amount, pa.int64()),
            "loan_duration": pa.array(loan_duration, pa.int64()),
            "balance_to_loan": [
                stats[k]["mean_balance"] / la if la else 0.0
                for k, la in zip(keys, loan_amount, strict=True)
            ],
            "min_balance": [stats[k]["min_balance"] for k in keys],
            "balance_volatility": [stats[k]["volatility"] for k in keys],
            "income_coverage": [
                stats[k]["monthly_income"] / (la / ld) if la else 0.0
                for k, la, ld in zip(keys, loan_amount, loan_duration, strict=True)
            ],
            "balance_trend": [stats[k]["trend"] for k in keys],
            "penalty_interest_count": pa.array(
                [int(stats[k]["penalties"]) for k in keys], pa.int64()
            ),
        }
    )
    loan_mask = pa.array([s is not None for s in attrs["loan_status"]])

    orders = pa.table(
        {
            "primary_key": [f"ORD-{j:05d}" for j in range(1, len(order_rows) + 1)],
            "account_id": [r[0] for r in order_rows],
            "bank_to": [r[1] for r in order_rows],
            "k_symbol": [r[2] for r in order_rows],
            "amount": [r[3] for r in order_rows],
        }
    )

    tables = {
        "accounts": accounts,
        "loan_accounts": accounts.filter(loan_mask),
        "transactions": transactions,
        "orders": orders,
        "districts": _districts(),
        "tx_types": pa.table({"primary_key": list(TX_TYPES)}),
        "operations": pa.table({"primary_key": list(OPERATIONS)}),
        "cpty_banks": pa.table({"primary_key": list(BANKS)}),
    }
    for name, table in tables.items():
        pq.write_table(table, data_dir / f"{name}.parquet")


def build(sphere_dir: Path, scale: int = 1) -> None:
    """Generate the sources and build the sphere into ``sphere_dir``.

    The build runs in a sibling work directory and is renamed into place
    only on success, so an interrupted build never leaves a half sphere.
    Work paths stay short: Lance file paths are deep and Windows caps a
    path at 260 characters.
    """
    from hypertopos.builder import builder as builder_mod
    from hypertopos.cli.build import build_from_config
    from hypertopos.cli.schema import parse_config

    work = sphere_dir.with_name(f".{sphere_dir.name}.build")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        data_dir = work / "src"
        generate(data_dir, scale=scale)
        # Mirror the full benchmark sphere's regime: its transaction population
        # is far above the bootstrap ceiling, so the event pattern carries no
        # bootstrap confidence there — skip it here too (also the dominant
        # build cost at this scale). Anchor populations stay bootstrapped.
        saved = builder_mod._BOOTSTRAP_MAX_N
        builder_mod._BOOTSTRAP_MAX_N = 5_000
        try:
            out = work / "s"
            build_from_config(parse_config(YAML_PATH), out, source_dir=data_dir)
        finally:
            builder_mod._BOOTSTRAP_MAX_N = saved
        if sphere_dir.exists():
            shutil.rmtree(sphere_dir)
        out.rename(sphere_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python generate.py <sphere_out_dir>")
    build(Path(sys.argv[1]).resolve())
