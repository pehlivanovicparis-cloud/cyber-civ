# CYBER CIV

**Persistent Evolving Cyber Civilization** — four AI civilizations with distinct
personalities perceive world events, learn, form alliances, and compete across
generations. One of them is a government. It sanctions, vetoes, and can be voted
out of power.

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved. See [LICENSE](LICENSE).

---

## What is this?

Three digital civilizations — RED (pessimist), BLUE (optimist), GRAY (pragmatist) —
plus GOV, a sovereign regulator that governs them. Every generation they perceive
risk events, update their intent, choose actions (EXPAND / STABILIZE / EXPLORE),
ally with the like-minded, and face off in probabilistic competition.

Each civilization carries two identities:

- a **fingerprint** — a stable SHA-256 ID derived from its DNA that survives
  collapse, rebirth, and everything in between;
- a **behavioral profile** — a drifting label like *Pessimist Stabilizer
  (Dominant)* built from win rate, volatility, and dominant action.

When world pressure hits an extreme, the world collapses and every civilization
is reborn — intents reset, DNA preserved. Nothing is forgotten: every generation
is appended to an immutable history log.

## The civilizations

| Civ | Personality | Bias | Learning rate |
|-----|-------------|------|---------------|
| RED | Pessimist, fast adapter | +0.10 | 0.07 |
| BLUE | Optimist, slow adapter | −0.10 | 0.04 |
| GRAY | Pragmatist, medium adapter | 0.00 | 0.06 |
| GOV | Regulator: sees risk everywhere | +0.05 | 0.05 |

## How a generation works

1. **Event** — a risk scenario is drawn from the crypto, banking, network, or
   governance worlds, across the US, China, Nigeria, or Germany.
2. **Perception** — each civ perceives the risk through its own bias plus noise.
   GOV reads governance-world events clearly; everyone else guesses.
3. **Decision** — intent updates from perceived risk; the civ acts: EXPAND
   (intent > 0.70), STABILIZE (> 0.40), or EXPLORE.
4. **Governance** — GOV may issue one directive (mandate / sanction), each
   costing it intent. Pending mandates override this generation's decisions.
5. **Alliances** — civs with near-identical intent ally and refuse to fight
   each other.
6. **Competition** — two non-allied civs face off. Higher intent is favored
   (Bradley-Terry), but upsets happen. Sanctions halve a target's odds; GOV
   can veto a runaway leader's win.
7. **World update** — global pressure drifts with average intent; extremes
   trigger collapse and rebirth. Every 25 generations, an election is held:
   the swarms can vote GOV out of power for 10 generations.

## Governance powers

| Power | Effect | Cost / limit |
|-------|--------|--------------|
| Mandate | Order an overheated civ (intent > 0.70) to STABILIZE next generation | 0.02 intent |
| Sanction | Halve a runaway winner's (win rate > 0.55) odds for 2 generations | 0.02 intent |
| Veto | Overturn a runaway leader's match win | 10-generation cooldown |
| Election | Swarms vote every 25 generations; majority ousts GOV for 10 generations | automatic |

## Verified balance

40 runs × 30 generations, zero failures:

| Civ | Win rate |
|-----|----------|
| RED | 52.5% |
| GOV | 51.4% |
| BLUE | 47.7% |
| GRAY | 47.4% |

No civ dominates; every match is contestable. Personalities stay distinct —
parity comes from the contest design, not from flattening the characters.

## Quickstart

```bash
python3 cyber_civ.py                          # 20 generations
python3 cyber_civ.py --generations 100 --seed 7
python3 cyber_civ.py --delay 0 --generations 200
python3 cyber_civ.py --fingerprints           # print the identity roster only
python3 cyber_civ.py --reset                  # wipe state and start over
```

No dependencies beyond the Python standard library. No network access.
State lives in `memory/store.json`; full history in `memory/history.jsonl`.

### LLM-driven civilizations (Phase 5)

Give each civilization a real language-model brain — perception and actions
come from distinct personas, while alliances, competition, sanctions, vetoes,
and elections still run on the verified core engine:

```bash
export CYBER_CIV_API_KEY="..."          # any OpenAI-compatible endpoint
python3 cyber_civ_llm.py --generations 20 --seed 7
python3 cyber_civ_llm.py --check        # one test API call
python3 cyber_civ_llm.py --dry-run --generations 3   # preview prompts, no API
```

Any API failure falls back to the scripted behavior, so the simulation never
stalls. See `assistant/` for CIV-GUIDE, the project's AI assistant
(system prompt + knowledge base + web chat at `/assistant.html`).

Example output:

```
🏛️ GOV SANCTION: RED win odds halved for 2 generations
🏆 RED vs BLUE → winner BLUE | 🌍 pressure 0.512
🏛️ GOV VETO: RED's win overturned → BLUE wins
🗳️ ELECTION: GOV voted OUT (3/3), suspended 10 generations

🧬 CIVILIZATION FINGERPRINT ROSTER (generation 30)
RED   7B5FEA3A327B  Pessimist Stabilizer (Balanced)   intent=0.62 wr=0.52
BLUE  3A992A32797C  Optimist Stabilizer (Balanced)    intent=0.51 wr=0.48
GRAY  BCBE2B399F26  Pragmatist Stabilizer (Balanced)  intent=0.57 wr=0.47
GOV   B644F5AB7AED  Pessimist Stabilizer (Balanced)   intent=0.55 wr=0.51
🏛️ GOV status: IN POWER
```

## Project structure

```
cyber_civ.py    # the simulation (stdlib only)
cyber_civ_llm.py # LLM-driven civilizations (Phase 5, stdlib + any OpenAI-compatible API)
assistant/      # CIV-GUIDE assistant: system prompt, knowledge base, deploy guide
dashboard.html  # live web dashboard — open in any browser, no server needed
docs/           # public website (GitHub Pages): index, demo, assistant chat
LICENSE         # proprietary license — all rights reserved
README.md       # this file
launch-kit-x.md # X/Twitter launch thread
memory/         # runtime state (gitignored): store.json, history.jsonl, fingerprints.json
```

## License

Proprietary. Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
No copying, modification, distribution, or use without prior written permission.
Commercial, governmental, or institutional use — including endorsement —
requires a paid license agreement. Contact: Pehlivanovicparis@gmail.com
