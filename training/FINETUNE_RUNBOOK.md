# Cyber CIV soup fine-tune — runbook (draft, prepared 2026-10-14)

## What's ready
- Dataset: `training/data/train.jsonl` + `training/data/train_chat.jsonl` — 2,987 LLM-only
  examples each (verified 2026-10-14: all lines parse, Alpaca `instruction/input/output` keys).
- Config: `training/soup.yaml` — base `meta-llama/Llama-3.1-8B-Instruct`, SFT, LoRA r=64/alpha=16,
  4-bit, 3 epochs, val_split 0.1, alpaca format.
- Tool: soup-cli 0.75.2 installed here at `~/.local/bin/soup` (trains locally via QLoRA;
  this VM has no GPU, so the run belongs on a rented box).
- Cost: `soup cost --config soup.yaml` (run 2026-10-14 against the real dataset) estimates
  **~$2–8 total** — Vast.ai RTX 4090 $0.35/hr ≈ $2.09 (6h); RunPod A100 80G $1.89/hr ≈ $7.88 (4h);
  Lambda A100 40G ≈ $4.59. Spot/interruptible cuts more. Marketplace prices move — confirm in the
  provider console at booking time.

## Steps (all on the rented GPU box)
1. **Rent one GPU.** RunPod (Community $0.34/hr or Secure $0.89/hr, RTX 4090, checked 2026-10-14)
   or Vast.ai (~$0.30–0.35/hr 4090). PyTorch/CUDA template. This is your spend call and your
   signup — same as the DigitalOcean route.
2. **Upload** `training/soup.yaml` and `training/data/` (train.jsonl is ~2 MB).
3. **Install:** `pipx install "soup-cli[train]"` (same install route that worked here).
4. **Llama license:** accept the Llama 3.1 license on huggingface.co and set a token with access —
   your click, can't be done from here.
5. **Validate:** `soup train --config soup.yaml --dry-run` (checks config + data, no training).
6. **Train:** `soup train --config soup.yaml`.
7. **Export:** `soup export` to GGUF (Ollama route) — see `soup export --help` on the box.
8. **Ollama swap:** `ollama create cyberciv-finetuned -f <Modelfile pointing at the GGUF>`.
9. **Validate:** rerun `verify_balance.py` and the head-to-head against stock llama3.1:8b
   (the production plan's acceptance step — run here once the model is back).

## Caveats
- QLoRA on 8B fits 24 GB VRAM; no A100 needed unless you want speed.
- Don't leave the pod running idle — storage/egress can add small charges; terminate when done.
