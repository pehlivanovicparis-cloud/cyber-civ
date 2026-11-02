# Ingest Expansion — Phase 1 Design (MT940 + xpub validation)

Date: 2026-11-02. Status: APPROVED by Paris ("Go", 2026-11-02 07:35 EST).
Method: TDD — tests first, then implementation. Stdlib only (no Pydantic,
no new dependencies). Phase 2 (BIP32 derivation + UTXO lookup) is separate.

## Constraints (binding)

- Stdlib only. No Pydantic, no `bip32`/`hdwallet` library. Dataclasses +
  hand-rolled validation.
- No account info / no login details (Paris, 2026-11-01). The parsers
  accept only a signal the user voluntarily provides (a statement text
  snippet, an xpub). They never accept credentials, private keys, or
  API tokens.
- MT940: structural account identifiers (:25: account ID, :28C:
  statement number) are stripped at ingress and must not appear
  anywhere in the returned object or its string representation.
- xpub: only public keys. Any string starting with xprv/yprv/zprv
  (extended private key) is rejected with a clear security error.
- Open Banking webhooks (Plaid/Tink) are OUT OF SCOPE for live
  ingestion — receiving them requires a connected integration, which
  breaks the no-account/no-login constraint. Schema-only at most.

## MT940 parser — `recovery/mt940.py`

Input: raw SWIFT MT940 message text (a statement snippet the user
pastes). Output: `MT940Signal` dataclass.

Tags handled:
- `:25:` Account Identification — STRIPPED, never stored or returned.
- `:28C:` Statement Number — STRIPPED, never stored or returned.
- `:60F:` Opening Balance — `C|D` + YYMMDD + 3-char currency + amount
  (comma decimal separator, e.g. `1234,56`). Gives currency,
  statement_period_start, opening_balance.
- `:62F:` Closing Balance — same format as :60F:. Gives
  closing_balance.
- `:61:` Statement Line — YYMMDD (value date) + MMDD (entry date,
  optional) + `C|D` + amount + 3-4 char transaction type (e.g. NTRF,
  NMSC) + reference. Each line becomes one `TransactionVelocity`
  (entry_date, amount, is_credit, transaction_type).
- `:20:`, `:86:`, `:64:`, all other tags — ignored (may contain PII
  or data outside the contract).

`MT940Signal` fields: currency (3-char ISO), opening_balance (float),
closing_balance (float), statement_period_start (date),
transaction_count (int), velocity_profile (list of
TransactionVelocity). No field holds an account number or statement
number. The parser never echoes the raw input text.

Errors: malformed input (truncated file, missing :60F: or :62F:,
invalid transaction type, unparseable amount/date) raises
`MT940ParseError` — a clean exception, never a crash. The service
layer maps it to a generic error response.

## xpub validator — `recovery/xpub.py`

Input: an extended public key string + optional gap_limit.
Output: `XpubSignal` dataclass.

Validation (stdlib only, hashlib + string parsing):
- Prefix: must start with `xpub`, `ypub`, or `zpub`. Anything else
  (including a bare address or a random string) raises
  `XpubValidationError`.
- Private-key defense: a string starting with `xprv`, `yprv`, or
  `zprv` raises `XpubValidationError` with an explicit security
  message. Checked before any other validation.
- Base58Check: decode the base58 string; the result must be exactly
  82 bytes (78-byte payload + 4-byte checksum); the last 4 bytes
  must equal the first 4 bytes of double-SHA256 of the first 78
  bytes. A corrupted string (altered characters) fails this check.
- Version bytes: the 4-byte version must match the prefix
  (xpub 0x0488b21e, ypub 0x049d7cb2, zpub 0x04b24746). The key data
  (last 33 bytes of the payload) must start with 0x02 or 0x03
  (compressed public key).
- gap_limit: default 20, must be 10–100 inclusive.

`XpubSignal` fields: xpub_key (the validated string),
derivation_type (inferred from prefix: xpub→BIP44, ypub→BIP49,
zpub→BIP84), gap_limit (int). No derivation happens in Phase 1.

Test vector: BIP32 test vector 1 master xpub
`xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8`
(seed 000102030405060708090a0b0c0d0e0f). Public test vector, not a
real key.

## Phase 2 (NOT in this change)

BIP32 address derivation from the xpub (needs secp256k1 math —
HMAC-SHA512 + curve operations — written in pure Python stdlib) and
UTXO balance lookup via a public indexer (e.g. Blockstream Esplora,
mempool.space). Derivation is real crypto code and gets its own
careful pass with its own tests. No Phase 2 code is written here.

## Tests

- `recovery/test_mt940.py` — valid block extraction, account
  scrubbing (account + statement numbers absent from the result and
  its repr), malformation handling, debit/credit, comma decimals.
- `recovery/test_xpub.py` — valid xpub (BIP32 test vector),
  xprv/yprv/zprv rejection, bad prefix, corrupted checksum, decoded
  length, gap_limit bounds, derivation-type inference.

Run: `python3 -m unittest discover -s recovery -p "test_*.py" -t .`
from `~/workspace/cyber_civ`.
