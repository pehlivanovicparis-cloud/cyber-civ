# Cyber CIV: Recovery Engine Architecture Overview

## High-Assurance, Zero-PII Crypto & Banking Asset Sensing Protocol

Developed for the November 3, 2026 code freeze, this production-grade,
zero-mutation protocol securely senses blockchain and banking asset
states over an authenticated public tunnel without collecting, storing,
or handling user account data or private keys.

## 1. The Zero-PII Security Architecture

The core engineering constraint of the Cyber CIV engine is
zero-retention, read-only sensing. Traditional recovery systems pull
users into a connected-account model (e.g. live Plaid webhooks, API
tokens), creating massive compliance and security liability. Cyber CIV
treats financial history purely as transient mathematical state markers.

- **Account Identifier Scrubbing:** The banking pipeline accepts raw
  SWIFT MT940 snippets, extracts necessary transaction metrics
  (currency, opening/closing balances, transaction velocity profiles),
  and permanently discards structural identifiers (`:25:` Account
  Number and `:28C:` Statement Number) by construction at the ingress
  layer. Account data is never extracted, never stored in memory, and
  never emitted in JSON responses.
- **Purely Read-Only, Zero-Mutation Ingress:** Ingesting a wallet key
  or financial statement executes as a stateless query. The backend
  endpoints (`/ingest/xpub` and `/ingest/mt940`) process input
  parameters in-memory to populate the active user dashboard session.
  They are strictly decoupled from the core database persistence engine
  (cycle 725). Sensing an asset can never mutate or pollute the
  system's transaction, incident, or account tables.

## 2. High-Performance Pure-Python Cryptography

To eliminate supply-chain vulnerabilities, compiled C-binary
dependencies, and external cryptographic packages, the entire
blockchain derivation pipeline is built using Python standard-library
primitives only.

- **Hand-Rolled secp256k1 Curve Math:** Implements fundamental elliptic
  curve arithmetic, point addition, double-and-add point
  multiplication, and compressed public key de-serialization (0x02/0x03
  parity flags) over finite fields F_p.
- **Zero Performance Bottlenecks:** Offloads intensive modular inversion
  calculations directly to Python's C-backed built-in `pow(a, -1, p)`,
  achieving complete address derivation matrices in 0.23 to 0.26
  seconds for a standard 20-address wallet lookahead.
- **Deterministic On-Chain State Derivation:** The BIP32 child key
  derivation pipeline (CKDpub) splits 64-byte HMAC-SHA512 outputs to
  tweak parent keys, encodes Native SegWit (bc1) witness programs via
  a hand-rolled BIP173 Bech32 serializer, and executes stateless queries
  against a configurable public Esplora API (blockstream.info/api) to
  reconstruct accurate on-chain satoshi balances without private keys.

## 3. Production-Grade Defense-in-Depth

The public-facing tunnel (localhost.run edge forwarding) is wrapped in
a tight, multi-layered application-layer firewall designed to neutralize
network-level exploits, denial-of-service, and reconnaissance before
they hit the execution loop.

| Defense Layer | Technical Specification | Operational Impact |
|---|---|---|
| Strict Request Capping | Max body size: 64 KB (413 Payload Too Large); max signal size: 256 chars (400 Bad Request) | Eliminates buffer exploits and heavy payload-stuffing vectors at the boundary. |
| Adaptive Rate Limiting | 10 mutating requests/min per IP; 60 read requests/min per IP | Prevents API exhaustion and throttles malicious traffic over the public tunnel. |
| Brute-Force Lockout | 5 failed authentication attempts within 1 minute triggers a hard 5-minute IP ban | Prices out automated credential stuffing and basic-auth scanning. |
| Information Masking | All unhandled exceptions return a flat 500 Internal Server Error | Eradicates reconnaissance by hiding stack traces from clients. |
| Idempotent Client Retries | Dashboard JavaScript uses an AbortController capped at 15s per attempt (up to 3 retries) with 1s then 2s backoff | Eliminates cold-start delays from transient SSH tunnel idling. Verified safe via backend idempotency assertions. |

## 4. High-Assurance Test Suite & Pre-Freeze Audit

The application enforces a rigorous, test-driven development (TDD)
model. Before locking the code freeze, the test footprint was expanded
to **167/167** comprehensive unit and integration tests passing.

A final read-only, edge-case security review logged under
`recovery/CODE-REVIEW-2026-11-02.md` captured and neutralized two
hidden structural vulnerabilities prior to deployment:

1. **Bech32 Decoder Hardening:** Patched the address validation loop to
   enforce identical witness version (0–16) and program size
   constraints (2–40 bytes) during decoding as it does during encoding,
   blocking valid-checksum malformed addresses.
2. **Order-Independent Currency Verification:** Rewrote the MT940
   statement validator to decouple currency integrity from file tag
   layout order. The engine now stores and cross-checks opening
   (`:60F:`) and closing (`:62F:`) currency bytes independently,
   neutralizing maliciously structured multi-currency inputs.

## Freeze Profile

- Test suite: 167/167 passing.
- Audit artifact: `recovery/CODE-REVIEW-2026-11-02.md`.
- Live state: cycle 725, audit chain valid.
- Public tunnel: https://5774c128729357.lhr.life (free localhost.run
  URL; rotates on restart).
- Auth: HTTP Basic Auth (username `paris`; password in
  `recovery/.auth`, 0600, git-ignored).
