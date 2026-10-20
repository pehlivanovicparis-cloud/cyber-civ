# Cyber CIV — RUNBOOK

Copyright (c) 2026 Paris Pehlivanovic. All rights reserved. PROPRIETARY —
see [LICENSE](LICENSE). (This runbook is an operational doc; the public
README is intentionally left untouched.)

## 0. Ground rules

- Real code/site branch is **`master`**. `main` is stale (SECURITY.md +
  Search Console file only) — do not work on it, do not flip the default
  branch.
- **GitHub Pages is served from `master`, path `/` (legacy).** Everything
  at repo root and under `docs/` is potentially web-served. Never edit
  `docs/*.html`, `dashboard.html`, `promo/**`, `README.md`, or `LICENSE`
  as part of a code change. Site changes ship separately, deliberately.
- Python ≥ 3.10. **Stdlib only** — `pip install -r requirements.txt` is a
  no-op by design (see that file). No secrets in git, ever: API keys and
  Stripe secrets are environment variables only (`.gitignore` blocks
  `.env*`, `*.key`, `*.pem`, `credentials*`, `secrets*`).

## 1. Run the simulation

Scripted (no LLM, verified core engine):

```bash
python3 cyber_civ.py --generations 20
```

LLM-driven (any OpenAI-compatible endpoint, e.g. local Ollama):

```bash
export CYBER_CIV_API_KEY=...            # never commit this
export CYBER_CIV_BASE_URL=http://localhost:11434/v1   # example: Ollama
export CYBER_CIV_MODEL=llama3.1:8b
python3 cyber_civ_llm.py --generations 20
python3 cyber_civ_llm.py --dry-run --generations 3   # prompts only, no API
python3 cyber_civ_llm.py --check                     # one test API call
```

Timeout & fallback — read this before tuning:

- `CYBER_CIV_TIMEOUT` defaults to **120s**. Do not lower it toward the old
  25s default: at 25s local models timed out into scripted fallback
  (8B scored 0% real-LLM decisions at 25s vs 93% at 300s). Harvest systemd
  units use 300s.
- Non-strict runs fall back to scripted play on LLM failure and say so
  (`⚠️ … using scripted fallback`, `[scripted]` tags, end-of-run counts).
- **Harvests must use `--strict`**: the run halts (exit 3) after 3
  consecutive LLM failures or a >25% failure rate over ≥20 calls, rather
  than silently generating scripted data.

## 2. Fine-tuned adapter — readiness gate (fail loud)

The 2026-10-15 QLoRA adapter lives (locally, git-ignored) at
`training/adapters/cyber-civ-lora-2026-10-15/`. Its weights file
`adapter_model.safetensors` (~361 MB) is **not in git and not local** —
it exists only on Kaggle until Paris downloads it from the notebook's
Output tab into that directory. That download is an owner step; no
automation can do it.

```bash
python3 adapter_check.py              # status: READY / INCOMPLETE / MISSING
python3 adapter_check.py --require    # exit 2 unless genuinely READY
python3 cyber_civ_llm.py --require-adapter --generations 20   # gated run
# or: CYBER_CIV_REQUIRE_ADAPTER=1 python3 cyber_civ_llm.py ...
```

Rules: a run may only claim the tuned model when the gate says READY
(all required files present, config parses as LoRA, weights ≥ 50 MB —
placeholders/truncated downloads fail). Without `--require-adapter`,
runs proceed on the base model and must be described that way.

## 3. Test

Exact commands (stdlib only; pytest is NOT required and the rental tests
are script-style):

```bash
python3 rental/test_stripe_webhook.py   # expect: ALL 24 TESTS PASSED
python3 rental/test_server.py           # expect: ALL 14 TESTS PASSED
python3 -m unittest test_adapter_check  # expect: Ran 10 tests … OK
python3 verify_balance.py               # expect: RESULT: PASS (spread ≤15%)
```

Known gap (accepted, tracked): no unit tests yet for `cyber_civ.py` core
mechanics or `cyber_civ_llm.py` parsing — `verify_balance.py` is the
core's regression gate until those exist.

## 4. Deploy / rollback

- **Website (Pages):** pushing to `master` deploys automatically
  (Actions → "pages build and deployment"). There is no staging. To roll
  back a bad site change: `git revert <sha>` on master and push; verify
  the Pages build goes green. Because code-only pushes also trigger a
  Pages build, keep master green at all times.
- **Simulation/harvest hosts:** systemd units in `systemd/`
  (`civ-harvest`, `civ-harvest8b`, `harvest-toggle`, `ollama-serve`);
  `systemd/restore-units.sh` restores them after a host replacement.
- **Rental backend:** see `rental/deploy/DEPLOY.md` (droplet, Caddy,
  webhook secret via env only).

## 5. CI — PROPOSED, needs a web-UI step (not yet live)

There is currently **no test CI** — only Pages builds. The automation
token available to agents has no `workflow` scope, so the workflow below
must be added by Paris once via the GitHub web UI
(**Add file → Create new file → `.github/workflows/ci.yml`**), pasting:

```yaml
name: ci
on:
  push: { branches: [master] }
  pull_request: { branches: [master] }
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python3 rental/test_stripe_webhook.py
      - run: python3 rental/test_server.py
      - run: python3 -m unittest test_adapter_check
      - run: python3 verify_balance.py
      - run: python3 adapter_check.py   # informational; must not fail CI
```

Until that lands, §3 run locally before every push to master is the CI.

## 6. Hygiene checklist before pushing to master

1. §3 all green, exact counts recorded.
2. `git status` — no weights, caches, harvest data, or `.env*` staged
   (`.gitignore` covers `training/adapters/`, `training/harvest*/`,
   `training/output/`, `.pytest_cache/`, venvs).
3. Grep the diff for secret-shaped material (Stripe/GitHub key prefixes,
   PEM headers, assigned API secrets) — placeholders in docs/tests are
   fine, real values are a stop-and-rotate event.
4. No changes to site-critical files (§0) unless the push *is* the site
   change, made deliberately.
