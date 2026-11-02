# Ingest Expansion — Phase 2 Design (BIP32 derivation + UTXO lookup)

Date: 2026-11-02. Status: APPROVED by Paris ("Go", 2026-11-02 07:41 EST).
Method: TDD — tests first, then implementation. Stdlib only (no
Pydantic, no C bindings, no pre-compiled crypto). This is pure-Python
cryptographic code; every step is verified against published test
vectors, and any failure is loud, never silent.

## Constraints (binding)

- Stdlib only. `hashlib` (SHA-256, SHA-512, HMAC, RIPEMD-160),
  `hmac`, `urllib.request`. No `bip32`, no `ecdsa`, no C extension.
- Public keys only. The engine never accepts, stores, or derives
  from a private key. Hardened derivation (index >= 2^31) requires
  the parent private key, so any hardened index raises
  `BIP32DerivationError` immediately — never silent garbage.
- No account info / no login details (Paris, 2026-11-01). The
  indexer is a public, unauthenticated read (address -> balance /
  UTXOs). No API key, no account.
- The derivation engine is decoupled from the indexer. The indexer
  base URL is configurable (Blockstream Esplora primary,
  mempool.space fallback) so failover changes no crypto code.

## Modules

- `recovery/secp256k1.py` — curve constants (p, n, G, a=0, b=7),
  point addition / doubling (handles the point at infinity without
  a division-by-zero), scalar multiplication (double-and-add),
  modular inverse via `pow(a, -1, p)`. Points are (x, y) tuples;
  the point at infinity is None.
- `recovery/bech32.py` — BIP173 Bech32 encoding/decoding
  (HRP expansion, polymod checksum, convertbits). Witness version
  is kept separate from the program bytes.
- `recovery/bip32.py` — xpub parsing (reuses the Base58Check decode
  from `recovery/xpub.py`), CKDpub (HMAC-SHA512, split into
  32-byte scalar tweak + 32-byte child chain code, child pubkey =
  tweak*G + parent pubkey, compressed 0x02/0x03 output), address
  derivation (P2PKH for BIP44/xpub, P2SH-P2WPKH for BIP49/ypub,
  P2WPKH Bech32 for BIP84/zpub), and path derivation (account xpub
  -> external chain /0 -> addresses 0..gap_limit-1, and internal
  chain /1).
- `recovery/utxo.py` — indexer client. `get_address_info(address)`
  returns balance (satoshis), tx count, and UTXO list. Primary
  base URL `https://blockstream.info/api`, fallback
  `https://mempool.space/api`, configurable. Uses urllib.request
  with a timeout; network errors raise `IndexerError` (clean,
  never a crash).

## Test vectors (ground truth)

- secp256k1 identity: G*1 = G; G*n = infinity (point at infinity);
  G*2 = the known doubled point; point + infinity = point.
- CKDpub (BIP32 test vector 1, seed
  000102030405060708090a0b0c0d0e0f). Only two steps are
  non-hardened and therefore derivable from an xpub:
  m/0' -> m/0'/1 (child 1) and m/0'/1/2' -> m/0'/1/2'/2
  (child 2). The full child xpub (version, depth+1, fingerprint,
  child number, child chain code, child pubkey) must match the
  published xpub exactly. NOTE: the xpubs below were derived from
  the seed with an independent CKDpriv implementation and verified
  against the master xpub — an earlier recalled copy had wrong
  tails and failed. Always derive test vectors from the seed, never
  from memory.
  - m/0' xpub: xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwBZeNK1VTsfTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw
  - m/0'/1 xpub: xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWbWMiGj7Wf5uMash7SyYq527Hqck2AxYysAA7xmALppuCkwQ
  - m/0'/1/2' xpub: xpub6D4BDPcP2GT577Vvch3R8wDkScZWzQzMMUm3PWbmWvVJrZwQY4VUNgqFJPMM3No2dFDFGTsxxpG5uJh7n7epu4trkrX7x7DogT5Uv6fcLW5
  - m/0'/1/2'/2 xpub: xpub6FHa3pjLCk84BayeJxFW2SP4XRrFd1JYnxeLeU8EqN3vDfZmbqBqaGJAyiLjTAwm6ZLRQUMv1ZACTj37sR62cfN7fe5JnJ7dh8zL4fiyLHV
- Bech32 (BIP173): witness version 0, program
  751e76e8199196d454941c45d1b3a323f1433bd6, HRP "bc" ->
  bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4.
- BIP84 (zpub): the first receiving address for the published
  BIP84 test zpub is bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu
  (verified empirically: it decodes cleanly and its witness program
  equals HASH160 of the derived public key; an earlier recalled
  copy had a wrong tail and fails the Bech32 checksum). Live
  on-chain: that address has tx_count 178, balance 0 — a real
  address with real history, confirming the derivation is genuine.
- Hardened guard: any index >= 2^31 raises BIP32DerivationError.
- Indexer: mocked urllib responses using a real Esplora payload
  shape (chain_stats funded/spent sums, mempool_stats). A live
  smoke test against a known address is run separately, not as a
  unit-test dependency.

## Performance note

Deriving a 20-address gap lookahead needs 21 scalar
multiplications (1 for the chain key + 20 for the addresses).
Each scalar multiplication is ~256 double-and-add steps in pure
Python. MEASURED 2026-11-02: 0.23-0.26s for a 20-address zpub
lookahead. No bottleneck — well under a second, fine for an
on-demand API call.

## Live verification (2026-11-02)

- Blockstream Esplora, genesis address 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa:
  balance 5,769,831,050 sats (57.69 BTC — the original 50 BTC plus
  donations), tx_count 67,240, latency ~1s.
- Esplora returns HTTP 400 on /utxo for addresses with an
  excessive number of UTXOs (the genesis address). The UTXO fetch
  is therefore non-fatal: a failure returns the balance + tx count
  with an empty UTXO list instead of killing the lookup. Covered
  by test_utxo_failure_is_nonfatal.
- zpub first address bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu:
  balance 0, tx_count 178 — live on-chain history, confirming the
  derivation produces a genuine address.

## Tests

- `recovery/test_bip32.py` — curve identity, point arithmetic,
  CKDpub against the two BIP32 vector steps, hardened rejection,
  Bech32 vector, address derivation (P2PKH / P2SH-P2WPKH /
  P2WPKH), gap-limit derivation, zpub first-address check.
- `recovery/test_utxo.py` — indexer URL construction, response
  parsing (balance = funded - spent, tx count, UTXO list),
  error handling (network failure, bad JSON, non-200), fallback
  base URL.

Run: `python3 -m unittest discover -s recovery -p "test_*.py" -t .`
from `~/workspace/cyber_civ`.
