# Aegis — fifth Cyber CIV civilization (recovery agent)

**Status:** PROTOTYPE, built 2026-11-01 in this separate copy (`~/workspace/cyber-civ-aegis`).
**NOT merged** into the live repo. The live showcase tree (`~/workspace/cyber_civ`) was
never touched; the batch driver kept running there throughout. **Do not merge**
without Paris's explicit word (commit + push is a publishing decision, same as Arbiter).

## Design (Paris picked "Fifth civ", 2026-11-01 ~12:10 EST)

Aegis is a full fifth civilization, not a system agent: it competes in matches,
forms alliances, votes in elections, and keeps a memory journal like the other four.
Name is provisional — Paris can rename (the other four were renamed by him in Sep 2026).

- **Doctrine:** Custodian. Restores stolen funds to victims from a shared pool.
- **Personality:** bias -0.05, learning_rate 0.05 (calm, steady). Home-turf edge:
  reads crypto/banking threats clearly (noise halved), mirroring Archon's
  governance edge.
- **Persona (LLM):** "You are Aegis, the custodian civilization... you restore the
  frozen share from the recovery pool... you never take funds for yourself."

## Mechanics (all in `cyber_civ.py`, `cyber_civ_llm.py`)

- **Treasury:** new per-civ variable (starts 1.0). Intent is unchanged as the
  political currency; treasury is the funds ledger.
- **Loss ledger:** a significant theft/breach event (world crypto/banking,
  risk >= 0.6) steals from the richest non-Archon, non-Aegis civ:
  amount = 0.05 + risk * 0.10, capped at the victim's treasury. The frozen
  share (uniform 40–60%, mirroring the real Binance theft: ~$3B of $6.3B frozen)
  is recoverable; the rest is gone for good.
- **Levy:** every civ pays 0.005 treasury per generation into the recovery pool
  (cap 0.5). Insurance model: everyone pays in, victims get reimbursed.
- **RECOVER action:** new advanced action (cost 0.01 intent, 4-gen cooldown,
  max 0.10 treasury per recovery). Aegis only — anyone else proposing it is
  rejected. Targets a victim; restores the oldest open loss's frozen remainder
  from the pool. Fails closed: no open loss, empty pool, or nothing recoverable
  all fall back to STABILIZE. Recovery can never exceed recorded frozen funds.
- **Recovery fallback (engine-level):** if no RECOVER succeeded in a
  generation, the engine itself reimburses the oldest open loss from the pool,
  capped at 0.05 treasury per generation (`FALLBACK_MAX`, `apply_recovery_fallback`
  in `cyber_civ.py`). No actor, no intent cost, no cooldown — the placeholder
  the recovery falls back on when Aegis is on cooldown, fails its roll, or its
  LLM call fails. Logged in history as `recovery_fallback` (type
  `RECOVERY_FALLBACK`, distinct from an Aegis RECOVER) and printed as 🛟 FALLBACK.
  Never fires in a generation where a RECOVER succeeded.
- **Scripted Aegis:** when an open loss exists and the pool can pay, attempts
  RECOVER with p=0.6, targeting the largest open frozen remainder.
- **Election:** ouster is now a strict majority of non-Archon voters (was >= 2
  of 3; same result for 4 civs, correct for 5). Denominator in the console
  print is dynamic.
- **LLM prompt:** every civ sees its treasury + the pool; Aegis also sees the
  open-loss list. RECOVER is in the hands prompt and the JSON schema. The
  directive schema now accepts Aegis as a mandate target.

## Test results (scripted, 15 gens, seed 31, 2026-11-01)

- No crash. Aegis present in all 15 generations; won a match (vs Ares).
- 4 thefts recorded (all hit Ares, the highest-intent target); 4 RECOVERs,
  all succeeded; all 4 losses fully closed (frozen shares restored).
- Invariants hold: recovered <= frozen per loss; treasury + pool = 5.0 minus
  total stolen (the full theft amount leaves the system at theft time — the
  frozen share is a reimbursement cap in the ledger, not money held in
  escrow; the pool pays victims out of levy money, so levy and recovery are
  transfers). Correction to the earlier wording: the 2026-11-01 note said
  "minus 0.54 destroyed" — that 0.54 was the total stolen in that run, not
  the non-frozen share.
- LLM dry-run (`--dry-run`, no Ollama): Aegis's prompt shows treasury, pool,
  open-loss line, RECOVER in hands + schema. Correct.
- Roster print works (Aegis: "Optimist Stabilizer (Dominant)", wr 0.75).

## Fallback test results (scripted, 15 gens, seed 31, `test_recovery_fallback.py`)

- Scenario 1, Aegis on: 3 thefts, 2 successful RECOVERs, 2 fallback payments
  (only in generations where no RECOVER succeeded — never alongside one).
  Invariants hold (treasury 4.425 + pool 0.180 = 5.0 − 0.395 stolen).
- Scenario 2, Aegis disabled (RECOVER_P=0.0): 3 thefts, 0 RECOVERs,
  4 fallback payments, **2 losses closed by the fallback alone**.
  Invariants hold (treasury 4.431 + pool 0.216 = 5.0 − 0.353 stolen).

## Still to do

- **LLM live test:** run a few generations with the 3B model after the showcase
  batch driver finishes (do NOT load Ollama concurrently — the driver is
  memory-fragile).
- **Spectator/dashboard:** `spectator.html` (live tree, local-only) and
  `dashboard.html` still list four civs. Update if Aegis ships.
- **Merge decision:** Paris's call. If it ships, the demo script ("four
  civilizations") and SUBMISSION.md ("Four civilizations, four doctrines")
  need updating, and the showcase would need a fresh run — the current 45-gen
  showcase is a four-civ system.
- **Balance:** thefts hit the highest-intent civ every time (Ares 4/4 in the
  test). Watch whether that makes Aegis a permanent Ares-nursemaid; the
  victim rule may want a random element.
