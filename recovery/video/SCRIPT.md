# Cyber CIV Recovery Engine — Submission Video Script
# Target: ~3.5 minutes. Six sections, one per slide.
# Numbers are written as they should sound.

## SECTION 1 — Intro
This is the Cyber CIV Recovery Engine. A zero-P-I-I, read-only sensing
protocol for crypto and banking asset recovery. It answers one question:
can you find and recover your money without ever handing over your
account, your login, or your private key? The answer is yes. The engine
senses wallet and bank state from a mere signal — a transaction hash, a
wallet address, a bank routing number — and it never collects, stores,
or handles your account data. It was built for the Meta Global A-I
Developer Hackathon, and it is live right now.

## SECTION 2 — The problem
Here is the problem it solves. People lose access to wallets and bank
accounts every day. A lost key, a hacked exchange, a locked account.
Traditional recovery tools make it worse: they ask you to connect your
account, paste your credentials, hand over A-P-I tokens. That creates a
bigger target. If the recovery service gets breached, your money is gone.
Cyber CIV flips the model. Nothing connects. Nothing logs in. The engine
reads public signals and derives state mathematically.

## SECTION 3 — Zero-P-I-I
The core rule is zero retention. For banking, you paste a SWIFT M-T-9-4-0
statement snippet. The parser extracts only the currency, the opening
and closing balances, and the transaction velocity. The account number
and statement number are discarded by construction at the ingress layer
— never extracted, never stored, never returned. For crypto, you give it
an extended public key. It derives your addresses and checks their
balances on-chain. Your private key never leaves your hands, because the
engine never asks for it. Sensing an asset can never mutate the system:
the ingest endpoints are read-only and idempotent.

## SECTION 4 — Crypto
Everything runs on pure Python, standard library only. No compiled C
dependencies, no external crypto packages, no supply-chain risk. The
secp-two-five-six-k-one elliptic curve math is hand-rolled: point
addition, double-and-add multiplication, compressed key parsing. The
heavy modular inversion is handed to Python's C-backed built-in power
function, so a full twenty-address wallet lookahead completes in about
a quarter of a second. B-I-P-32 derivation, Bech-32 address encoding,
and U-T-X-O lookups against the public Esplora indexer reconstruct your
real on-chain balances — verified against live addresses, down to the
satoshi.

## SECTION 5 — Defense and tests
The public tunnel is wrapped in defense in depth. Sixty-four kilobyte
body caps, rate limiting, a five-minute lockout after five failed
logins, and flat error responses that hide stack traces. The dashboard
retries with a fifteen-second timeout and exponential backoff, so a cold
tunnel never hangs your first request. And the code is proven, not just
claimed: one hundred sixty-seven tests pass, and a pre-freeze security
audit caught and fixed two real bugs — a Bech-32 decoder that skipped
its guardrails, and an M-T-9-4-0 currency check that could be bypassed
by swapping tag order.

## SECTION 6 — Closing
It is live now, at the link below, with the full source and audit log
on GitHub. The engine is on cycle seven thirty-one, the audit chain is
valid, and the commit is G-P-G signed. Cyber CIV Recovery Engine: your
money, sensed from a signal, never taken from your hands. Thank you.
