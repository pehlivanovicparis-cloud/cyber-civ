# Cyber CIV fine-tune — process record (2026-10-15, Kaggle QLoRA run)

Actual execution log for the first Cyber CIV fine-tune. Companion to
FINETUNE_RUNBOOK.md (which planned a rented-GPU run; this run used Kaggle instead).

## Setup
- Notebook: `parispehlivanovic/notebook841de1be49` (Kaggle)
- Model: `unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit`, 4-bit QLoRA
- LoRA: r=64, alpha=16, dropout 0; targets q/k/v/o + gate/up/down; `random_state=42`
- Data: `train_chat.jsonl` (2,987 rows) → `train_test_split(test_size=0.10, seed=42)` →
  2,688 train / 299 eval. Rows are `{"messages": [...]}`; training text built by
  `tok.apply_chat_template(messages, tokenize=False)` (model's built-in template, no override).
- Hyperparams: lr 2e-5, 3 epochs (504 steps), eval every 50 steps, checkpoints every
  25 steps, `save_total_limit=2`. Baseline: fixed-split eval loss 4.287947177886963.

## Session deaths and resumes (3)
Kaggle killed the session 3 times mid-training (steps 6, ~51–67, 225). No error text —
output froze and the session sat at "Session is starting...". Resume-from-latest-checkpoint
worked every time (optimizer/scheduler/RNG state all in checkpoints; seed-pinned split
regenerates identically). Notable: on the third death the frontend froze at step 75 while
the backend kept training to 225 — always verify via checkpoint files on disk, not the
progress bar.

## Seed fix (the baseline drift)
First two runs showed baselines of 3.0962 vs 4.2883 on supposedly identical data.
Root cause: the split used global `random.shuffle(rows)` — never seeded, so the eval set
changed every run. Fix: `train_test_split(test_size=0.10, seed=SEED)` with `SEED=42`
(explicit local RNG), plus seed logging in cell output. Fresh start from step 0 after the
fix (old checkpoint trained on an unknown split).

## Hardening (applied to cells 4–7 while training ran; took effect on next launch)
- `baseline.json` in OUT: `{base_loss, SEED, eval_fp, train_fp}`. Written once by cell 5;
  re-runs fail loudly on mismatch instead of silently moving the keep bar. Cell 7 loads
  the baseline from disk, never from memory.
- Fingerprints: length-prefixed MD5 helper `fp(ds)` over all train/eval texts.
  Cell 6 asserts `eval_fp` and `SEED` match on resume (raise, not assert) and refuses
  to train on drift. Cell 7 re-verifies `eval_fp` before the keep/reject comparison.

## Training result
504/504 steps (~19:01–20:15 EDT after final resume). Final eval loss 0.266393
vs 0.95×baseline ≈ 4.0735 — passes the loss gate by ~15x. Eval curve smooth and
monotonic (250: 0.2925 → 504: 0.2664), no overfitting spike. Final train loss 0.148.

## Evaluation (why loss alone was not trusted)
1. **Overlap analysis** (3 tiers, live kernel): exact 0/299, normalized 0/299,
   char-5-gram Jaccard ≥ 0.8: **286/299 near-dupes**. Sequential-generation data means
   eval rows' siblings land in train — the 16x loss drop is largely near-duplicate
   memorization, not generalization. Lesson: check overlap BEFORE training next time.
2. **Spot checks** (cell 9, side-by-side base vs tuned, greedy, seed 42, N=13 clean rows):
   base format 0% (prose rambling, never valid JSON); tuned format 92.3% (12/13 —
   one output was a JSON string, not an object). Valid actions per training data:
   EXPAND / STABILIZE dominate (train 1340/1332, eval 154/144); EXPLORE/SPY rare;
   EMBARGO/SUMMIT absent from training entirely.
3. **EXPAND-bias investigation**: greedy tuned outputs picked EXPAND 12/13, but training
   data is 50/50 — not a learned prior. Sampled pass (3×13 @ T=0.7): EXPAND 19 /
   STABILIZE 14 / non-dict JSON 6 — the model DOES discriminate; greedy amplified a
   modest lean. Not mode collapse. (15% non-dict JSON under sampling: format softens
   at temperature.)

## Verdict: CONDITIONAL KEEP (pre-registered criteria, locked before outputs)
- Loss gate: passed numerically (unreliable per overlap finding).
- Overlap gate: 286 rows excluded, 13 clean used, counts recorded. ✓
- Format gate: 92.3% vs 0% base — misses 95% letter by one row (N=13 granularity),
  margin leg passes by 92 points.
- Content gate: no collapse; sampled action mix tracks the data; outputs in-character.
- Adapter saved: `/kaggle/working/cyber-civ-lora/final/` (adapter_model.safetensors,
  adapter_config.json, tokenizer files, chat_template.jinja) — cell 8.

## Open / next
- Definitive eval needs fresh-harvest held-out data (structurally distinct prompts).
- Deployment (wiring the adapter into the sim) is a direction call, not yet made.
- EMBARGO/SUMMIT have zero training coverage — the adapter cannot have learned them.
