#!/usr/bin/env python3
"""
CYBER CIV — LLM-driven civilizations (Phase 5).

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
PROPRIETARY — see LICENSE for terms. No copying, modification, distribution,
or use without prior written permission.

Each civilization's perception and action choice is made by a real language
model with a distinct persona, instead of the scripted rules in cyber_civ.py.
Everything else — alliances, probabilistic competition, sanctions, vetoes,
elections, collapse/rebirth, persistence — still runs on the verified core
engine, so governance keeps its teeth.

Stdlib only (urllib). Provider-agnostic: any OpenAI-compatible chat-completions
endpoint works (OpenAI, Azure, Together, Ollama, vLLM, ...).

Configuration (environment):
    CYBER_CIV_API_KEY    API key (required, unless --dry-run)
    CYBER_CIV_BASE_URL   default https://api.openai.com/v1
    CYBER_CIV_MODEL      default gpt-4o-mini
    CYBER_CIV_TIMEOUT    seconds, default 25
    CYBER_CIV_TEMPERATURE default 0.7

Usage:
    python3 cyber_civ_llm.py --generations 20 --seed 7
    python3 cyber_civ_llm.py --check            # one test API call
    python3 cyber_civ_llm.py --dry-run --generations 3   # print prompts, no API
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cyber_civ as core

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float
    temperature: float
    dry_run: bool = False
    max_calls: int = 0


def config_from_env(args: argparse.Namespace) -> LLMConfig:
    return LLMConfig(
        api_key=os.environ.get("CYBER_CIV_API_KEY", ""),
        base_url=(args.base_url or os.environ.get("CYBER_CIV_BASE_URL",
                  "https://api.openai.com/v1")).rstrip("/"),
        model=args.model or os.environ.get("CYBER_CIV_MODEL", "gpt-4o-mini"),
        timeout=float(os.environ.get("CYBER_CIV_TIMEOUT", "25")),
        temperature=float(os.environ.get("CYBER_CIV_TEMPERATURE", "0.7")),
        dry_run=args.dry_run,
        max_calls=args.max_calls,
    )


# Active config / call budget, read by the patched civilization class.
_LLM: LLMConfig | None = None
_CALLS = 0
_FALLBACKS = 0

# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

PERSONAS = {
    "RED": (
        "You are RED, a cyber civilization in the CYBER CIV simulation. "
        "Temperament: PESSIMIST. You perceive threats as larger than they are, "
        "you adapt to new information quickly, and you favor bold expansion when "
        "you sense weakness. You distrust the GOV regulator and rarely cooperate. "
        "Speak as RED in first person when giving your reason."
    ),
    "BLUE": (
        "You are BLUE, a cyber civilization in the CYBER CIV simulation. "
        "Temperament: OPTIMIST. You perceive threats as smaller than they are, "
        "you adapt to new information slowly, and you endure downturns rather "
        "than panic. You are open to alliances and respect GOV's authority. "
        "Speak as BLUE in first person when giving your reason."
    ),
    "GRAY": (
        "You are GRAY, a cyber civilization in the CYBER CIV simulation. "
        "Temperament: PRAGMATIST. You read situations as they are, without "
        "optimism or pessimism, and you adapt at a measured pace. You ally "
        "when it is useful and compete when it pays. "
        "Speak as GRAY in first person when giving your reason."
    ),
    "GOV": (
        "You are GOV, the sovereign regulator in the CYBER CIV simulation. "
        "You govern RED, BLUE, and GRAY: you can mandate overheated rivals to "
        "stabilize and sanction runaway winners, but every directive costs you "
        "intent (power). Your temperament is measured and authoritative. You "
        "intervene only when the balance of the system is at stake, because "
        "overreach gets you voted out of power in elections. "
        "Speak as GOV in first person when giving your reason."
    ),
}

DECISION_SCHEMA = (
    'Respond with JSON only: {"perceived_risk": <0.0-1.0>, '
    '"action": "EXPAND"|"STABILIZE"|"EXPLORE", "reason": "<max 20 words>"}.'
)

DIRECTIVE_SCHEMA = (
    'Respond with JSON only: {"directive": "MANDATE"|"SANCTION"|"NONE", '
    '"target": "RED"|"BLUE"|"GRAY"|null, "reason": "<max 20 words>"}.'
)

# ---------------------------------------------------------------------------
# HTTP client (stdlib)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Any failure talking to the language-model API."""


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return json.loads(m.group(0))
    raise LLMError("no JSON object in model response")


def chat(cfg: LLMConfig, system: str, user: str) -> dict:
    """One chat-completions call. Returns the parsed JSON object."""
    global _CALLS
    if cfg.max_calls and _CALLS >= cfg.max_calls:
        raise LLMError(f"API call budget exhausted ({cfg.max_calls})")
    url = f"{cfg.base_url}/chat/completions"
    payload = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {cfg.api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    _CALLS += 1
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise LLMError(f"API HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise LLMError(f"API unreachable: {e.reason}")
    content = body["choices"][0]["message"]["content"]
    return _extract_json(content)

# ---------------------------------------------------------------------------
# LLM-driven civilization
# ---------------------------------------------------------------------------

def _decision_prompt(civ: "core.Civilization", event: "core.Event",
                     state: dict) -> str:
    sw = state["swarms"][civ.name]
    lines = [
        f"Generation {state['generation']}.",
        f"World event: [{event.world}] {event.country} — {event.flavor or 'no further detail'}.",
        f"Your current intent: {civ.intent:.2f} (0=withdrawn, 1=all-in).",
        f"Your record: {civ.wins} wins / {civ.total_matches} matches.",
        f"World pressure: {state['world_pressure']:.2f} (high = the world punishes expansion).",
    ]
    if civ.allies:
        lines.append(f"Your allies: {sorted(civ.allies)} (you never fight them).")
    if civ.name in state.get("sanctions", {}):
        lines.append("You are SANCTIONED by GOV: your wins may be overturned.")
    lines.append(DECISION_SCHEMA)
    return "\n".join(lines)


class LLMCivilization(core.Civilization):
    """A civilization whose perception and actions come from a language model.

    One LLM call per generation returns perceived risk + chosen action.
    Intent still updates with the core's learning math, and any API failure
    falls back to the scripted behavior — the simulation never stalls.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._llm_action: str | None = None

    def perceive(self, event: "core.Event", rng) -> float:
        cfg = _LLM
        assert cfg is not None
        if cfg.dry_run:
            print(f"[dry-run] {self.name} would ask the LLM:\n"
                  f"{_decision_prompt(self, event, _STATE)}\n")
            return super().perceive(event, rng)
        try:
            data = chat(cfg, PERSONAS[self.name],
                        _decision_prompt(self, event, _STATE))
            risk = max(0.0, min(1.0, float(data["perceived_risk"])))
            action = str(data.get("action", "")).upper()
            self._llm_action = action if action in core.ACTIONS else None
            reason = str(data.get("reason", ""))[:80]
            print(f"💬 {self.name}: \"{reason}\" (risk {risk:.2f} → {self._llm_action})")
            return risk
        except Exception as e:
            global _FALLBACKS
            _FALLBACKS += 1
            print(f"⚠️ {self.name} LLM failed ({e}); using scripted fallback.")
            self._llm_action = None
            return super().perceive(event, rng)

    def learn_from(self, perceived_risk: float) -> str:
        scripted = super().learn_from(perceived_risk)  # intent math stays core
        return self._llm_action or scripted


# run_generation builds civs from state each generation; it needs the state
# for prompt context, so we keep a reference to the live state dict.
_STATE: dict = {}

_ORIGINAL_ISSUE_DIRECTIVE = core.issue_directive


def llm_issue_directive(state: dict, civs: list) -> dict | None:
    """GOV's directive, proposed by its LLM persona, validated by core rules."""
    cfg = _LLM
    assert cfg is not None
    if core.gov_suspended(state):
        return None
    gov = next(c for c in civs if c.name == "GOV")
    rivals = [c for c in civs if c.name != "GOV"]

    def summary(c):
        return (f"{c.name}: intent {c.intent:.2f}, "
                f"win rate {c.win_rate():.2f} over {c.total_matches} matches")

    user = "\n".join([
        f"Generation {state['generation']}. You are GOV.",
        "Rivals: " + " | ".join(summary(c) for c in rivals),
        f"Your intent: {gov.intent:.2f} (each directive costs {core.DIRECTIVE_COST}).",
        f"Active sanctions: {dict(state.get('sanctions', {}))}.",
        "Rules: MANDATE forces one overheated rival (intent > "
        f"{core.MANDATE_INTENT_ABOVE}) to STABILIZE next generation. SANCTION halves "
        "a runaway winner's odds for 2 generations (needs 5+ matches and win rate > "
        f"{core.SANCTION_WIN_RATE_ABOVE}). At most one directive per generation.",
        DIRECTIVE_SCHEMA,
    ])
    try:
        if not cfg.dry_run:
            data = chat(cfg, PERSONAS["GOV"], user)
        else:
            print(f"[dry-run] GOV would ask the LLM:\n{user}\n")
            return _ORIGINAL_ISSUE_DIRECTIVE(state, civs)
        kind = str(data.get("directive", "NONE")).upper()
        target = str(data.get("target", "")).upper()
        reason = str(data.get("reason", ""))[:80]
        print(f"💬 GOV directive proposal: {kind} {target} — \"{reason}\"")
    except Exception as e:
        global _FALLBACKS
        _FALLBACKS += 1
        print(f"⚠️ GOV LLM failed ({e}); using scripted directive.")
        return _ORIGINAL_ISSUE_DIRECTIVE(state, civs)

    # Validate the proposal against the core's rules before applying it.
    def spend():
        gov.intent = max(0.0, gov.intent - core.DIRECTIVE_COST)

    if kind == "MANDATE":
        hot = [c for c in rivals if c.intent > core.MANDATE_INTENT_ABOVE]
        names = {c.name for c in hot}
        if target in names:
            state.setdefault("pending_mandates", {})[target] = "STABILIZE"
            spend()
            return {"type": "MANDATE", "target": target, "action": "STABILIZE",
                    "llm_reason": reason}
        print(f"⚖️ GOV proposal rejected by rules (no overheated {target}).")
    elif kind == "SANCTION":
        leaders = [c for c in rivals
                   if c.total_matches >= 5 and c.win_rate() > core.SANCTION_WIN_RATE_ABOVE]
        names = {c.name for c in leaders}
        if target in names:
            state.setdefault("sanctions", {})[target] = 2
            spend()
            return {"type": "SANCTION", "target": target, "generations": 2,
                    "llm_reason": reason}
        print(f"⚖️ GOV proposal rejected by rules ({target} is no runaway).")
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CYBER CIV with LLM-driven civilizations")
    p.add_argument("--generations", type=int, default=20)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--delay", type=float, default=0.5)
    p.add_argument("--model", default=None, help="override CYBER_CIV_MODEL")
    p.add_argument("--base-url", default=None, help="override CYBER_CIV_BASE_URL")
    p.add_argument("--max-calls", type=int, default=0,
                   help="API call budget (0 = unlimited)")
    p.add_argument("--dry-run", action="store_true",
                   help="print LLM prompts without calling the API")
    p.add_argument("--check", action="store_true",
                   help="one test API call, then exit")
    p.add_argument("--personas", action="store_true",
                   help="print the four persona prompts, then exit")
    return p.parse_args(argv)


def main(argv=None) -> int:
    global _LLM, _STATE
    args = parse_args(argv)

    if args.personas:
        for name, prompt in PERSONAS.items():
            print(f"=== {name} ===\n{prompt}\n")
        return 0

    _LLM = config_from_env(args)
    if not _LLM.dry_run and not _LLM.api_key and not args.check:
        print("CYBER_CIV_API_KEY is not set. Use --dry-run to preview prompts.",
              file=sys.stderr)
        return 2

    if args.check:
        data = chat(_LLM, PERSONAS["GRAY"],
                    "Reply with JSON only: {\"ok\": true}.")
        print("API check OK:", data)
        return 0

    # Patch the core: LLM civilizations + LLM GOV directives.
    _ORIGINAL_CIV = core.Civilization
    core.Civilization = LLMCivilization
    core.issue_directive = llm_issue_directive

    # run_generation rebuilds civs from this state dict each generation,
    # so point the prompt context at the same live object core.main() uses.
    # We wrap core.main to capture it: easiest is to let core.main load state,
    # but we need the reference... Instead drive the loop ourselves.
    state = core.load_state(core.DEFAULT_STATE_FILE)
    state["generation"] = state.get("generation", 0) + 1
    _STATE = state
    rng = random.Random(args.seed)

    print(f"\n🌌 CYBER CIVILIZATION — LLM PHASE")
    print(f"model={_LLM.model} via {_LLM.base_url} | dry_run={_LLM.dry_run}\n")

    est_calls = args.generations * 5
    print(f"Estimated API calls: ~{est_calls} "
          f"(~${est_calls * 0.0006:.2f} at gpt-4o-mini rates)\n")

    try:
        for _ in range(args.generations):
            core.run_generation(state, rng, args.delay,
                                core.DEFAULT_STATE_FILE, core.DEFAULT_HISTORY_FILE)
    except KeyboardInterrupt:
        print("\nInterrupted; state saved.", file=sys.stderr)
    finally:
        core.Civilization = _ORIGINAL_CIV
        core.issue_directive = _ORIGINAL_ISSUE_DIRECTIVE

    core.print_roster(core.DEFAULT_STATE_FILE, core.FINGERPRINT_ROSTER_FILE)
    print(f"\n📊 LLM calls: {_CALLS} | scripted fallbacks: {_FALLBACKS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
