"""Cyber CIV Arbiter — standalone resolution engine (prototype).

Built 2026-10-31 on Paris's "Yes, build the Arbiter" — adapted from the
Arbiter design pasted the same evening. Corrections vs the proposal:
- Real faction names: Ares, Pax, Vex, Archon (not RED/BLUE/GRAY/GOV).
- Deterministic: seeded random.Random instance. The proposal called itself
  deterministic while using unseeded module-level random.
- No LLM/provider calls: pure function of faction payloads + intent ledger.
  No OpenAI/Anthropic key needed, nothing to call.
- `json` is imported (the proposal used json.dumps without importing it).
- Missing payload fields default safely instead of raising KeyError.

NOT wired into the live sim (cyber_civ.py / cyber_civ_llm.py) and NOT the
enterprise stack (no Kafka, Kubernetes, or microservices). Standalone
prototype only: feed it faction payloads, get back the resolved generation.
"""

import json
import random
from typing import Dict, List, Tuple

CIVIL_FACTIONS = ["Ares", "Pax", "Vex"]
REGULATOR = "Archon"
ALL_FACTIONS = CIVIL_FACTIONS + [REGULATOR]
ACTIONS = ("EXPAND", "STABILIZE", "EXPLORE")


class CyberCivArbiter:
    """Deterministic resolution engine for one Cyber CIV generation."""

    def __init__(self, seed: int = 31, combat_variance: float = 0.15):
        self.rng = random.Random(seed)
        self.seed = seed
        self.combat_variance = combat_variance

    def process_generation_cycle(
        self,
        current_generation: int,
        faction_payloads: Dict[str, dict],
        current_intent_scores: Dict[str, float],
    ) -> dict:
        """Steps 4-7: mandates, alliances, conflicts, ledger update."""
        # 1. GOVERNANCE & MANDATES (Step 4)
        archon_action = faction_payloads.get(REGULATOR, {}).get(
            "selected_action", "STABILIZE"
        )
        archon_target = faction_payloads.get(REGULATOR, {}).get(
            "target_faction_focus", "NONE"
        )
        is_archon_active = (
            archon_action in ("EXPAND", "EXPLORE") and archon_target != "NONE"
        )
        archon_deposed = self._evaluate_anti_regulator_sentiment(faction_payloads)
        if archon_deposed:
            is_archon_active = False

        # 2. ALLIANCE FORMATION (Step 5)
        alliances = self._calculate_alliances(faction_payloads, archon_deposed)

        # 3. COMPETITION & CONFLICT RESOLUTION (Step 6)
        clashes = self._detect_conflict_vectors(
            faction_payloads, alliances, is_archon_active, archon_target
        )
        intent_deltas = {f: 0.0 for f in faction_payloads}
        clash_log = []
        for attacker, defender in clashes:
            attacker_win, delta = self._resolve_probabilistic_contest(
                attacker, defender, current_intent_scores
            )
            if attacker_win:
                intent_deltas[attacker] += delta
                intent_deltas[defender] -= delta
                clash_log.append(f"{attacker} disrupted {defender} (+{delta:.3f})")
            else:
                intent_deltas[attacker] -= delta * 0.5
                clash_log.append(f"{defender} repelled {attacker}")

        # 4. DRIFT & STATE LEDGER UPDATE (Step 7)
        new_intent_scores = {}
        for faction, base_score in current_intent_scores.items():
            action = faction_payloads.get(faction, {}).get(
                "selected_action", "STABILIZE"
            )
            action_reward = 0.02 if action == "EXPAND" else 0.01
            final_score = base_score + intent_deltas.get(faction, 0.0) + action_reward
            new_intent_scores[faction] = round(max(0.0, min(1.0, final_score)), 3)

        return {
            "generation_id": current_generation,
            "archon_deposed_status": archon_deposed,
            "archon_mandate_active": is_archon_active,
            "active_alliances": alliances,
            "resolved_clashes": [list(c) for c in clashes],
            "clash_log": clash_log,
            "updated_intent_ledger": new_intent_scores,
        }

    def _evaluate_anti_regulator_sentiment(self, payloads: Dict[str, dict]) -> bool:
        """True when >= 2 of the 3 civil factions target Archon."""
        votes = sum(
            1
            for f in CIVIL_FACTIONS
            if payloads.get(f, {}).get("target_faction_focus") == REGULATOR
        )
        return votes >= 2

    def _calculate_alliances(
        self, payloads: Dict[str, dict], archon_deposed: bool
    ) -> List[List[str]]:
        """Group civil factions into blocs on shared non-EXPAND action or
        joint anti-Archon targeting while Archon is deposed."""
        alliances = []
        checked = set()
        for i, f1 in enumerate(CIVIL_FACTIONS):
            if f1 in checked:
                continue
            bloc = [f1]
            for f2 in CIVIL_FACTIONS[i + 1:]:
                same_action = payloads.get(f1, {}).get(
                    "selected_action"
                ) == payloads.get(f2, {}).get("selected_action")
                both_targeting_archon = (
                    archon_deposed
                    and payloads.get(f1, {}).get("target_faction_focus") == REGULATOR
                    and payloads.get(f2, {}).get("target_faction_focus") == REGULATOR
                )
                if (
                    same_action
                    and payloads.get(f1, {}).get("selected_action") != "EXPAND"
                ) or both_targeting_archon:
                    bloc.append(f2)
                    checked.add(f2)
            if len(bloc) > 1:
                alliances.append(bloc)
                checked.add(f1)
        return alliances

    def _detect_conflict_vectors(
        self,
        payloads: Dict[str, dict],
        alliances: List[List[str]],
        is_archon_active: bool,
        archon_target: str,
    ) -> List[Tuple[str, str]]:
        """Map outbound EXPAND focus to unblocked competitive clashes."""
        def share_alliance(f1, f2):
            return any(f1 in bloc and f2 in bloc for bloc in alliances)

        clashes = []
        if is_archon_active and archon_target in payloads:
            clashes.append((REGULATOR, archon_target))
        for faction, data in payloads.items():
            if faction == REGULATOR:
                continue
            target = data.get("target_faction_focus", "NONE")
            if (
                data.get("selected_action") == "EXPAND"
                and target in payloads
                and target != faction
                and not share_alliance(faction, target)
                and target != REGULATOR
            ):
                clashes.append((faction, target))
        return clashes

    def _resolve_probabilistic_contest(
        self, attacker: str, defender: str, scores: Dict[str, float]
    ) -> Tuple[bool, float]:
        """Intent-mass contest with seeded variance. Deterministic per seed."""
        att_power = scores.get(attacker, 0.5)
        def_power = scores.get(defender, 0.5)
        att_roll = att_power + self.rng.uniform(
            -self.combat_variance, self.combat_variance
        )
        def_roll = def_power + self.rng.uniform(
            -self.combat_variance, self.combat_variance
        )
        success = att_roll > def_roll
        intensity_delta = round(abs(att_roll - def_roll) * 0.15, 3)
        return success, max(0.01, intensity_delta)


if __name__ == "__main__":
    # Demo 1: mirror of real showcase generation 64 (Binance $6.3B headline).
    gen64_payloads = {
        "Ares": {"selected_action": "STABILIZE", "target_faction_focus": "NONE"},
        "Pax": {"selected_action": "EXPAND", "target_faction_focus": "Ares"},
        "Vex": {"selected_action": "EXPAND", "target_faction_focus": "Pax"},
        "Archon": {"selected_action": "STABILIZE", "target_faction_focus": "NONE"},
    }
    gen64_intents = {"Ares": 0.944, "Pax": 0.184, "Vex": 0.597, "Archon": 0.045}

    # Demo 2: coup scenario — Pax + Vex both target Archon.
    coup_payloads = {
        "Ares": {"selected_action": "STABILIZE", "target_faction_focus": "NONE"},
        "Pax": {"selected_action": "STABILIZE", "target_faction_focus": "Archon"},
        "Vex": {"selected_action": "STABILIZE", "target_faction_focus": "Archon"},
        "Archon": {"selected_action": "EXPAND", "target_faction_focus": "Pax"},
    }
    coup_intents = {"Ares": 0.650, "Pax": 0.420, "Vex": 0.510, "Archon": 0.700}

    arbiter = CyberCivArbiter(seed=31)
    print("--- Demo 1: generation 64 mirror ---")
    print(json.dumps(arbiter.process_generation_cycle(64, gen64_payloads, gen64_intents), indent=2))
    print("--- Demo 2: coup scenario ---")
    print(json.dumps(arbiter.process_generation_cycle(143, coup_payloads, coup_intents), indent=2))
