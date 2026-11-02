# Recovery API hardening (2026-11-02)

The recovery API is exposed publicly via a localhost.run tunnel behind
HTTP Basic Auth. This document records the hardening applied on
2026-11-02, what was already safe, and what was fixed.

## Stack

Python standard library only (`http.server.ThreadingHTTPServer`).
No FastAPI, no Flask, no new dependencies. That matters because the
usual rate-limit libraries (slowapi, Flask-Limiter) do not apply —
the limiter is implemented in the handler (`recovery/ratelimit.py`,
in-memory, per client IP).

## What was already safe (verified, not changed)

- **Auth timing:** credential comparison already uses
  `hmac.compare_digest` (constant-time). No timing side channel.
- **Signal parser strictness:** tx hashes and addresses must match
  anchored regexes (`^0x[0-9a-fA-F]{64}$`, `^0x[0-9a-fA-F]{40}$`);
  bank signals must be digits with a valid ABA checksum. A malformed
  signal is rejected, never executed. No `eval`, no dynamic code.
- **Engine validation:** `Account` rejects unknown types and negative
  balances; `transfer()` rejects unknown sources, empty destinations,
  non-positive amounts, and overdrafts; `report_incident()` rejects
  unknown victims, negative amounts, frozen > stolen, and amounts
  above the victim balance.

## What was fixed (2026-11-02)

1. **Rate limiting** (`recovery/ratelimit.py`, in-memory, per IP):
   - POST (mutating) endpoints: 10 requests / 60s per IP.
   - GET (read-only) endpoints: 60 requests / 60s per IP.
   - Over the limit returns HTTP 429 with a `Retry-After` header.
   - The auto-cycle calls POST /cycles/run once per minute, so 10/min
     leaves headroom; the dashboard polls GET endpoints ~12/min.
2. **Failed-auth blocking (brute-force defense):** 5 failed Basic Auth
   attempts from one IP in 60s blocks that IP from authenticating
   for 5 minutes (429 instead of 401). A missing or malformed
   Authorization header counts as a failed attempt.
3. **Request body cap:** bodies over 64 KB are rejected with HTTP 413.
   (Previously there was no cap — a huge Content-Length could exhaust
   server memory.)
4. **Signal length cap:** /sense signals over 256 chars are rejected
   with HTTP 400. (A tx hash is 66 chars, an address 42.)
5. **Graceful /sense failure:** sensing is wrapped in try/except. An
   unexpected failure returns a clean HTTP 500 (`{"error": "sensing
   failed"}`), never a dropped connection or a stack trace to the
   client.

## Verification (live, 2026-11-02 ~07:20 EST)

- 75/75 unit tests pass (`python3 -m unittest discover -s recovery`),
  including 11 new hardening tests (rate limit, auth block, body cap,
  signal cap, graceful /sense failure).
- Live API (localhost:8000, after service restart):
  - health with auth → 200 (cycle 682, audit valid, state preserved).
  - health without auth → 401.
  - 12 rapid POST /cycles/run → 10× 200, then 429.
  - 6 failed auth attempts → 401s, then 429 (IP blocked).
  - 70 KB body → 413. 302-char signal → 400.
  - /sense with a real bank signal → 200, BANK_SIGNAL_VALID.
- Public tunnel (https://13a74a08bfa62b.lhr.life):
  dashboard 200 (auth), health 401 (no auth), health 200 (auth).

## Deployment note

The recovery services are system systemd units
(`cyber-civ-recovery`, `cyber-civ-recovery-autocycle`,
`cyber-civ-recovery-tunnel`). On 2026-11-02 the units had been wiped
by a VM replacement and the API was down (nothing on port 8000). The
three recovery units were restored from
`~/workspace/cyber_civ/systemd/` to `/etc/systemd/system/` and
enabled/started. The harvest units were NOT restored in that step.
If the VM is replaced again, run `systemd/restore-units.sh` (or
re-copy the recovery units) to bring the API back.

## Follow-up fix — memory bound + tunnel identity (2026-11-02 ~07:30 EST)

Claude's review of the rate limiter found one real gap and one
documented limitation.

**Memory growth — FIXED.** The original limiter pruned old timestamps
from each bucket's list but never deleted the dict keys, so an IP
that made one request and never returned kept a key forever. Under a
scan from many IPs the dict grew without bound. Fixed in
`ratelimit.py`: keys whose lists prune to empty are dropped, and the
number of tracked IPs is capped at MAX_TRACKED_IPS (10,000) with
least-recently-seen eviction above that. Two new tests
(`test_memory_bound_evicts_stale_ips`,
`test_empty_buckets_are_dropped`); suite is now **77/77 pass**.
Service restarted, health 200 (cycle 689, audit valid).

**Thread safety — already handled.** A single `threading.Lock`
guards all counters; ThreadingHTTPServer runs requests on multiple
threads, so every counter update is under the lock.

**Client identity behind the tunnel — LIMITATION, documented.**
The public endpoint is a localhost.run SSH tunnel, so every remote
client reaches the API as 127.0.0.1 (the local SSH client is the TCP
peer). Per-IP limiting therefore treats all remote clients as one
client, sharing a bucket with the local auto-cycle. Acceptable for
this single-user private demo (Paris + the auto-cycle together use
far less than the limits), but one abusive remote client could
consume the shared bucket. A proper fix needs a proxy under our
control that sets a trusted client-IP header. We do NOT trust
X-Forwarded-For from localhost.run — we do not control that proxy
and the header is spoofable.

**Order of checks — already handled.** do_POST enforces the rate
limit BEFORE reading the request body, so an over-limit request
never reaches the body cap; the body cap is a second line of
defense, not the only one.

## Ingest API wiring — COMPLETE (2026-11-02 ~07:56 EST)

Paris: "Go ahead and wire them in!" The two ingest engines are now
live HTTP endpoints under a unified /ingest namespace, inheriting
the hardening (auth, rate limits, 64 KB body cap, clean 500s).

### Endpoints (read-only sensing — no state mutation, no persist)

- POST /ingest/mt940 — body {"text": "<raw MT940>"}. Parses the
  snippet, returns currency, opening/closing balances, statement
  period start, transaction count, velocity profile. The account
  number (:25:) and statement number (:28C:) are stripped by
  construction and verified absent from the JSON response.
  Malformed input -> clean 400. Missing text -> 400.
- POST /ingest/xpub — body {"xpub", "gap_limit" (default 20,
  10-100), "base_url" (default Blockstream Esplora)}. Validates
  (xprv -> 400 security error), derives the receiving addresses,
  resolves each balance via the indexer in parallel (5 workers).
  Returns derivation type, per-address balances/tx counts/UTXO
  counts, total balance (sats), total tx count, active-address
  count, and any per-address errors. A per-address indexer failure
  leaves that balance null instead of failing the request.

### Design decision (Paris's Q1)

Results return in-memory in the response and are shown on the
dashboard. They are NOT written into the cycle persistence layer:
sensing a user's signal must not mutate the engine's accounts,
incidents, or pool. The ingest endpoints follow the /sense pattern
(read-only, no persist()). If persistence is ever wanted, it is a
separate design decision (how a sensed statement/wallet maps onto
the engine's account model).

### Dashboard

dashboard.html has a new "Ingest a statement or wallet key"
section: an MT940 textarea + Parse button, and an xpub input +
Resolve button. Results render inline. No account numbers are
ever displayed.

### Live verification (2026-11-02 ~07:56 EST)

- MT940: 200, USD, open 1234.56, close 934.56, 2 txs, account
  number absent from the response. Malformed -> 400. No auth -> 401.
  Oversized body -> 413.
- xpub (live Esplora): 200 in 2.1s, BIP84, 10 addresses,
  total_balance_sats 0, total_tx_count 216, 10 active, errors [].
  First address bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu, 0 sats,
  178 txs. (A first attempt after the restart timed out at 30s —
  cold start / transient network delay; the retry took 2.1s. The
  server logged a BrokenPipeError for the abandoned connection,
  which is the client disconnecting, not a server fault.)
- xprv: 400, "security violation: extended PRIVATE key provided".
- Health after restart: 200, cycle 717-718, audit valid, state
  preserved.
- Public tunnel URL CHANGED on the 07:55 restart (free
  localhost.run URLs rotate): new URL
  https://8ace5f943536c3.lhr.life — dashboard 200, health 200.
  The old 13a74a08bfa62b URL is dead (502).

### Tests

16 new integration tests (recovery/test_ingest_api.py): MT940
valid/scrubbing/malformed/missing-text/auth/oversized/no-mutation;
xpub valid/default-gap/xprv/bad-key/bad-gap/missing/auth/indexer-
failure/no-mutation. Suite now **144/144 pass**.
