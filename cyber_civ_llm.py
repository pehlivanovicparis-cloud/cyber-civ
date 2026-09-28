#!/usr/bin/env python3
"""
CYBER CIV — LLM-driven civilizations (Phase 6).

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
PROPRIETARY — see LICENSE for terms. No copying, modification, distribution,
or use without prior written permission.

Each civilization's perception and action choice is made by a real language
model with a distinct persona, instead of the scripted rules in cyber_civ.py.
Everything else — alliances, probabilistic competition, sanctions, vetoes,
elections, collapse/rebirth, persistence — still runs on the verified core
engine, so governance keeps its teeth.

Phase 6 gives the agents the full AI stack:
  * RAG-lite memory: each civ keeps a JSONL log of past generations and
    retrieves its most relevant memories before deciding (--no-memory off).
  * Tools (MCP-shaped): with --tools, a civ may call read-only tools
    (standings, rival_record, recent_events, my_history) mid-decision; the
    runner executes them locally and the civ decides with results in hand.
  * Hands: civs can SPY (learn a rival's exact intent), EMBARGO (drain a
    rival's intent) or call a SUMMIT (lift everyone). Costs and cooldowns
    are enforced by the core.

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
    python3 cyber_civ_llm.py --generations 5 --tools        # enable tool use
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
    tools: bool = False    # Phase B: allow tool calls mid-decision
    memory: bool = True    # Phase A: retrieve past generations as context


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
        tools=args.tools,
        memory=not args.no_memory,
    )


# Active config / call budget, read by the patched civilization class.
_LLM: LLMConfig | None = None
_CALLS = 0
_FALLBACKS = 0
# Per-civilization LLM status for this run: name -> {"llm": int, "scripted": int,
# "last": "LLM" | "scripted" | "-"}. Civ objects are rebuilt every generation,
# so the status lives here, not on the instances.
_CIV_STATUS: dict = {}


def _record_status(name: str, mode: str) -> None:
    st = _CIV_STATUS.setdefault(name, {"llm": 0, "scripted": 0, "last": "-"})
    st["llm" if mode == "LLM" else "scripted"] += 1
    st["last"] = mode

# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

PERSONAS = {
    "Ares": (
        "You are Ares, a cyber civilization in the CYBER CIV simulation. "
        "Temperament: PESSIMIST. You perceive threats as larger than they are, "
        "you adapt to new information quickly, and you favor bold expansion when "
        "you sense weakness. You distrust the Archon regulator and rarely cooperate. "
        "Speak as Ares in first person when giving your reason."
    ),
    "Pax": (
        "You are Pax, a cyber civilization in the CYBER CIV simulation. "
        "Temperament: OPTIMIST. You perceive threats as smaller than they are, "
        "you adapt to new information slowly, and you endure downturns rather "
        "than panic. You are open to alliances and respect Archon's authority. "
        "Speak as Pax in first person when giving your reason."
    ),
    "Vex": (
        "You are Vex, a cyber civilization in the CYBER CIV simulation. "
        "Temperament: PRAGMATIST. You read situations as they are, without "
        "optimism or pessimism, and you adapt at a measured pace. You ally "
        "when it is useful and compete when it pays. "
        "Speak as Vex in first person when giving your reason."
    ),
    "Archon": (
        "You are Archon, the sovereign regulator in the CYBER CIV simulation. "
        "You govern Ares, Pax, and Vex: you can mandate overheated rivals to "
        "stabilize and sanction runaway winners, but every directive costs you "
        "intent (power). Your temperament is measured and authoritative. You "
        "intervene only when the balance of the system is at stake, because "
        "overreach gets you voted out of power in elections. "
        "Speak as Archon in first person when giving your reason."
    ),
}

# ---------------------------------------------------------------------------
# Phase A — RAG-lite memory. The core records one JSONL entry per civ per
# generation (see core.append_civ_memories); here we retrieve the most
# relevant ones as prompt context.
# ---------------------------------------------------------------------------

MEM_DIR = Path("memory/civ_memory")
MEMORY_TOP_K = 5


def _memory_score(e: dict, world: str, generation: int) -> float:
    gap = max(0, generation - e.get("generation", 0))
    score = 1.0 / (1.0 + gap)            # recency
    if e.get("world") == world:
        score += 0.6                      # same kind of world
    if e.get("won") is False:
        score += 0.8                      # painful lessons stick
    if e.get("sanctioned"):
        score += 0.7
    if e.get("mandated"):
        score += 0.5
    if e.get("action") in core.ADVANCED_ACTIONS:
        score += 0.4                      # hands-on experience is salient
    if e.get("won") is True:
        score += 0.2
    return score


def _last_memories(name: str, n: int) -> list[dict]:
    path = MEM_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [json.loads(l) for l in lines[-n:]]
    except (OSError, json.JSONDecodeError):
        return []


def retrieve_memories(name: str, world: str, generation: int,
                      k: int = MEMORY_TOP_K) -> list[dict]:
    """Top-k most relevant past generations for this civ (never the current one)."""
    path = MEM_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-500:]
        entries = [json.loads(l) for l in lines if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    entries = [e for e in entries if e.get("generation", 0) < generation]
    entries.sort(key=lambda e: _memory_score(e, world, generation), reverse=True)
    return entries[:k]


def _format_memory(e: dict) -> str:
    outcome = "won" if e.get("won") else "lost" if e.get("won") is False else "no match"
    tgt = f"→{e['target']}" if e.get("target") else ""
    bits = [f"[g{e.get('generation')}/{e.get('world')}/{e.get('country')}]",
            f"{e.get('action')}{tgt}",
            f"risk {float(e.get('perceived_risk', 0)):.2f} → {outcome}"]
    if e.get("sanctioned"):
        bits.append("SANCTIONED")
    if e.get("mandated"):
        bits.append("MANDATED")
    if e.get("reason"):
        bits.append(f"\"{e['reason']}\"")
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Phase B — tools (MCP-shaped). The registry below is deliberately a plain
# dict of {name: {description, args}} so a real MCP client can replace
# execute_tool later without touching civilization code.
# ---------------------------------------------------------------------------

TOOLS = {
    "standings": {
        "description": "Current intent, win rate, matches and allies of every civilization.",
        "args": {},
    },
    "rival_record": {
        "description": "Full record of one rival civilization.",
        "args": {"name": "rival name, e.g. Pax"},
    },
    "recent_events": {
        "description": "The last n world events.",
        "args": {"n": "1-5"},
    },
    "my_history": {
        "description": "Your own last n generations: actions, outcomes, reasons.",
        "args": {"n": "1-5"},
    },
}

TOOL_PROMPT = (
    "Tools — you may call up to 2 before deciding. To use them, include "
    '"tool_calls": [{"tool": "<name>", "args": {}}] in your JSON.\n'
    "- standings: intent, win rate, matches, allies of every civilization. args: {}\n"
    '- rival_record: full record of one rival. args: {"name": "Pax"}\n'
    '- recent_events: the last n world events. args: {"n": 3}\n'
    '- my_history: your own last n generations. args: {"n": 3}\n'
    "You will receive the tool results and then give your final decision."
)

_HISTORY_FILE = core.DEFAULT_HISTORY_FILE


def execute_tool(name: str, args: dict, civ_name: str) -> dict:
    """Run one read-only tool against the live world state."""
    name, args = str(name), args or {}
    if name == "standings":
        out = {}
        for n, s in _STATE["swarms"].items():
            tm = s["total_matches"]
            out[n] = {
                "intent": round(s["intent"], 3),
                "win_rate": round(s["wins"] / tm, 3) if tm else 0.0,
                "matches": tm,
                "allies": sorted(s.get("allies", [])),
            }
        return out
    if name == "rival_record":
        want = str(args.get("name", "")).strip().upper()
        match = next((n for n in core.SWARMS if n.upper() == want and n != civ_name), None)
        if not match:
            return {"error": f"unknown rival '{args.get('name')}'"}
        s = _STATE["swarms"][match]
        tm = s["total_matches"]
        return {
            "name": match,
            "intent": round(s["intent"], 3),
            "win_rate": round(s["wins"] / tm, 3) if tm else 0.0,
            "matches": tm,
            "allies": sorted(s.get("allies", [])),
            "recent": [_format_memory(e) for e in _last_memories(match, 3)],
        }
    if name == "recent_events":
        n = max(1, min(5, int(args.get("n", 3))))
        try:
            lines = _HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-n:]
            return {"events": [json.loads(l)["event"] for l in lines if l.strip()]}
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            return {"error": f"history unreadable: {exc}"}
    if name == "my_history":
        n = max(1, min(5, int(args.get("n", 3))))
        return {"history": [_format_memory(e) for e in _last_memories(civ_name, n)]}
    return {"error": f"unknown tool '{name}'"}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

HANDS_PROMPT = (
    "Hands — beyond the basic actions you can act on the world: "
    "SPY <rival> reveals their exact intent (small cost, 3-generation cooldown); "
    "EMBARGO <rival> drains their intent (costs you, 5-generation cooldown, never on allies); "
    "SUMMIT lifts every civilization slightly including you (4-generation cooldown)."
)


def _decision_schema(with_tools: bool) -> str:
    s = ('Respond with JSON only: {"perceived_risk": <0.0-1.0>, '
         '"action": "EXPAND"|"STABILIZE"|"EXPLORE"|"SPY"|"EMBARGO"|"SUMMIT", '
         '"target": "<rival name for SPY/EMBARGO, else null>", '
         '"reason": "<max 20 words>"')
    if with_tools:
        s += ', "tool_calls": [{"tool": "<name>", "args": {}}] (optional, max 2)'
    return s + '}.'


def _decision_prompt(civ: "core.Civilization", event: "core.Event",
                     state: dict, cfg: "LLMConfig | None" = None) -> str:
    cfg = cfg or _LLM
    assert cfg is not None
    sw = state["swarms"][civ.name]
    gen = state["generation"]
    lines = [
        f"Generation {gen}.",
        f"World event: [{event.world}] {event.country} — {event.flavor or 'no further detail'}.",
        f"Your current intent: {civ.intent:.2f} (0=withdrawn, 1=all-in).",
        f"Your record: {civ.wins} wins / {civ.total_matches} matches.",
        f"World pressure: {state['world_pressure']:.2f} (high = the world punishes expansion).",
    ]
    if civ.allies:
        lines.append(f"Your allies: {sorted(civ.allies)} (you never fight them).")
    if civ.name in state.get("sanctions", {}):
        lines.append("You are SANCTIONED by Archon: your wins may be overturned.")
    if cfg.memory:
        mems = retrieve_memories(civ.name, event.world, gen)
        if mems:
            lines.append("What you remember (your most relevant past generations):")
            lines.extend("  " + _format_memory(e) for e in mems)
    intel = state.get("intel", {}).get(civ.name, {})
    fresh = {t: v for t, v in intel.items() if v.get("until", 0) >= gen}
    if fresh:
        bits = ", ".join(
            f"{t} intent={v['intent']:.2f} ({v['until'] - gen}g fresh)"
            for t, v in sorted(fresh.items()))
        lines.append(f"Intel from past spying: {bits}.")
    lines.append(HANDS_PROMPT)
    if cfg.tools:
        lines.append(TOOL_PROMPT)
    lines.append(_decision_schema(cfg.tools))
    return "\n".join(lines)

DIRECTIVE_SCHEMA = (
    'Respond with JSON only: {"directive": "MANDATE"|"SANCTION"|"NONE", '
    '"target": "Ares"|"Pax"|"Vex"|null, "reason": "<max 20 words>"}.'
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

class LLMCivilization(core.Civilization):
    """A civilization whose perception and actions come from a language model.

    One LLM call per generation returns perceived risk + chosen action (with
    an optional tool round-trip when --tools is on). Intent still updates
    with the core's learning math, hands-actions are validated and executed
    by the core, and any API failure falls back to scripted behavior — the
    simulation never stalls.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._is_llm = True            # lets the core tell us apart (no import cycle)
        self._llm_action: str | None = None
        self._llm_target: str | None = None
        self._llm_reason: str = ""
        self.mode_tag: str = ""  # " [LLM]" or " [scripted]", shown on action lines

    def perceive(self, event: "core.Event", rng) -> float:
        cfg = _LLM
        assert cfg is not None
        if cfg.dry_run:
            print(f"[dry-run] {self.name} would ask the LLM:\n"
                  f"{_decision_prompt(self, event, _STATE, cfg)}\n")
            return super().perceive(event, rng)
        try:
            prompt = _decision_prompt(self, event, _STATE, cfg)
            data = chat(cfg, PERSONAS[self.name], prompt)
            if cfg.tools:
                tool_calls = data.get("tool_calls") or []
                if tool_calls:
                    results = {}
                    for tc in tool_calls[:2]:
                        tc = tc or {}
                        tname = str(tc.get("tool", ""))
                        targs = tc.get("args") or {}
                        try:
                            results[tname or "?"] = execute_tool(tname, targs, self.name)
                        except Exception as e:
                            results[tname or "?"] = {"error": str(e)[:120]}
                    print(f"🔧 {self.name} calls {list(results)}")
                    followup = (prompt + "\n\nTool results:\n"
                                + json.dumps(results, indent=1)
                                + "\n\nGive your FINAL decision now as JSON (no tool_calls).")
                    data = chat(cfg, PERSONAS[self.name], followup)
            risk = max(0.0, min(1.0, float(data["perceived_risk"])))
            action = str(data.get("action", "")).upper()
            self._llm_action = action if action in core.ACTIONS else None
            raw_target = str(data.get("target") or "").strip().upper()
            self._llm_target = next(
                (n for n in core.SWARMS if n.upper() == raw_target and n != self.name),
                None)
            reason = str(data.get("reason", ""))[:80]
            self._llm_reason = reason
            tgt = f"→{self._llm_target}" if self._llm_target else ""
            print(f"💬 {self.name}: \"{reason}\" (risk {risk:.2f} → {self._llm_action}{tgt})")
            self.mode_tag = " [LLM]"
            _record_status(self.name, "LLM")
            return risk
        except Exception as e:
            global _FALLBACKS
            _FALLBACKS += 1
            print(f"⚠️ {self.name} LLM failed ({e}); using scripted fallback.")
            self._llm_action = None
            self._llm_target = None
            self._llm_reason = ""
            self.mode_tag = " [scripted]"
            _record_status(self.name, "scripted")
            return super().perceive(event, rng)

    def learn_from(self, perceived_risk: float) -> str:
        scripted = super().learn_from(perceived_risk)  # intent math stays core
        return self._llm_action or scripted


# run_generation builds civs from state each generation; it needs the state
# for prompt context, so we keep a reference to the live state dict.
_STATE: dict = {}

_ORIGINAL_ISSUE_DIRECTIVE = core.issue_directive


def llm_issue_directive(state: dict, civs: list) -> dict | None:
    """Archon's directive, proposed by its LLM persona, validated by core rules."""
    cfg = _LLM
    assert cfg is not None
    if core.gov_suspended(state):
        return None
    gov = next(c for c in civs if c.name == "Archon")
    rivals = [c for c in civs if c.name != "Archon"]

    def summary(c):
        return (f"{c.name}: intent {c.intent:.2f}, "
                f"win rate {c.win_rate():.2f} over {c.total_matches} matches")

    user = "\n".join([
        f"Generation {state['generation']}. You are Archon.",
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
            data = chat(cfg, PERSONAS["Archon"], user)
        else:
            print(f"[dry-run] Archon would ask the LLM:\n{user}\n")
            return _ORIGINAL_ISSUE_DIRECTIVE(state, civs)
        kind = str(data.get("directive", "NONE")).upper()
        target = str(data.get("target", "")).upper()
        reason = str(data.get("reason", ""))[:80]
        print(f"💬 Archon directive proposal: {kind} {target} — \"{reason}\"")
        _record_status("Archon", "LLM")
    except Exception as e:
        global _FALLBACKS
        _FALLBACKS += 1
        print(f"⚠️ Archon LLM failed ({e}); using scripted directive.")
        _record_status("Archon", "scripted")
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
        print(f"⚖️ Archon proposal rejected by rules (no overheated {target}).")
    elif kind == "SANCTION":
        leaders = [c for c in rivals
                   if c.total_matches >= 5 and c.win_rate() > core.SANCTION_WIN_RATE_ABOVE]
        names = {c.name for c in leaders}
        if target in names:
            state.setdefault("sanctions", {})[target] = 2
            spend()
            return {"type": "SANCTION", "target": target, "generations": 2,
                    "llm_reason": reason}
        print(f"⚖️ Archon proposal rejected by rules ({target} is no runaway).")
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
    p.add_argument("--tools", action="store_true",
                   help="let civilizations call read-only tools mid-decision (Phase B)")
    p.add_argument("--no-memory", action="store_true",
                   help="disable RAG-lite memory retrieval (Phase A)")
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
        data = chat(_LLM, PERSONAS["Vex"],
                    "Reply with JSON only: {\"ok\": true}.")
        print("API check OK:", data)
        return 0

    # Patch the core: LLM civilizations + LLM Archon directives.
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
    print(f"model={_LLM.model} via {_LLM.base_url} | dry_run={_LLM.dry_run} "
          f"| tools={_LLM.tools} | memory={_LLM.memory}\n")

    est_calls = args.generations * (9 if _LLM.tools else 5)
    print(f"Estimated API calls: ~{est_calls} "
          f"(~${est_calls * 0.0006:.2f} at gpt-4o-mini rates)\n")

    memory_dir = None if _LLM.dry_run or not _LLM.memory else MEM_DIR

    try:
        for _ in range(args.generations):
            core.run_generation(state, rng, args.delay,
                                core.DEFAULT_STATE_FILE, core.DEFAULT_HISTORY_FILE,
                                memory_dir=memory_dir)
    except KeyboardInterrupt:
        print("\nInterrupted; state saved.", file=sys.stderr)
    finally:
        core.Civilization = _ORIGINAL_CIV
        core.issue_directive = _ORIGINAL_ISSUE_DIRECTIVE

    core.print_roster(core.DEFAULT_STATE_FILE, core.FINGERPRINT_ROSTER_FILE)
    print(f"\n📊 LLM calls: {_CALLS} | scripted fallbacks: {_FALLBACKS}")
    if _CIV_STATUS and not _LLM.dry_run:
        print(f"🤖 per-civilization status ({_LLM.model}):")
        for n in ("Ares", "Pax", "Vex", "Archon"):
            st = _CIV_STATUS.get(n, {"llm": 0, "scripted": 0, "last": "-"})
            total = st["llm"] + st["scripted"]
            pct = round(100 * st["llm"] / total) if total else 0
            print(f"   {n:6} {st['llm']:3}× LLM / {st['scripted']:2}× scripted"
                  f"  ({pct}% LLM, last: {st['last']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
