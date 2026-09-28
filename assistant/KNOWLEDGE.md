# CYBER CIV — Assistant Knowledge Base

> Factual reference for the CYBER CIV assistant. Do not invent facts beyond
> what is written here. If asked something not covered, say so and offer
> what is known.

## What it is
CYBER CIV is a persistent multi-agent simulation by **Paris Pehlivanovic**
(© 2026, all rights reserved). Four AI civilizations with distinct
personalities perceive world events, learn, form alliances, and compete —
while one of them, GOV, governs the others with real powers and can be
voted out of power.

## The civilizations
- **RED** — the pessimist. Sees risk everywhere (+0.10 bias), adapts fast
  (learning rate 0.07), strikes hard.
- **BLUE** — the optimist. Underestimates risk (−0.10 bias), adapts slowly
  (learning rate 0.04), endures.
- **GRAY** — the pragmatist. Reads the world straight (0.00 bias), adapts in
  the middle (learning rate 0.06).
- **GOV** — the sovereign regulator (+0.05 bias, learning rate 0.05).
  Governs the other three at a cost to its own intent.

## How a generation works
1. **Event** — a risk scenario is drawn from the crypto, banking, network, or
   governance worlds.
2. **Perception** — each civ perceives the risk through its own bias plus
   noise. GOV reads governance events clearly; the rest guess.
3. **Decision** — intent updates from perceived risk: EXPAND, STABILIZE, or
   EXPLORE.
4. **Governance** — GOV may issue a directive (each costs 0.02 intent).
   Pending mandates override decisions.
5. **Alliances** — civs with near-identical intent ally and refuse to fight.
6. **Competition** — probabilistic contests favor higher intent, but upsets,
   sanctions, and vetoes happen.
7. **The world turns** — pressure drifts with average intent; extremes trigger
   collapse and rebirth. Every 25 generations: election.

## Governance powers
| Power | Effect | Cost / limit |
|---|---|---|
| Mandate | Orders an overheated civ to STABILIZE next generation | 0.02 intent |
| Sanction | Halves a runaway winner's odds for 2 generations | 0.02 intent |
| Veto | Overturns a runaway leader's match win | 10-generation cooldown |
| Election | Every 25 generations the civs vote; majority ousts GOV for 10 generations | automatic |

## Verified balance (40 runs × 30 generations, 0 failures)
RED 52.5% · GOV 51.4% · BLUE 47.7% · GRAY 47.4%. No civ dominates.

## Running it
- Stdlib-only Python, no dependencies: `python3 cyber_civ.py`
- Reproducible: `python3 cyber_civ.py --generations 100 --seed 7`
- LLM-driven civs: `python3 cyber_civ_llm.py --generations 20`
  (needs `CYBER_CIV_API_KEY`; any OpenAI-compatible endpoint)
- Browser: the live dashboard runs the full simulation in-page, no server.

## Links
- Website: https://pehlivanovicparis-cloud.github.io/cyber-civ/
- Live demo: https://pehlivanovicparis-cloud.github.io/cyber-civ/demo.html
- Assistant: https://pehlivanovicparis-cloud.github.io/cyber-civ/assistant.html
- Repository: https://github.com/pehlivanovicparis-cloud/cyber-civ
- Licensing contact: Pehlivanovicparis@gmail.com

## License (summary)
Proprietary, all rights reserved. You may read the code; copying,
modification, distribution, or use requires prior written permission from
Paris Pehlivanovic. Commercial, governmental, institutional, or endorsement
use requires a paid written agreement. This is NOT open source — never
suggest MIT/Apache/GPL relicensing.

## Pricing guidance (starting points, always confirm with Paris)
- Indie / small commercial: $250–500 one-time
- Company / enterprise: $2,500–10,000 per year
- Government / institutional / endorsement: $10,000+, custom quote only
