#!/usr/bin/env python3
"""
verify_balance.py — re-verify core balance after the Phase 6 "hands" change.

Runs 40 x 30 generations fully scripted in isolated temp dirs (never touches
the real persistent world), then reports failures, win rates, and collapses.

Pass criteria (same bar as the original verification):
  * 0 run failures
  * no civilization dominates: max-min win-rate spread <= 15pp

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved. PROPRIETARY.
"""
import contextlib
import io
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cyber_civ as core

RUNS = 40
GENS = 30
MAX_SPREAD = 0.15


def one_run(seed: int) -> dict:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        state = core.load_state(td / "store.json")  # fresh state
        state["generation"] = state.get("generation", 0) + 1
        rng = random.Random(1000 + seed)
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(GENS):
                core.run_generation(state, rng, 0, td / "store.json",
                                    td / "history.jsonl")
        return state


def main() -> int:
    failures = 0
    wins = {n: 0 for n in core.SWARMS}
    matches = {n: 0 for n in core.SWARMS}
    collapses = 0
    advanced_used = 0
    for seed in range(RUNS):
        try:
            state = one_run(seed)
        except Exception as exc:  # noqa: BLE001 — a failure is the finding
            failures += 1
            print(f"run {seed} FAILED: {exc!r}")
            continue
        collapses += state.get("collapses", 0)
        for n in core.SWARMS:
            wins[n] += state["swarms"][n]["wins"]
            matches[n] += state["swarms"][n]["total_matches"]
            for a in core.ADVANCED_ACTIONS:
                advanced_used += state["swarms"][n]["action_counts"].get(a, 0)

    print(f"\n== balance verification: {RUNS} runs x {GENS} generations (scripted) ==")
    print(f"run failures: {failures}/{RUNS}")
    rates = {}
    for n in core.SWARMS:
        wr = wins[n] / matches[n] if matches[n] else 0.0
        rates[n] = wr
        print(f"  {n:6} win rate {wr:.1%} over {matches[n]} matches")
    print(f"total collapses: {collapses}")
    print(f"advanced actions used (scripted heuristic): {advanced_used}")
    spread = max(rates.values()) - min(rates.values()) if rates else 1.0
    print(f"win-rate spread: {spread:.1%} (bar: <= {MAX_SPREAD:.0%})")

    ok = failures == 0 and spread <= MAX_SPREAD
    print("RESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
