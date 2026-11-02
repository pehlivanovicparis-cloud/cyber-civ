# Cyber CIV — Recovery Subsystem

A real-world backup / recovery system for banking, crypto, and
fintech accounts, built on the Cyber CIV recovery concepts and
shipped as a deployable subsystem of the Cyber CIV project.

It is the integrated version of the fintech recovery prototype:
the same engine (accounts, incidents, recovery pool, guard,
RecoveryAgent, deterministic fallback, hash-chained audit log),
plus a service layer (CLI + HTTP API) so it can actually be run.

**This is not the LLM RECOVER retry.** The retry (re-prompt on a
null-target RECOVER) was tested on 2026-11-01 and FAILED (2/10
valid, bar ≥7/10). It stays unmerged. This subsystem uses the
deterministic engine only: the guard validates every proposal,
and the fallback guarantees progress even when the agent fails.

## What it does

- **Accounts:** bank, crypto wallet, or fintech accounts, each with
  a balance and a currency.
- **Incidents:** a cyber incident (phishing, API breach, fraud)
  drains funds from a victim and freezes a portion in the
  Recovery Pool.
- **RecoveryAgent (Aegis):** each cycle it proposes a RECOVER naming
  the victim with the largest open frozen amount.
- **Guard:** validates every proposal. It rejects a null target, a
  victim with no open loss, an amount over the frozen remainder, a
  pool that cannot cover it, and proposals made while on cooldown.
- **Deterministic fallback:** if no RECOVER succeeds in a cycle, it
  auto-reimburses the oldest open loss, capped per cycle. Recovery
  makes progress even when the agent fails every time.
- **Recovery Wallet (`RECOVERY-WALLET`):** rescued funds are
  deposited here from the pool, then released to the victim — both
  steps logged (`WALLET_DEPOSIT`, `RECOVERED`). The wallet is a real
  account (visible in `/summary` and `/accounts`) and tracks
  cumulative `total_deposited` / `total_released`, so the flow of
  rescued funds is fully auditable. The victim is still paid
  immediately; the wallet balance returns to 0 after each release.
- **Send to an address:** `transfer()` sends funds from any account
  to a destination — an external wallet address (e.g. `0x...`) or
  another account id for an internal transfer. Every transfer is
  recorded in a transfers ledger and written to the audit log
  (`TRANSFER`), so the flow is fully traceable. Note: this moves
  balances inside the system; it does not broadcast a real on-chain
  transaction (a production deployment would hand the recorded
  transfer to a blockchain / banking rail to execute it).
- **Signal sensing (`signal.py`):** sense money from a mere signal,
  with no addresses or login details needed. Give it a transaction
  hash, a wallet address, a bank signal (`bank:<routing>:<account>`),
  or a bank reference (`ref:<reference>`) and it resolves it to
  on-chain / public data and fires signals (large transfer, no
  activity, invalid routing). Crypto sensing is full, from public
  on-chain data via a public Ethereum RPC. Banking sensing is
  public-signal only (routing-number validation); bank balances are
  private by design and need authorized bank API access. No private
  keys, no passwords, no logins — only the signal.
- **Signal wiring:** a `LARGE_TRANSFER` signal from a registered
  account auto-opens a recovery incident. Accounts carry an optional
  public `address` (wallet address or account number); the engine's
  `find_account_by_address` maps a sensed address to the account,
  and `report_signals_to_engine` opens the incident (stolen = the
  transfer value, capped at the victim's balance; frozen = 50%).
  The recovery cycle then recovers it through the wallet, like any
  other incident. Verified live on a real 22.2 ETH transfer.
- **Persistence (`state.py`):** the engine state lives in
  `recovery/state.json` and survives restarts. The service loads it
  on startup and saves it (atomically, temp file + rename) after
  every mutation — accounts, incidents, transfers, pool, wallet
  totals, and the full hash-chained audit log. The audit chain
  still verifies after a reload, and the guard's cooldown is
  preserved too. The state file is git-ignored (live operational
  data, never committed).
- **Dashboard (`dashboard.html`, `GET /dashboard`):** a web
  dashboard for individuals and banks — accounts with balances,
  recovery pool and wallet totals, open incidents, transfers, and
  the audit log, auto-refreshing every 10 seconds. It also has a
  signal-sensing form: type a signal (transaction hash, wallet
  address, bank signal, or reference) and it senses it via
  `POST /sense` (read-only, no state change). Served from the same
  origin as the API, so the browser's Basic Auth login covers it.
  Mobile-friendly.
- **Pool conservation:** funds in (frozen) == funds out (recovered).
  No funds are created or destroyed.
- **Audit log:** every action is written to an append-only log
  chained by SHA-256. Tampering with any entry breaks the chain and
  is detectable (`audit_chain_valid`).

## Quick start

Requires Python 3.10+ and nothing else (standard library only).

```bash
# from the Cyber CIV repo root
python3 -m recovery.cli demo      # end-to-end demo scenario
python3 -m recovery.cli test      # 61 tests (engine + HTTP API + auth + signal + wiring + persistence + dashboard/sense)
python3 -m recovery.cli serve     # API on http://127.0.0.1:8000
python3 -m recovery.cli sense <signal>   # sense money from a mere signal
```

## API

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/health` | liveness + audit chain status |
| GET | `/accounts` | list accounts |
| POST | `/accounts` | open an account `{"account_id","account_type","owner","balance","currency"}` |
| POST | `/incidents` | report an incident `{"victim_id","amount_stolen","amount_frozen","kind"}` |
| POST | `/cycles/run` | run one recovery cycle `{"force_null_target": false}` |
| POST | `/transfers` | send funds to a destination address `{"from_account_id","to_address","amount"}` |
| GET | `/transfers` | list outbound transfers |
| GET | `/summary` | full state (accounts, pool, recovery wallet, transfers, open incidents, audit status) |
| GET | `/audit` | the hash-chained audit log + chain validity |
| GET | `/` | API index |

Example (with the server running):

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/accounts \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"BANK-1","account_type":"bank","owner":"First National Bank","balance":10000,"currency":"USD"}'
curl -s -X POST http://127.0.0.1:8000/incidents \
  -H 'Content-Type: application/json' \
  -d '{"victim_id":"BANK-1","amount_stolen":1200,"amount_frozen":600,"kind":"phishing_transfer"}'
curl -s -X POST http://127.0.0.1:8000/cycles/run \
  -H 'Content-Type: application/json' -d '{}'
curl -s http://127.0.0.1:8000/summary
```

## Deploying it

### Private use (protect yourself)

Run it on your own machine, localhost only:

```bash
python3 -m recovery.cli serve
```

- The server binds to `127.0.0.1` by default, so it is reachable
  only from your own machine. Keep it that way.
- Open your accounts, report incidents as they happen, and run
  cycles to recover frozen funds.
- Keep the audit log: `GET /audit` gives you the full trail. Save
  it somewhere safe — it is your proof of what was recovered.

### Industrial / bank use (protect the bank and its clients)

Run it as a service on a server inside the bank's network:

```bash
python3 -m recovery.cli serve --host 127.0.0.1 --port 8000
```

- **Do not bind to `0.0.0.0` without a front door.** Put the API
  behind authentication and TLS (a reverse proxy such as nginx, or
  your existing API gateway). The API itself has no auth — it is
  meant to sit behind your network controls.
- **Run it as a managed service** (systemd, Docker, or your
  orchestrator). The repo has a `systemd/` directory with units
  for the Cyber CIV services; add a recovery unit the same way.
- **Back up the audit log.** The log is the accountability record.
  Export it regularly (`GET /audit`) and store it where it cannot
  be altered.
- **Tune the caps for your volume.** The defaults are
  `fallback_cap=50.0` per cycle and a 1-cycle guard cooldown.
  A bank with larger frozen amounts should raise the fallback cap
  (or run more cycles) so big losses clear in a reasonable number
  of cycles.

## Known limitations (prototype)

- **State is in memory.** Restarting the server loses accounts,
  incidents, and the audit log. A production deployment needs
  persistence (a state file or a database) and a backup of the
  audit log.
- **No authentication.** The API trusts the network it runs on.
  Never expose it directly to the internet.
- **Single currency pool / wallet.** The pool and the Recovery Wallet
  each hold one number. A deployment handling multiple currencies
  should run one engine (or one pool/wallet) per currency, so USD
  and ETH are never mixed in a single balance.
- **One open incident per victim.** The engine keeps the latest
  open incident per victim in its working map; the full history
  stays in `incidents`. A deployment with overlapping incidents
  for one victim should extend this.

## Files

| File | What it is |
|------|-----------|
| `engine.py` | core engine (accounts, incidents, pool, recovery wallet, transfers, guard, agent, fallback, audit log) |
| `signal.py` | signal monitor: sense crypto + banking from a mere signal (public data only, no credentials) |
| `dashboard.html` | web dashboard (accounts, stats, incidents, transfers, audit, signal sensing) |
| `state.py` | state persistence: atomic save/load of state.json |
| `service.py` | HTTP API server (stdlib only) |
| `cli.py` | command-line entry point (`demo` / `serve` / `test` / `sense`) |
| `demo.py` | end-to-end demo scenario |
| `test_engine.py` | engine unit tests (26, incl. 3 recovery-wallet + 7 transfer + 3 address-lookup tests) |
| `test_service.py` | HTTP API end-to-end tests (6) + auth tests (3) + dashboard/sense tests (4) |
| `test_signal.py` | signal monitor tests (15, mocked RPC) + signal-to-engine wiring tests (4) |
| `test_state.py` | state persistence tests (7: round-trip, restart simulation, corrupt/version, atomicity, guard cooldown) |
| `README.md` | this file |

## Cyber CIV mapping

| Cyber CIV | This subsystem |
|-----------|---------------|
| Civilization (Ares, Pax...) | Account (Bank, Crypto wallet, Fintech) |
| Intent (0-1) | Balance (USD / token units) |
| Theft (steals intent) | Cyber incident (drains funds, freezes some) |
| Frozen share | Frozen funds (held in the Recovery Pool) |
| RECOVER action | Recovery action (reimburse victim from pool) |
| Aegis (recovery agent) | RecoveryAgent (monitors, proposes RECOVER) |
| Guard (validates proposals) | Guard (rejects null/invalid/cooldown) |
| Deterministic fallback | Auto-reimburse oldest open loss (capped) |
| Recovery pool | Pooled reserve (holds frozen funds) |
| Audit log (hash-chained) | AuditLog (SHA-256 hash chain, append-only) |

Proprietary work of Paris Pehlivanovic. All rights reserved.
