# Final Code Review — Pre-Freeze Audit (2026-11-02)

Review of the hand-rolled crypto/parsing modules before the Nov 3
hackathon code freeze. Read-only audit: re-read each module, wrote a
scratch probe exercising every checklist edge case, fixed what the
probe found, locked the fix in with unit tests.

## Scope
- `recovery/secp256k1.py` — elliptic-curve arithmetic
- `recovery/bech32.py` — BIP173 encoding/decoding
- `recovery/mt940.py` — MT940 banking-signal parser
- `recovery/bip32.py` + `recovery/xpub.py` — BIP32 derivation, Base58

## Findings — 2 real bugs, both fixed

### Bug 1: `bech32_decode` skipped all guardrails (bech32.py)
Encode validated witness version (0–16) and program length (2–40,
version 0 = 20/32), but decode only checked the checksum. A
valid-checksum address with version 17, a v0 21-byte program, or a
v1 1-byte program decoded without error. **Fix:** decode now
enforces the same guardrails as encode. 4 new tests
(`TestBech32DecodeGuardrails`).

### Bug 2: MT940 currency mismatch missed when `:62F:` precedes `:60F:` (mt940.py)
The parser stored one shared `currency` variable. If `:62F:` (EUR)
came first, it set currency=EUR; then `:60F:` (USD) overwrote it
with no check, so the mismatch was silently accepted. Real MT940
files always put `:60F:` first, but a malformed file could swap
them. **Fix:** opening and closing currencies are now tracked
separately and compared after the loop, so the check is
order-independent. 2 new tests.

## Verified clean (probe: 18/18 PASS, no changes needed)
- secp256k1: point_add handles infinity (both sides, and
  P + (-P) = infinity); point_double returns infinity at y == 0;
  scalar_mult k=0 / k=N / k=2N gives infinity, k=N+1 wraps to G;
  compressed keys round-trip; non-residue x raises ValueError;
  bad prefix/length and x >= p raise ValueError.
  Note: a y == 0 curve point cannot exist on secp256k1 (prime-order
  group has no element of order 2), so the compressed-key y == 0
  parity edge case is unreachable — the branch is still correct.
- Bech32: 20/32-byte round-trips; bad checksum rejected; convertbits
  padding correct (no dropped data, no index errors).
- MT940: zero amounts (0,00) parse as 0.0; absent decimals
  ("1234," / "1234") raise; leap-year dates (2024-02-29 valid,
  2023-02-29 rejected via datetime); duplicate :60F: raises.
- BIP32: zero tweak (IL == 0) and tweak >= n both raise
  BIP32DerivationError (verified via mocked HMAC); hardened indices
  rejected; Base58 leading-zero bytes round-trip as leading "1"s.

## Test suite
167/167 pass (149 before the review + 18 new: 5 secp256k1, 4 Bech32
decode guardrails, 2 BIP32 tweak, 6 MT940, 1 Base58). Both review
probes green (18/18 + 6/6). Live service NOT restarted — the fixes
are in the library modules; the running service picks them up on
its next restart (systemd Restart=always on failure; no restart
was forced for a library-only change).

## Self-score
1) Goal hit? Yes — the checklist was audited end to end, 2 real
   bugs found and fixed with tests, everything else verified clean.
2) Tools right? Yes — scratch probes for the checklist, then
   permanent unit tests for the fixes; full suite re-run.
3) Waste — what I'd skip next time? The first probe run failed on
   a PYTHONPATH miss (script in /tmp couldn't import recovery);
   run scratch probes from the repo directory from the start.
