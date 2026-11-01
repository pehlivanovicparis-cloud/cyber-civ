#!/usr/bin/env python3
"""Recovery-fallback checks for the Aegis economy (scripted, no Ollama).

Two scripted runs, seed 31, fresh state each:
  1. Normal (RECOVER_P=0.6): Aegis RECOVERs still work, and the fallback
     never fires in a generation where a RECOVER succeeded.
  2. Aegis disabled (RECOVER_P=0.0): no RECOVER can succeed, so any loss
     closed was closed by the engine fallback.

Invariants checked in both runs: recovered <= frozen per loss; exact
accounting — treasury + pool equals the initial 5.0 minus the total
stolen (the full theft amount leaves the system at theft time; the pool
reimburses victims up to the frozen share out of levy money, so levy and
recovery are transfers, not new money). The pool never reaches its 0.5 cap
in 15 generations, so no levy is discarded by the cap and the check is
exact.

Usage: python3 test_recovery_fallback.py   (exit 0 = pass)
"""
import contextlib
import io
import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cyber_civ as core


def run_scenario(recover_p: float, gens: int = 15, seed: int = 31):
    """Run `gens` scripted generations; return (history records, final state)."""
    core.RECOVER_P = recover_p
    tmp = Path(tempfile.mkdtemp(prefix="aegis-fallback-"))
    state = core.default_state()
    rng = random.Random(seed)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(gens):
            core.run_generation(state, rng, 0.0,
                                tmp / "state.json", tmp / "history.jsonl")
    records = [json.loads(line) for line in
               (tmp / "history.jsonl").read_text().splitlines()]
    return records, state


def check_invariants(records, state, label: str) -> dict:
    """Assert recovered <= frozen and exact treasury+pool accounting.

    A theft removes the FULL amount from the system at theft time — the
    frozen share is a reimbursement cap recorded in the ledger, not money
    held in escrow. The pool pays victims out of levy money, so levy and
    recovery are pure transfers: treasury + pool == 5.0 - total stolen.
    """
    for rec in records:
        for loss in rec["loss_ledger"]:
            assert loss["recovered"] <= loss["frozen"] + 1e-9, (
                f"{label}: loss #{loss['id']} recovered {loss['recovered']} "
                f"> frozen {loss['frozen']} (gen {rec['generation']})")
    treasuries = sum(state["swarms"][n]["treasury"] for n in core.SWARMS)
    pool = state["recovery_pool"]
    stolen = sum(l["amount"] for l in state["loss_ledger"])
    expected = 5.0 - stolen
    assert abs(treasuries + pool - expected) < 0.002, (
        f"{label}: treasury+pool {treasuries + pool:.3f} "
        f"!= expected {expected:.3f} (total stolen {stolen:.3f})")
    max_pool = max(r["recovery_pool"] for r in records)
    assert max_pool < core.POOL_CAP, f"{label}: pool hit the cap ({max_pool})"
    assert pool >= -1e-9 and treasuries >= 0, f"{label}: negative balance"
    return {"treasuries": round(treasuries, 3), "pool": round(pool, 3),
            "stolen": round(stolen, 3), "max_pool": max_pool}


def main() -> int:
    try:
        # Scenario 1: Aegis on — RECOVERs still work, fallback stays out of
        # the way when a RECOVER succeeds.
        rec1, state1 = run_scenario(0.6)
        recovers = [ae for r in rec1 for ae in r["advanced_actions"]
                    if ae["action"] == "RECOVER" and ae["ok"]]
        fallbacks1 = [r["recovery_fallback"] for r in rec1
                      if r["recovery_fallback"]]
        for r in rec1:
            ok_recover = any(ae["action"] == "RECOVER" and ae["ok"]
                             for ae in r["advanced_actions"])
            assert not (ok_recover and r["recovery_fallback"]), (
                f"normal run: fallback fired in gen {r['generation']} "
                "alongside a successful RECOVER")
        inv1 = check_invariants(rec1, state1, "normal run")
        print(f"scenario 1 (Aegis on): thefts={len(state1['loss_ledger'])} "
              f"RECOVERs={len(recovers)} fallbacks={len(fallbacks1)} "
              f"invariants OK {inv1}")
        assert recovers, "normal run: Aegis performed zero successful RECOVERs"

        # Scenario 2: Aegis off — every closure is the fallback's work.
        rec2, state2 = run_scenario(0.0)
        recovers2 = [ae for r in rec2 for ae in r["advanced_actions"]
                     if ae["action"] == "RECOVER" and ae["ok"]]
        fallbacks2 = [r["recovery_fallback"] for r in rec2
                      if r["recovery_fallback"]]
        closed2 = [l for l in state2["loss_ledger"] if l["status"] == "closed"]
        inv2 = check_invariants(rec2, state2, "Aegis-off run")
        print(f"scenario 2 (Aegis off): thefts={len(state2['loss_ledger'])} "
              f"RECOVERs={len(recovers2)} fallbacks={len(fallbacks2)} "
              f"losses closed by fallback={len(closed2)} invariants OK {inv2}")
        assert not recovers2, "Aegis-off run: a RECOVER succeeded unexpectedly"
        assert fallbacks2, "Aegis-off run: fallback never fired"
        assert closed2, ("Aegis-off run: fallback fired but closed no loss "
                         f"(fired {len(fallbacks2)}x)")
        print("PASS: fallback works; Aegis RECOVERs unaffected; invariants hold")
        return 0
    finally:
        core.RECOVER_P = 0.6


if __name__ == "__main__":
    raise SystemExit(main())
