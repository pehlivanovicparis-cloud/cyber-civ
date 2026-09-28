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

Phase 4 adds Archon, a sovereign regulator civilization: directive powers
(mandate / sanction / veto) that cost intent to wield, a governance world
with its own event flavors, and election cycles that can vote Archon out of
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

SWARMS = ("Ares", "Pax", "Vex", "Archon")
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

# -- governance (Archon sovereign) -------------------------------------------
GOVERNANCE_FLAVORS = ("regulation", "election", "sanction", "treaty")
MANDATE_INTENT_ABOVE = 0.70   # Archon orders STABILIZE on overheated civs
SANCTION_WIN_RATE_ABOVE = 0.55
VETO_WIN_RATE_ABOVE = 0.55
VETO_COOLDOWN_GENS = 10
DIRECTIVE_COST = 0.02         # intent Archon spends per directive issued
ELECTION_EVERY = 25
GOV_SUSPENSION_GENS = 10

# Distinct starting personalities so fingerprints differ from generation 0.
PERSONALITY = {
    "Ares": {"bias": 0.10, "learning_rate": 0.07},   # pessimist, fast adapter
    "Pax": {"bias": -0.10, "learning_rate": 0.04}, # optimist, slow adapter
    "Vex": {"bias": 0.0, "learning_rate": 0.06},   # pragmatist, medium adapter
    "Archon": {"bias": 0.05, "learning_rate": 0.05},   # regulator: sees risk, medium adapter
}

ACTIONS = ("EXPAND", "STABILIZE", "EXPLORE", "SPY", "EMBARGO", "SUMMIT")
BASIC_ACTIONS = ("EXPAND", "STABILIZE", "EXPLORE")
ADVANCED_ACTIONS = ("SPY", "EMBARGO", "SUMMIT")  # "hands": act on the world

# Hands mechanics: cost is intent spent by the actor; cooldowns in generations.
ACTION_COOLDOWNS = {"SPY": 3, "EMBARGO": 5, "SUMMIT": 4}
SPY_COST = 0.01
SPY_INTEL_GAIN = 0.015   # small edge from knowing the target's intent
SPY_INTEL_GENS = 3       # how long the intel stays fresh
EMBARGO_COST = 0.02
EMBARGO_DAMAGE = 0.05    # intent stripped from the target
SUMMIT_COST = 0.01
SUMMIT_BOOST = 0.01      # every civilization gains this much intent

# Scripted-heuristic usage rates (per generation, per civ). Kept low so the
# verified balance stays close to the all-basic baseline.
SCRIPTED_SPY_P = 0.06
SCRIPTED_EMBARGO_P = 0.05
SCRIPTED_SUMMIT_P = 0.04


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
            # Home turf: Archon reads regulation clearly; everyone else guesses.
            noise *= 0.5 if self.name == "Archon" else 1.5
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
            "SPY": "Infiltrator",
            "EMBARGO": "Coercer",
            "SUMMIT": "Diplomat",
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
            "allies": [],
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
        "intel": {},                                    # spy -> {target: {intent, until}}
        "action_cooldowns": {n: {} for n in SWARMS},    # name -> {action: gens left}
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
    entry.setdefault("allies", [])
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
    state.setdefault("intel", {})
    state.setdefault("action_cooldowns", {n: {} for n in SWARMS})
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


# ---------------------------------------------------------------------------
# Hands: advanced actions (SPY / EMBARGO / SUMMIT)
# ---------------------------------------------------------------------------

def _cooldowns(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    return state.setdefault("action_cooldowns", {})


def cooldown_ready(state: dict[str, Any], name: str, action: str) -> bool:
    return _cooldowns(state).get(name, {}).get(action, 0) <= 0


def set_cooldown(state: dict[str, Any], name: str, action: str) -> None:
    _cooldowns(state).setdefault(name, {})[action] = ACTION_COOLDOWNS[action]


def tick_cooldowns(state: dict[str, Any]) -> None:
    for cds in _cooldowns(state).values():
        for a in list(cds):
            cds[a] -= 1
            if cds[a] <= 0:
                del cds[a]


def normalize_target(target: str | None, civs: list[Civilization], actor: str) -> str | None:
    """Match a target name case-insensitively; None if invalid or self."""
    if not target:
        return None
    want = str(target).strip().upper()
    for c in civs:
        if c.name.upper() == want and c.name != actor:
            return c.name
    return None


def choose_scripted_advanced_action(civ: Civilization, civs: list[Civilization],
                                    state: dict[str, Any],
                                    rng: random.Random) -> tuple[str | None, str | None]:
    """Small chance a scripted civilization uses its hands.

    Returns (action, target) or (None, None). Kept deliberately rare so the
    long-run balance stays near the verified all-basic baseline.
    """
    rivals = [c for c in civs if c.name != civ.name and c.name not in civ.allies]
    if not rivals:
        return None, None
    r = rng.random()
    if r < SCRIPTED_EMBARGO_P and cooldown_ready(state, civ.name, "EMBARGO"):
        prey = max(rivals, key=lambda c: c.intent)
        if prey.intent > civ.intent + 0.15:
            return "EMBARGO", prey.name
    if r < SCRIPTED_EMBARGO_P + SCRIPTED_SPY_P and cooldown_ready(state, civ.name, "SPY"):
        leader = max(rivals, key=lambda c: c.win_rate())
        return "SPY", leader.name
    if (r < SCRIPTED_EMBARGO_P + SCRIPTED_SPY_P + SCRIPTED_SUMMIT_P
            and cooldown_ready(state, civ.name, "SUMMIT")):
        return "SUMMIT", None
    return None, None


def resolve_advanced_action(state: dict[str, Any], civs: list[Civilization],
                            name: str, action: str, target: str | None,
                            events: list[dict[str, Any]]) -> bool:
    """Apply one hands-action. Returns True if it took effect.

    Validation failures are recorded in events and the caller falls back to
    STABILIZE, so the simulation never stalls on a bad proposal.
    """
    by_name = {c.name: c for c in civs}
    civ = by_name[name]
    ok, detail = True, ""
    if not cooldown_ready(state, name, action):
        ok, detail = False, "on cooldown"
    elif action == "SPY":
        t = by_name.get(target or "")
        if t is None or target in civ.allies:
            ok, detail = False, f"bad target {target}"
        else:
            civ.intent = max(0.0, min(1.0, civ.intent - SPY_COST + SPY_INTEL_GAIN))
            state.setdefault("intel", {}).setdefault(name, {})[target] = {
                "intent": round(t.intent, 3),
                "until": state["generation"] + SPY_INTEL_GENS,
            }
            set_cooldown(state, name, "SPY")
            detail = f"learns {target} intent={t.intent:.2f}"
    elif action == "EMBARGO":
        t = by_name.get(target or "")
        if t is None or target in civ.allies:
            ok, detail = False, f"bad target {target}"
        else:
            civ.intent = max(0.0, civ.intent - EMBARGO_COST)
            t.intent = max(0.0, t.intent - EMBARGO_DAMAGE)
            set_cooldown(state, name, "EMBARGO")
            detail = f"{target} -{EMBARGO_DAMAGE:.2f} intent"
    elif action == "SUMMIT":
        civ.intent = max(0.0, civ.intent - SUMMIT_COST)
        for c in civs:
            c.intent = min(1.0, c.intent + SUMMIT_BOOST)
        set_cooldown(state, name, "SUMMIT")
        detail = f"all +{SUMMIT_BOOST:.2f} intent"
    else:
        ok, detail = False, f"unknown action {action}"
    events.append({"type": "ADVANCED_ACTION", "actor": name, "action": action,
                   "target": target, "ok": ok, "detail": detail})
    return ok


def append_civ_memories(memory_dir: Path, state: dict[str, Any],
                        civs: list[Civilization], event: Event,
                        perceived: dict[str, float], actions: dict[str, str],
                        targets: dict[str, str | None], winner: Civilization,
                        match_names: set[str], sanctioned_now: set[str],
                        mandated: set[str]) -> None:
    """Phase A (RAG-lite): append one memory record per civilization.

    Retrieval lives in the LLM driver; the core just records faithfully.
    Files are capped at 500 entries to bound disk and prompt size.
    """
    gen = state["generation"]
    for civ in civs:
        rec = {
            "generation": gen,
            "world": event.world,
            "country": event.country,
            "flavor": event.flavor,
            "event_risk": round(event.risk, 3),
            "perceived_risk": perceived[civ.name],
            "action": actions[civ.name],
            "target": targets.get(civ.name),
            "intent_after": round(civ.intent, 4),
            "in_match": civ.name in match_names,
            "won": (winner.name == civ.name) if civ.name in match_names else None,
            "sanctioned": civ.name in sanctioned_now,
            "mandated": civ.name in mandated,
            "allies": sorted(civ.allies),
            "mode": (getattr(civ, "mode_tag", "") or "").strip() or "scripted",
            "reason": getattr(civ, "_llm_reason", ""),
        }
        path = memory_dir / f"{civ.name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        # Prune occasionally so files stay small.
        if gen % 25 == 0:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 500:
                path.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")


# Match upset temperature: higher intent is favored, but every match has an
# element of chance. Raising this value increases upsets and keeps long-run
# win rates balanced across swarms despite fixed personality differences.
UPSET_TEMPERATURE = 1.0


# ---------------------------------------------------------------------------
# Governance: Archon, the sovereign regulator
# ---------------------------------------------------------------------------

def gov_suspended(state: dict[str, Any]) -> bool:
    """Archon can issue directives / vetoes only while it holds power."""
    return state["generation"] < state.get("gov_suspended_until", 0)


def issue_directive(state: dict[str, Any], civs: list[Civilization]) -> dict[str, Any] | None:
    """Archon issues at most one directive per generation. Power costs intent."""
    if gov_suspended(state):
        return None
    gov = next(c for c in civs if c.name == "Archon")
    rivals = [c for c in civs if c.name != "Archon"]

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
    """Every ELECTION_EVERY generations the swarms vote on Archon's legitimacy.

    A civ votes to oust when Archon is weak (win rate < 0.40) or has it
    sanctioned. A majority suspends Archon's powers for GOV_SUSPENSION_GENS.
    """
    gen = state["generation"]
    if gen == 0 or gen % ELECTION_EVERY != 0:
        return None
    gov = next(c for c in civs if c.name == "Archon")
    sanctions = state.get("sanctions", {})
    votes_oust = sum(
        1 for c in civs
        if c.name != "Archon" and (gov.win_rate() < 0.40 or c.name in sanctions)
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
    converge across swarms. Archon's sanctions and veto can then overturn results.
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

    # Archon vetoes runaway leaders (needs a track record first).
    if (winner.name != "Archon" and winner.total_matches >= 5
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
    state["intel"] = {}                                     # intel goes stale
    state["action_cooldowns"] = {n: {} for n in SWARMS}      # cooldowns reset
    return kind


def _push_recent(civ: Civilization) -> None:
    civ.recent_intents.append(round(civ.intent, 4))
    if len(civ.recent_intents) > RECENT_INTENT_WINDOW:
        civ.recent_intents = civ.recent_intents[-RECENT_INTENT_WINDOW:]


def run_generation(state: dict[str, Any], rng: random.Random, delay: float,
                   state_file: Path, history_file: Path,
                   memory_dir: Path | None = None) -> None:
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
            allies=set(sw[n].get("allies", [])),
        )
        for n in SWARMS
    ]

    event = generate_event(rng)

    # Last generation's Archon mandates override this generation's decisions.
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

    # Archon issues at most one new directive per generation.
    directive = issue_directive(state, civs)
    if directive:
        gov_events.append(directive)

    # Hands: resolve advanced actions (SPY/EMBARGO/SUMMIT). Mandates overrule.
    targets: dict[str, str | None] = {}
    advanced_events: list[dict[str, Any]] = []
    for civ in civs:
        if civ.name in pending:
            continue
        if getattr(civ, "_is_llm", False):
            act = getattr(civ, "_llm_action", None)
            if act not in ADVANCED_ACTIONS:
                continue
            target = normalize_target(getattr(civ, "_llm_target", None), civs, civ.name)
        else:
            act, target = choose_scripted_advanced_action(civ, civs, state, rng)
            if act is None:
                continue
            actions[civ.name] = act
            civ.action_counts[act] = civ.action_counts.get(act, 0) + 1
        targets[civ.name] = target
        if not resolve_advanced_action(state, civs, civ.name, act, target, advanced_events):
            # Rejected hands fall back to holding position.
            civ.action_counts[act] -= 1
            civ.action_counts["STABILIZE"] = civ.action_counts.get("STABILIZE", 0) + 1
            actions[civ.name] = "STABILIZE"

    update_alliances(civs)
    a, b = pick_competitors(civs, rng)
    winner = compete(a, b, state, rng, gov_events)
    evolve_world_pressure(state, civs)
    collapse = maybe_collapse(state, civs, rng)
    election = maybe_election(state, civs)

    # Tick down governance timers.
    sanctioned_now = set(state.get("sanctions", {}))
    sanctions = state.get("sanctions", {})
    for name in list(sanctions):
        sanctions[name] -= 1
        if sanctions[name] <= 0:
            del sanctions[name]
    if state.get("veto_cooldown", 0) > 0:
        state["veto_cooldown"] -= 1
    tick_cooldowns(state)

    # Persist learned fields back into state.
    for civ in civs:
        sw[civ.name]["intent"] = round(civ.intent, 4)
        sw[civ.name]["action_counts"] = civ.action_counts
        sw[civ.name]["recent_intents"] = civ.recent_intents
        sw[civ.name]["allies"] = sorted(civ.allies)

    # Console output (compact, one line per civilization).
    for civ in civs:
        ally_str = f" allies={sorted(civ.allies)}" if civ.allies else ""
        mode_tag = getattr(civ, "mode_tag", "")  # " [LLM]"/" [scripted]" in LLM runs
        tgt = f"→{targets[civ.name]}" if targets.get(civ.name) else ""
        print(f"{civ.name} {civ.fingerprint()} -> {actions[civ.name]}{tgt}{mode_tag} | "
              f"intent={civ.intent:.2f} wr={civ.win_rate():.2f}{ally_str}")
    print(f"perceived risk: {perceived}")
    print(f"🏆 {a.name} vs {b.name} → winner {winner.name} | 🌍 pressure {state['world_pressure']:.3f}")
    if collapse:
        print(f"💥 {collapse}: world reset, intents reborn (collapses={state['collapses']})")
    for ge in gov_events:
        t = ge["type"]
        if t == "MANDATE":
            print(f"🏛️ Archon MANDATE: {ge['target']} ordered to {ge['action']} next generation")
        elif t == "MANDATE_APPLIED":
            print(f"🏛️ mandate enforced: {ge['target']} acts {ge['action']}")
        elif t == "SANCTION":
            print(f"🏛️ Archon SANCTION: {ge['target']} win odds halved for {ge['generations']} generations")
        elif t == "SANCTION_FLIP":
            print(f"🏛️ sanction flips it: {ge['original_winner']} denied → {ge['new_winner']} takes the win")
        elif t == "VETO":
            print(f"🏛️ Archon VETO: {ge['original_winner']}'s win overturned → {ge['new_winner']} wins")
    for ae in advanced_events:
        icon = {"SPY": "🕵️", "EMBARGO": "🚫", "SUMMIT": "🤝"}.get(ae["action"], "✋")
        mark = "✓" if ae["ok"] else "✗"
        tgt = f" → {ae['target']}" if ae["target"] else ""
        print(f"{icon} {ae['actor']} {ae['action']}{tgt} {mark} ({ae['detail']})")
    if election:
        if election["ousted"]:
            print(f"🗳️ ELECTION: Archon voted OUT ({election['votes_oust']}/3), "
                  f"suspended {GOV_SUSPENSION_GENS} generations")
        else:
            print(f"🗳️ ELECTION: Archon retains power ({election['votes_oust']}/3 votes to oust)")
    print("-" * 64)

    if memory_dir is not None:
        append_civ_memories(memory_dir, state, civs, event, perceived, actions,
                            targets, winner, {a.name, b.name}, sanctioned_now,
                            set(pending))

    save_state(state_file, state)
    append_history(history_file, {
        "generation": state["generation"],
        "event": {"world": event.world, "country": event.country,
                  "risk": round(event.risk, 3), "flavor": event.flavor},
        "perceived": perceived,
        "intents": {c.name: round(c.intent, 4) for c in civs},
        "actions": actions,
        "targets": targets,
        "advanced_actions": advanced_events,
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
        print(f"🏛️ Archon status: SUSPENDED until generation {state['gov_suspended_until']}")
    else:
        print("🏛️ Archon status: IN POWER")
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
