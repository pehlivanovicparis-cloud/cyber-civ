#!/usr/bin/env python3
"""
adapter_check.py — readiness gate for the Cyber CIV fine-tuned LoRA adapter.

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved.
PROPRIETARY — see LICENSE for terms.

Why this exists
---------------
The 2026-10-15 Kaggle QLoRA run produced an adapter whose small files
(adapter_config.json, tokenizer files, chat template) are local under
training/adapters/cyber-civ-lora-2026-10-15/, but whose weights file —
adapter_model.safetensors (~361 MB) — is NOT local; it must be downloaded
from Kaggle by Paris. Nothing may claim "the tuned model ran" while that
file is absent. This module is the honest gate:

  * check_adapter()  -> AdapterStatus (READY / INCOMPLETE / MISSING)
  * require_adapter()-> returns the status if READY, otherwise raises
                        AdapterNotReadyError with a loud, specific message.
  * CLI:  python3 adapter_check.py [--dir DIR] [--require]
          exit 0 = ready (or, without --require, status printed),
          exit 2 = --require was given and the adapter is not ready.

Stdlib only, like the rest of the codebase. Detection is structural only:
expected files present, adapter_config.json parses and declares a LoRA
adapter, and the weights file is at least MIN_WEIGHTS_BYTES (a real
361 MB-class file; a placeholder / LFS pointer / truncated download fails).
It does NOT load the model — loading stays with whatever serving stack
(Ollama / vLLM / PEFT) Paris chooses; this gate only stops a run from
pretending the tuned adapter is in play when it is not.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_ADAPTER_DIR = ROOT / "training" / "adapters" / "cyber-civ-lora-2026-10-15"

WEIGHTS_FILE = "adapter_model.safetensors"
# The real weights are ~361 MB. 50 MB is a conservative floor: any genuine
# Llama-3.1-8B LoRA (r=64) weights file clears it by a wide margin, while
# placeholders, git-LFS pointers and truncated downloads do not.
MIN_WEIGHTS_BYTES = 50 * 1024 * 1024

REQUIRED_FILES = (
    WEIGHTS_FILE,
    "adapter_config.json",
    "tokenizer_config.json",
    "tokenizer.json",
)
OPTIONAL_FILES = ("chat_template.jinja", "README.md")


class AdapterNotReadyError(RuntimeError):
    """Raised by require_adapter() when the tuned adapter cannot be used."""


@dataclass
class AdapterStatus:
    adapter_dir: Path
    state: str  # "READY" | "INCOMPLETE" | "MISSING"
    present: dict[str, int] = field(default_factory=dict)  # name -> size bytes
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.state == "READY"

    def summary(self) -> str:
        lines = [f"adapter dir: {self.adapter_dir}", f"state: {self.state}"]
        for name, size in sorted(self.present.items()):
            lines.append(f"  present: {name} ({size} bytes)")
        for name in self.missing:
            lines.append(f"  MISSING: {name}")
        for problem in self.problems:
            lines.append(f"  problem: {problem}")
        return "\n".join(lines)


def check_adapter(
    adapter_dir: Path | str = DEFAULT_ADAPTER_DIR,
    *,
    min_weights_bytes: int = MIN_WEIGHTS_BYTES,
) -> AdapterStatus:
    """Inspect an adapter directory without loading anything."""
    d = Path(adapter_dir)
    status = AdapterStatus(adapter_dir=d, state="MISSING")
    if not d.is_dir():
        status.missing = list(REQUIRED_FILES)
        status.problems.append(
            "adapter directory does not exist — the fine-tuned adapter "
            "has not been placed here"
        )
        return status

    for name in REQUIRED_FILES + OPTIONAL_FILES:
        p = d / name
        if p.is_file():
            status.present[name] = p.stat().st_size
        elif name in REQUIRED_FILES:
            status.missing.append(name)

    if WEIGHTS_FILE in status.present:
        size = status.present[WEIGHTS_FILE]
        if size < min_weights_bytes:
            status.problems.append(
                f"{WEIGHTS_FILE} is only {size} bytes "
                f"(< {min_weights_bytes} minimum) — this looks like a "
                "placeholder or truncated download, not the real "
                "~361 MB weights file from the Kaggle run"
            )
    elif WEIGHTS_FILE not in status.missing:
        status.missing.append(WEIGHTS_FILE)
    if WEIGHTS_FILE in status.missing:
        status.problems.append(
            f"{WEIGHTS_FILE} is absent — it is the ~361 MB weights file "
            "that still lives only on Kaggle; Paris must download it "
            "from the notebook Output tab and place it in this directory"
        )

    config_path = d / "adapter_config.json"
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            status.problems.append(f"adapter_config.json does not parse: {exc}")
        else:
            if cfg.get("peft_type") != "LORA":
                status.problems.append(
                    "adapter_config.json does not declare peft_type=LORA "
                    f"(got {cfg.get('peft_type')!r})"
                )

    if not status.missing and not status.problems:
        status.state = "READY"
    else:
        status.state = "INCOMPLETE"
    return status


def require_adapter(
    adapter_dir: Path | str = DEFAULT_ADAPTER_DIR,
    *,
    min_weights_bytes: int = MIN_WEIGHTS_BYTES,
) -> AdapterStatus:
    """Fail loud unless the adapter is genuinely ready. Returns status."""
    status = check_adapter(adapter_dir, min_weights_bytes=min_weights_bytes)
    if not status.ready:
        raise AdapterNotReadyError(
            "FINE-TUNED ADAPTER NOT READY — refusing to run as if the "
            "tuned model were in use.\n"
            + status.summary()
            + "\nFix: download adapter_model.safetensors from the Kaggle "
            "fine-tune notebook's Output tab into the adapter directory, "
            "or run without requiring the adapter (the base/scripted sim "
            "will be used and labelled as such)."
        )
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check Cyber CIV LoRA adapter readiness")
    ap.add_argument("--dir", default=str(DEFAULT_ADAPTER_DIR),
                    help="adapter directory (default: %(default)s)")
    ap.add_argument("--require", action="store_true",
                    help="exit non-zero unless the adapter is READY")
    args = ap.parse_args(argv)
    status = check_adapter(args.dir)
    print(status.summary())
    if args.require and not status.ready:
        print("\n🛑 ADAPTER NOT READY (see above). No tuned model will be used.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
