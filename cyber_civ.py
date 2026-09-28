# Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
#
# PROPRIETARY — see LICENSE for terms. No copying, modification,
# distribution, or use without prior written permission.
# Commercial, governmental, or institutional use — including endorsement —
# requires a paid license agreement.

"""
cyber_civ.py — Persistent Evolving Cyber Civilization (toy simulation, Phase 3)

Features:
  * per-swarm perception + personality (swarms diverge)
  * emergent alliances (close-intent swarms cooperate)
  * long-term history log (memory/history.jsonl)
  * collapse & rebirth cycles at pressure extremes
  * civilization identity fingerprints: a stable DNA-based ID (survives
    rebirth) plus a behavioral profile that drifts over time

Phase 4 adds GOV, a sovereign regulator civilization: directive powers
(mandate / sanction / veto) that cost intent to wield, a governance world
with its own event flavors, and election cycles that can vote GOV out of
power.

No network access, no wallets, no external services — just JSON on disk.

Usage:
    python cyber_civ.py                          # 20 generations
    python cyber_civ.py --generations 100 --seed 7
    python cyber_civ.py --delay 0 --generations 200
    python cyber_civ.py --reset
    python cyber_civ.py --fingerprints          # print a fingerprint roster
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_STATE_FILE = Path("memory/store.json")
DEFAULT_HISTORY_FILE = Path("memory/history.jsonl")
DEFAULT_GENERATIONS = 20
FINGERPRINT_ROSTER_FILE = Path("memory/fingerprints.json")

SWARMS = ("RED", "BLUE", "GRAY", "GOV")
WORLDS = ("crypto", "banking", "network", "governance")
COUNTRIES = ("US", "CN", "NG", "DE")

EXPAND_BELOW = 0.70
STABILIZE_BELOW = 0.40
INTENT_LEARNING_RATE = 0.05
PRESSURE_LEARNING_RATE = 0.05
PERCEPTION_NOISE = 0.10
ALLIANCE_THRESHOLD = 0.08
COLLAPSE_HIGH = 0.98
COLLAPSE_LOW = 0.02
RECENT_INTENT_WINDOW = 12

# -- governance (GOV sovereign) -------------------------------------------
GOVERNANCE_FLAVORS = ("regulation", "election", "sanction", "treaty")
MANDATE_INTENT_ABOVE = 0.70   # GOV orders STABILIZE on overheated civs
SANCTION_WIN_RATE_ABOVE = 0.55
VETO_WIN_RATE_ABOVE = 0.55
VETO_COOLDOWN_GENS = 10
DIRECTIVE_COST = 0.02         # intent GOV spends per directive issued
ELECTION_EVERY = 25
GOV_SUSPENSION_GENS = 10

# Distinct starting personalities so fingerprints differ from generation 0.
PERSONALITY = {
    "RED": {"bias": 0.10, "learning_rate": 0.07},   # pessimist, fast adapter
    "BLUE": {"bias": -0.10, "learning_rate": 0.04}, # optimist, slow adapter
    "GRAY": {"bias": 0.0, "learning_rate": 0.06},   # pragmatist, medium adapter
    "GOV": {"bias": 0.05, "learning_rate": 0.05},   # regulator: sees risk, medium adapter
}

ACTIONS = ("EXPAND", "STABILIZE", "EXPLORE")


@dataclass
class Event:
    world: str
    country: str
    risk: float
    flavor: str = ""   # governance-world event flavor (regulation, election, ...)


@dataclass
class Civilization:
    name: str
    intent: float = 0.5
    wins: int = 0
    bias: float = 0.0
    learning_rate: float = INTENT_LEARNING_RATE
    allies: set[str] = field(default_factory=set)
    civ_seed: int = 0                 # stable DNA — survives rebirth
    action_counts: dict[str, int] = field(default_factory=lambda: {a: 0 for a in ACTIONS})
    total_matches: int = 0
    recent_intents: list[float] = field(default_factory=list)

    def perceive(self, event: Event, rng: random.Random) -> float:
        noise = PERCEPTION_NOISE
        if event.world == "governance":
            # Home turf: GOV reads regulation clearly; everyone else guesses.
            noise *= 0.5 if self.name == "GOV" else 1.5
        return max(0.0, min(1.0, event.risk + self.bias + rng.uniform(-noise, noise)))

    def decide_action(self) -> str:
        if self.intent > EXPAND_BELOW:
            return "EXPAND"
        if self.intent > STABILIZE_BELOW:
            return "STABILIZE"
        return "EXPLORE"

    def learn_from(self, perceived_risk: float) -> str:
        self.intent = max(0.0, min(1.0, self.intent + (perceived_risk - 0.5) * self.learning_rate))
        return self.decide_action()

    # -- identity ------------------------------------------------------
    def fingerprint(self) -> str:
        """Stable ID from persistent DNA (survives collapse/rebirth)."""
        dna = json.dumps(
            [self.name, self.civ_seed, round(self.bias, 4), round(self.learning_rate, 4)],
            sort_keys=True,
        ).encode()
        return hashlib.sha256(dna).hexdigest()[:12].upper()

    def dominant_action(self) -> str:
        if sum(self.action_counts.values()) == 0:
            return "EXPLORE"
        return max(ACTIONS, key=lambda a: self.action_counts[a])

    def win_rate(self) -> float:
        return self.wins / self.total_matches if self.total_matches else 0.0

    def volatility(self) -> float:
        if len(self.recent_intents) < 2:
            return 0.0
        mean = sum(self.recent_intents) / len(self.recent_intents)
        var = sum((x - mean) ** 2 for x in self.recent_intents) / len(self.recent_intents)
        return math.sqrt(var)

    def profile(self) -> str:
        """Drifting behavioral label (changes as the civilization behaves)."""
        bias_label = "Optimist" if self.bias < -0.03 else "Pessimist" if self.bias > 0.03 else "Pragmatist"
        action_label = {
            "EXPAND": "Expansionist",
            "STABILIZE": "Stabilizer",
            "EXPLORE": "Explorer",
        }[self.dominant_action()]
        vol_label = "Volatile " if self.volatility() > 0.08 else ""
        wr = self.win_rate()
        strength = "Dominant" if wr > 0.45 else "Declining" if wr < 0.25 else "Balanced"
        return f"{vol_label}{bias_label} {action_label} ({strength})"


# ---------------------------------------------------------------------------
# State + persistence
# ---------------------------------------------------------------------------

def default_state() -> dict[str, Any]:
    swarms = {}
    for n in SWARMS:
        p = PERSONALITY[n]
        swarms[n] = {
            "intent": 0.5,
            "wins": 0,
            "bias": p["bias"],
            "learning_rate": p["learning_rate"],
            "civ_seed": random.randint(0, 1_000_000_000),
            "action_counts": {a: 0 for a in ACTIONS},
            "total_matches": 0,
            "recent_intents": [],
        }
    return {
        "generation": 0,
        "world_pressure": 0.5,
        "swarms": swarms,
        "collapses": 0,
        "pending_mandates": {},
        "sanctions": {},
        "veto_cooldown": 0,
        "gov_suspended_until": 0,
    }


def _repair_swarm_entry(entry: dict[str, Any], name: str) -> dict[str, Any]:
    p = PERSONALITY[name]
    entry.setdefault("intent", 0.5)
    entry.setdefault("wins", 0)
    entry.setdefault("bias", p["bias"])
    entry.setdefault("learning_rate", p["learning_rate"])
    entry.setdefault("civ_seed", random.randint(0, 1_000_000_000))
    counts = entry.setdefault("action_counts", {})
    for a in ACTIONS:
        counts.setdefault(a, 0)
    entry.setdefault("total_matches", 0)
    entry.setdefault("recent_intents", [])
    return entry


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        with path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: state unreadable ({exc}); starting fresh", file=sys.stderr)
        return default_state()
    swarms = state.setdefault("swarms", {})
    for n in SWARMS:
        swarms[n] = _repair_swarm_entry(swarms.setdefault(n, {}), n)
    state.setdefault("generation", 0)
    state.setdefault("world_pressure", 0.5)
    state.setdefault("collapses", 0)
    state.setdefault("pending_mandates", {})
    state.setdefault("sanctions", {})
    state.setdefault("veto_cooldown", 0)
    state.setdefault("gov_suspended_until", 0)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    serializable = json.loads(json.dumps(state, default=lambda o: sorted(o) if isinstance(o, set) else o))
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(serializable, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def append_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def generate_event(rng: random.Random) -> Event:
    world = rng.choice(WORLDS)
    flavor = rng.choice(GOVERNANCE_FLAVORS) if world == "governance" else ""
    return Event(world=world, country=rng.choice(COUNTRIES), risk=rng.random(), flavor=flavor)


def update_alliances(civs: list[Civilization]) -> None:
    for civ in civs:
        civ.allies = set()
    for i, a in enumerate(civs):
        for b in civs[i + 1:]:
            if abs(a.intent - b.intent) <= ALLIANCE_THRESHOLD:
                a.allies.add(b.name)
                b.allies.add(a.name)


def pick_competitors(civs: list[Civilization], rng: random.Random) -> tuple[Civilization, Civilization]:
    non_allied = [(a, b) for i, a in enumerate(civs) for b in civs[i + 1:] if b.name not in a.allies]
    pool = non_allied or [(a, b) for i, a in enumerate(civs) for b in civs[i + 1:]]
    return rng.choice(pool)


# Match upset temperature: higher intent is favored, but every match has an
# element of chance. Raising this value increases upsets and keeps long-run
# win rates balanced across swarms despite fixed personality differences.
UPSET_TEMPERATURE = 1.0


# ---------------------------------------------------------------------------
# Governance: GOV, the sovereign regulator
# ---------------------------------------------------------------------------

def gov_suspended(state: dict[str, Any]) -> bool:
    """GOV can issue directives / vetoes only while it holds power."""
    return state["generation"] < state.get("gov_suspended_until", 0)


def issue_directive(state: dict[str, Any], civs: list[Civilization]) -> dict[str, Any] | None:
    """GOV issues at most one directive per generation. Power costs intent."""
    if gov_suspended(state):
        return None
    gov = next(c for c in civs if c.name == "GOV")
    rivals = [c for c in civs if c.name != "GOV"]

    def spend() -> None:
        gov.intent = max(0.0, gov.intent - DIRECTIVE_COST)

    # 1. Mandate: order overheated civs to STABILIZE next generation.
    hot = [c for c in rivals if c.intent > MANDATE_INTENT_ABOVE]
    if hot:
        target = max(hot, key=lambda c: c.intent)
        state.setdefault("pending_mandates", {})[target.name] = "STABILIZE"
        spend()
        return {"type": "MANDATE", "target": target.name, "action": "STABILIZE"}

    # 2. Sanction: halve a runaway winner's odds for 2 generations.
    leaders = [c for c in rivals
               if c.total_matches >= 5 and c.win_rate() > SANCTION_WIN_RATE_ABOVE]
    if leaders:
        target = max(leaders, key=lambda c: c.win_rate())
        state.setdefault("sanctions", {})[target.name] = 2
        spend()
        return {"type": "SANCTION", "target": target.name, "generations": 2}

    return None


def maybe_election(state: dict[str, Any], civs: list[Civilization]) -> dict[str, Any] | None:
    """Every ELECTION_EVERY generations the swarms vote on GOV's legitimacy.

    A civ votes to oust when GOV is weak (win rate < 0.40) or has it
    sanctioned. A majority suspends GOV's powers for GOV_SUSPENSION_GENS.
    """
    gen = state["generation"]
    if gen == 0 or gen % ELECTION_EVERY != 0:
        return None
    gov = next(c for c in civs if c.name == "GOV")
    sanctions = state.get("sanctions", {})
    votes_oust = sum(
        1 for c in civs
        if c.name != "GOV" and (gov.win_rate() < 0.40 or c.name in sanctions)
    )
    ousted = votes_oust >= 2
    if ousted:
        state["gov_suspended_until"] = gen + GOV_SUSPENSION_GENS
    return {"ousted": ousted, "votes_oust": votes_oust,
            "gov_win_rate": round(gov.win_rate(), 3)}


def compete(a: Civilization, b: Civilization, state: dict[str, Any],
            rng: random.Random, gov_events: list[dict[str, Any]]) -> Civilization:
    """Probabilistic contest (Bradley-Terry): higher intent is favored.

    A pure "higher intent always wins" rule lets fixed personality drift
    (bias x learning rate) decide nearly every match, so one swarm dominates
    permanently. The softmax keeps intent meaningful while letting win rates
    converge across swarms. GOV's sanctions and veto can then overturn results.
    """
    diff = (a.intent - b.intent) / UPSET_TEMPERATURE
    p_a_wins = 1.0 / (1.0 + math.exp(-diff))
    winner = a if rng.random() < p_a_wins else b
    loser = b if winner is a else a

    # Sanctioned civs win only half of their would-be victories.
    if winner.name in state.get("sanctions", {}) and rng.random() < 0.5:
        gov_events.append({"type": "SANCTION_FLIP",
                           "original_winner": winner.name, "new_winner": loser.name})
        winner, loser = loser, winner

    # GOV vetoes runaway leaders (needs a track record first).
    if (winner.name != "GOV" and winner.total_matches >= 5
            and winner.win_rate() > VETO_WIN_RATE_ABOVE
            and state.get("veto_cooldown", 0) <= 0
            and not gov_suspended(state)):
        gov_events.append({"type": "VETO",
                           "original_winner": winner.name, "new_winner": loser.name})
        winner, loser = loser, winner
        state["veto_cooldown"] = VETO_COOLDOWN_GENS
    winner.wins += 1
    state["swarms"][winner.name]["wins"] = winner.wins
    for civ in (a, b):
        civ.total_matches += 1
        state["swarms"][civ.name]["total_matches"] = civ.total_matches
        if civ.name != winner.name and civ.name in winner.allies:
            civ.intent = max(0.0, min(1.0, civ.intent + 0.01))
    return winner


def evolve_world_pressure(state: dict[str, Any], civs: list[Civilization]) -> None:
    avg = sum(c.intent for c in civs) / len(civs)
    state["world_pressure"] = max(0.0, min(1.0, state["world_pressure"] + (avg - 0.5) * PRESSURE_LEARNING_RATE))


def maybe_collapse(state: dict[str, Any], civs: list[Civilization], rng: random.Random) -> str | None:
    p = state["world_pressure"]
    if p >= COLLAPSE_HIGH:
        kind = "COLLAPSE"
    elif p <= COLLAPSE_LOW:
        kind = "STAGNATION"
    else:
        return None
    for civ in civs:
        # Rebirth: randomize intent around 0.5 with a trace of the old self.
        # DNA (bias, learning_rate, civ_seed) is preserved -> fingerprint stable.
        civ.intent = max(0.0, min(1.0, 0.5 + rng.uniform(-0.3, 0.3) + (civ.intent - 0.5) * 0.1))
        civ.allies = set()
        civ.recent_intents = []
    state["world_pressure"] = 0.5
    state["collapses"] = state.get("collapses", 0) + 1
    return kind


def _push_recent(civ: Civilization) -> None:
    civ.recent_intents.append(round(civ.intent, 4))
    if len(civ.recent_intents) > RECENT_INTENT_WINDOW:
        civ.recent_intents = civ.recent_intents[-RECENT_INTENT_WINDOW:]


def run_generation(state: dict[str, Any], rng: random.Random, delay: float,
                   state_file: Path, history_file: Path) -> None:
    sw = state["swarms"]
    civs = [
        Civilization(
            name=n,
            intent=sw[n]["intent"],
            wins=sw[n]["wins"],
            bias=sw[n]["bias"],
            learning_rate=sw[n]["learning_rate"],
            civ_seed=sw[n]["civ_seed"],
            action_counts=dict(sw[n]["action_counts"]),
            total_matches=sw[n]["total_matches"],
            recent_intents=list(sw[n]["recent_intents"]),
        )
        for n in SWARMS
    ]

    event = generate_event(rng)

    # Last generation's GOV mandates override this generation's decisions.
    gov_events: list[dict[str, Any]] = []
    pending = state.pop("pending_mandates", {})
    for target_name, forced in pending.items():
        gov_events.append({"type": "MANDATE_APPLIED", "target": target_name, "action": forced})

    actions: dict[str, str] = {}
    perceived: dict[str, float] = {}
    for civ in civs:
        pr = civ.perceive(event, rng)
        perceived[civ.name] = round(pr, 3)
        action = civ.learn_from(pr)
        if civ.name in pending:
            action = pending[civ.name]   # the mandate overrules the decision
        civ.action_counts[action] += 1
        actions[civ.name] = action
        _push_recent(civ)

    # GOV issues at most one new directive per generation.
    directive = issue_directive(state, civs)
    if directive:
        gov_events.append(directive)

    update_alliances(civs)
    a, b = pick_competitors(civs, rng)
    winner = compete(a, b, state, rng, gov_events)
    evolve_world_pressure(state, civs)
    collapse = maybe_collapse(state, civs, rng)
    election = maybe_election(state, civs)

    # Tick down governance timers.
    sanctions = state.get("sanctions", {})
    for name in list(sanctions):
        sanctions[name] -= 1
        if sanctions[name] <= 0:
            del sanctions[name]
    if state.get("veto_cooldown", 0) > 0:
        state["veto_cooldown"] -= 1

    # Persist learned fields back into state.
    for civ in civs:
        sw[civ.name]["intent"] = round(civ.intent, 4)
        sw[civ.name]["action_counts"] = civ.action_counts
        sw[civ.name]["recent_intents"] = civ.recent_intents

    # Console output (compact, one line per civilization).
    for civ in civs:
        ally_str = f" allies={sorted(civ.allies)}" if civ.allies else ""
        print(f"{civ.name} {civ.fingerprint()} -> {actions[civ.name]} | "
              f"intent={civ.intent:.2f} wr={civ.win_rate():.2f}{ally_str}")
    print(f"perceived risk: {perceived}")
    print(f"🏆 {a.name} vs {b.name} → winner {winner.name} | 🌍 pressure {state['world_pressure']:.3f}")
    if collapse:
        print(f"💥 {collapse}: world reset, intents reborn (collapses={state['collapses']})")
    for ge in gov_events:
        t = ge["type"]
        if t == "MANDATE":
            print(f"🏛️ GOV MANDATE: {ge['target']} ordered to {ge['action']} next generation")
        elif t == "MANDATE_APPLIED":
            print(f"🏛️ mandate enforced: {ge['target']} acts {ge['action']}")
        elif t == "SANCTION":
            print(f"🏛️ GOV SANCTION: {ge['target']} win odds halved for {ge['generations']} generations")
        elif t == "SANCTION_FLIP":
            print(f"🏛️ sanction flips it: {ge['original_winner']} denied → {ge['new_winner']} takes the win")
        elif t == "VETO":
            print(f"🏛️ GOV VETO: {ge['original_winner']}'s win overturned → {ge['new_winner']} wins")
    if election:
        if election["ousted"]:
            print(f"🗳️ ELECTION: GOV voted OUT ({election['votes_oust']}/3), "
                  f"suspended {GOV_SUSPENSION_GENS} generations")
        else:
            print(f"🗳️ ELECTION: GOV retains power ({election['votes_oust']}/3 votes to oust)")
    print("-" * 64)

    save_state(state_file, state)
    append_history(history_file, {
        "generation": state["generation"],
        "event": {"world": event.world, "country": event.country,
                  "risk": round(event.risk, 3), "flavor": event.flavor},
        "perceived": perceived,
        "intents": {c.name: round(c.intent, 4) for c in civs},
        "actions": actions,
        "match": f"{a.name} vs {b.name}",
        "winner": winner.name,
        "world_pressure": round(state["world_pressure"], 4),
        "collapse": collapse,
        "fingerprints": {c.name: c.fingerprint() for c in civs},
        "governance": {
            "directive": directive,
            "events": gov_events,
            "election": election,
            "sanctions": dict(state.get("sanctions", {})),
            "gov_suspended": gov_suspended(state),
        },
    })

    state["generation"] += 1
    time.sleep(delay)


def print_roster(state_file: Path, roster_file: Path) -> None:
    """Print and save the current fingerprint + profile roster."""
    state = load_state(state_file)
    sw = state["swarms"]
    roster = []
    print(f"\n🧬 CIVILIZATION FINGERPRINT ROSTER (generation {state['generation']})")
    print("-" * 64)
    for n in SWARMS:
        civ = Civilization(
            name=n, intent=sw[n]["intent"], wins=sw[n]["wins"], bias=sw[n]["bias"],
            learning_rate=sw[n]["learning_rate"], civ_seed=sw[n]["civ_seed"],
            action_counts=dict(sw[n]["action_counts"]), total_matches=sw[n]["total_matches"],
            recent_intents=list(sw[n]["recent_intents"]),
        )
        row = {
            "name": n,
            "fingerprint": civ.fingerprint(),
            "profile": civ.profile(),
            "intent": round(civ.intent, 3),
            "win_rate": round(civ.win_rate(), 3),
            "volatility": round(civ.volatility(), 3),
            "dominant_action": civ.dominant_action(),
            "total_matches": civ.total_matches,
        }
        roster.append(row)
        print(f"{n:5} {civ.fingerprint()}  {civ.profile():34} "
              f"intent={civ.intent:.2f} wr={civ.win_rate():.2f}")
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    roster_file.write_text(json.dumps(roster, indent=2) + "\n", encoding="utf-8")
    if gov_suspended(state):
        print(f"🏛️ GOV status: SUSPENDED until generation {state['gov_suspended_until']}")
    else:
        print("🏛️ GOV status: IN POWER")
    print(f"\n(saved to {roster_file})")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--generations", type=int, default=DEFAULT_GENERATIONS)
    p.add_argument("--delay", type=float, default=0.8)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    p.add_argument("--history-file", type=Path, default=DEFAULT_HISTORY_FILE)
    p.add_argument("--roster-file", type=Path, default=FINGERPRINT_ROSTER_FILE)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--reset", action="store_true")
    p.add_argument("--fingerprints", action="store_true", help="Just print the fingerprint roster and exit.")
    args = p.parse_args(argv)
    # Security: validate numeric inputs explicitly instead of silently
    # coercing or crashing on out-of-range values.
    if args.generations < 1:
        p.error("--generations must be >= 1")
    if args.delay < 0:
        p.error("--delay must be >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.reset:
        for f in (args.state_file, args.history_file, args.roster_file):
            if f.exists():
                f.unlink()

    if args.fingerprints:
        print_roster(args.state_file, args.roster_file)
        return 0

    state = load_state(args.state_file)
    state["generation"] = state.get("generation", 0) + 1
    rng = random.Random(args.seed)

    print(f"\n🌌 CYBER CIVILIZATION (PHASE 3)")
    print(f"Generation {state['generation']} | state={args.state_file} | history={args.history_file}\n")

    try:
        for _ in range(args.generations):
            run_generation(state, rng, args.delay, args.state_file, args.history_file)
    except KeyboardInterrupt:
        print("\nInterrupted; state saved.", file=sys.stderr)

    print_roster(args.state_file, args.roster_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
