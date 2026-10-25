# Copyright (C) 2026 Karol Kędzia
# SPDX-License-Identifier: Apache-2.0
"""detect_pattern merges step results in plan order, not thread completion order."""

from __future__ import annotations

import time


def test_independent_steps_merge_in_plan_order() -> None:
    from hypertopos_mcp.tools.smart import _run_independent_steps

    def run_step(step: dict) -> tuple[str, dict]:
        time.sleep(step["delay"])
        return step["name"], {"results": [step["name"]]}

    steps = [
        {"name": "slow", "delay": 0.2},
        {"name": "fast", "delay": 0.0},
        {"name": "medium", "delay": 0.1},
    ]
    assert list(_run_independent_steps(steps, run_step)) == ["slow", "fast", "medium"]
