#!/usr/bin/env python3
"""Convert Cyber CIV decision records (civ_memory JSONL) into Soup training data.

Sources: the repo's memory/civ_memory/*.jsonl plus any run directories passed
on the command line (e.g. /tmp/civtest8b). Records are deduped across sources.

Outputs (in training/data/):
  train.jsonl       Alpaca format  {"instruction", "input", "output"}
  train_chat.jsonl  Chat format    {"messages": [system, user, assistant]}
                    with the real persona text from cyber_civ_llm.PERSONAS.

Rerun any time the sim has produced more records:
  python3 training/convert_to_soup.py [/extra/run/dir ...]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "training" / "data"

INSTRUCTION = (
    "You are {name}, a cyber civilization in the CYBER CIV simulation. "
    "Given the current world state, choose your action for this generation. "
    'Respond with a JSON object: {{"action": one of EXPAND, EXPLORE, '
    'STABILIZE, SPY, EMBARGO, SUMMIT, "target": a rival name or null, '
    '"reason": one sentence in character}}.'
)


def load_personas() -> dict[str, str]:
    """Pull the real PERSONAS system prompts out of cyber_civ_llm.py."""
    tree = ast.parse((ROOT / "cyber_civ_llm.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PERSONAS" for t in node.targets
        ):
            try:
                return ast.literal_eval(node.value)
            except Exception:
                break
    return {}


def state_text(r: dict) -> str:
    allies = ", ".join(r.get("allies") or []) or "none"
    return (
        f"Generation {r['generation']}. World event: {r['world']} "
        f"(focus {r.get('country')}), event risk {r.get('event_risk')}. "
        f"Your perceived risk: {r.get('perceived_risk')}. "
        f"Your current intent: {r.get('intent_after')}. Allies: {allies}. "
        f"Sanctioned: {r.get('sanctioned')}. Under mandate: {r.get('mandated')}. "
        f"In an active match: {r.get('in_match')}."
    )


def main() -> None:
    sources = [ROOT / "memory" / "civ_memory"] + [
        Path(p) / "memory" / "civ_memory" for p in sys.argv[1:]
    ]
    seen: set[str] = set()
    records: list[tuple[str, dict]] = []
    for src in sources:
        if not src.is_dir():
            continue
        for f in sorted(src.glob("*.jsonl")):
            civ = f.stem
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line or line in seen:
                    continue
                seen.add(line)
                try:
                    records.append((civ, json.loads(line)))
                except json.JSONDecodeError:
                    continue

    personas = load_personas()
    OUT.mkdir(parents=True, exist_ok=True)
    alpaca, chat = [], []
    for civ, r in records:
        out = json.dumps(
            {
                "action": r.get("action"),
                "target": r.get("target"),
                "reason": r.get("reason") or "",
            }
        )
        state = state_text(r)
        alpaca.append(
            {
                "instruction": INSTRUCTION.format(name=civ),
                "input": state,
                "output": out,
            }
        )
        chat.append(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": personas.get(
                            civ, f"You are {civ}, a cyber civilization in the CYBER CIV simulation."
                        ),
                    },
                    {"role": "user", "content": state},
                    {"role": "assistant", "content": out},
                ]
            }
        )

    (OUT / "train.jsonl").write_text(
        "\n".join(json.dumps(x) for x in alpaca) + "\n"
    )
    (OUT / "train_chat.jsonl").write_text(
        "\n".join(json.dumps(x) for x in chat) + "\n"
    )
    llm = sum(1 for _, r in records if r.get("mode") == "[LLM]" and r.get("reason"))
    print(f"records (deduped): {len(records)}  | with LLM reasoning: {llm}")
    print(f"wrote {OUT / 'train.jsonl'}")
    print(f"wrote {OUT / 'train_chat.jsonl'}")


if __name__ == "__main__":
    main()
