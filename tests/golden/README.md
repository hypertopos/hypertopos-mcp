# Golden outputs (hypertopos-mcp)

Canonical JSON snapshots of the JSON returned by the main MCP tools, checked
by `tests/test_golden_mcp.py`. They guard refactors that must not change
behaviour: a golden diff is either an intended change or a regression.

**Rule: every golden diff in a PR must be explained in the PR description
(what changed and why it is intended). An unexplained golden diff is a
regression.**

## What is covered

All snapshots run on the synthetic Berka-shaped sphere (`open_berka_sphere`
fixture, generated from seeded rows and built by the builder from
`tests/fixtures/berka_synth/sphere.yaml`), so builder output is under test
too. The module reopens the sphere and calls `sphere_overview` first, as an
agent would. It opens the sphere with `response_format="full"`, so the snapshots
cover every value, including the subtrees the default compact format omits
(positional `delta` vectors, numeric properties columns).

| File | Tool call |
|------|-----------|
| `calibration.json` | `sphere.json` per pattern: `mu`, `sigma_diag`, `theta`, `population_size`, `relations` |
| `sphere_overview.json` | `sphere_overview()` |
| `find_anomalies_anchor.json` | `find_anomalies("account_behavior_pattern", top_n=10)` |
| `find_anomalies_event.json` | `find_anomalies("tx_pattern", top_n=5)` |
| `explain_anomaly.json` | `explain_anomaly` for account `85` |
| `passive_scan.json` | `passive_scan("accounts", threshold=1)` |
| `find_drifting_entities.json` | `find_drifting_entities("account_behavior_pattern", top_n=5)` |
| `aggregate.json` | `aggregate`: count by account, `sum:amount` by tx type, `avg:amount` by account frequency |
| `find_counterparties.json` | `find_counterparties` for account `85` (account to bank, `top_n=4`: the cut falls inside a `tx_count` tie) |
| `extract_chains_disjoint_hint.json` | `extract_chains` on account to bank: no chains, the disjoint-namespace hint |
| `check_alerts.json` | `check_alerts()` |
| `investigate_entity.json` | `investigate_entity` for account `85` (chains section off) |

Chain extraction and the chain primitives are covered in hypertopos-py's
golden tests.

## Updating

```bash
HYPERTOPOS_UPDATE_GOLDEN=1 .venv/Scripts/python -m pytest packages/hypertopos-mcp/tests/test_golden_mcp.py
```

Add `::test_name` to regenerate one file. Review the resulting `git diff` of
`tests/golden/` and explain it in the PR. On a mismatch the failing test
prints the differing JSON paths (first 20) and the exact command for that
test. The synthetic sphere is cached under `tests/fixtures/gds/berka_synth/`
(gitignored); delete it to force a rebuild after a builder change.

## Normalisation

The snapshot is strict about structure and lenient only about float noise:

- JSON is written with sorted keys, 2-space indent, ASCII escapes, `\n` line
  endings. Comparison is on the parsed documents, not bytes.
- **List order is kept**: ranking order is part of the contract.
- Dict keys, strings, ints, bools and `null` must match exactly, including
  type (an int turning into a float is a diff).
- Floats are stored rounded to 10 significant digits. That keeps a float32
  value (about 7 significant digits) intact and drops float64 summation-order
  noise. Floats are compared with a relative tolerance of `1e-6` (absolute
  `1e-9`), which absorbs SIMD / BLAS / platform differences around the 7th
  digit while still catching any real change.
- Many tools already round their output (often to 4 decimals). A golden float
  printed with at most 6 decimals is treated as already rounded; a difference
  of one unit in its last decimal is accepted (a value sitting on a rounding
  boundary can flip between platforms). Because trailing zeros are not
  printed (`0.0500` is stored as `0.05`), that unit is never coarser than
  `1e-4`.
- NaN and infinities are stored as the strings `"NaN"`, `"Infinity"`,
  `"-Infinity"`; `-0.0` becomes `0.0`.
- Volatile keys are dropped at any depth: `elapsed_ms`, `elapsed_s`,
  `elapsed_seconds`, `computed_at`, `created_at`, `updated_at`,
  `last_refresh_at`, `built_at`, `generated_at` (wall-clock timings and
  build/refresh timestamps). Data timestamps are kept.
- Absolute sphere paths inside strings are replaced by `<SPHERE>`.

Helpers live in `tests/_golden_mcp.py`.
