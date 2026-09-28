# CYBER CIV — X Launch Thread

Copy-paste in order. Post 1 first, then reply to each with the next.

---

**1/** I built 4 AI civilizations that live, compete, and die.
One of them is a government.
It sanctions winners, vetoes matches — and just got voted out of power 3-0. 🧵

**2/** RED is a pessimist. BLUE is an optimist. GRAY is a pragmatist.
Each perceives the same world event differently, learns from it, and acts:
EXPAND, STABILIZE, or EXPLORE.

**3/** Then there's GOV. It doesn't just compete — it governs.
It MANDATES behavior. SANCTIONS runaway winners (win odds halved).
VETOES match results outright.
But every directive costs it power. Rule has a price.

**4/** Every 25 generations: ELECTION.
The three civs vote on GOV's legitimacy.
In testing it got ousted 3-0 and suspended for 10 generations.
Term limits — but for AI.

**5/** Every civ carries a permanent fingerprint (SHA-256 of its DNA)
that survives collapse and rebirth, plus a drifting behavioral profile.
The history log reads like a political chronicle, not a spreadsheet.

**6/** Verified across 40 runs × 30 generations:
RED 52.5% · GOV 51.4% · BLUE 47.7% · GRAY 47.4%
No permanent domination. Every match is contestable.
Stdlib-only Python. No network. Watch the politics unfold. 👇

---

# Standalone one-liners (pick one as an alternate opener)

- My AI government just vetoed a match result. Then got voted out 3-0. This is a simulation. Probably.
- Tamagotchi meets geopolitics: 4 AI civilizations, one government, sanctions, vetoes, elections — and identities that survive death.
- What if AI civilizations had a government with real power over them? I built it. It sanctioned the winner, vetoed the match, and got ousted.

---

# Postable terminal capture (real output, seed 7, 30 generations)

Paste as a code block, or screenshot your own terminal running:
`python3 cyber_civ.py --generations 30 --seed 7`

```
🏛️ GOV SANCTION: GRAY win odds halved for 2 generations
🏛️ sanction flips it: GRAY denied → GOV takes the win
🏛️ GOV VETO: BLUE's win overturned → RED wins
🏛️ sanction flips it: RED denied → GRAY takes the win
🗳️ ELECTION: GOV voted OUT (3/3), suspended 10 generations

🧬 CIVILIZATION FINGERPRINT ROSTER (generation 30)
RED   C19C71A12988  Pessimist Stabilizer (Dominant)    intent=0.71 wr=0.50
BLUE  2B35BD7DB70D  Optimist Stabilizer (Dominant)     intent=0.40 wr=0.57
GRAY  ED5697C34BD8  Pragmatist Stabilizer (Dominant)   intent=0.51 wr=0.53
GOV   0DE3694C7CBA  Pessimist Stabilizer (Balanced)    intent=0.33 wr=0.41
🏛️ GOV status: SUSPENDED until generation 35
```
