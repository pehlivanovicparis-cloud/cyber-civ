"""Cyber CIV — Recovery subsystem, BIP32 public-key derivation.

Pure-Python BIP32 (public derivation only) + address generation.
Stdlib only; no C bindings, no external crypto library.

What it does:
- Parse an extended public key (xpub/ypub/zpub) into its fields.
- CKDpub: derive a non-hardened child public key from a parent
  public key. HMAC-SHA512(key=parent chain code,
  data=parent pubkey || index) -> IL (scalar tweak) + IR (child
  chain code). Child pubkey = IL*G + parent pubkey, compressed.
- Hardened indices (index >= 2^31) raise BIP32DerivationError
  immediately — they need the parent private key, and this engine
  never touches a private key.
- Address generation: P2PKH for BIP44 (xpub), P2SH-P2WPKH for
  BIP49 (ypub), P2WPKH (Bech32) for BIP84 (zpub).
- Path derivation: from an account-level xpub, derive the external
  chain (/0) and the first gap_limit receiving addresses.

HASH160 (RIPEMD-160(SHA-256(x))) uses hashlib's ripemd160, which
is available on this system. If a deployment lacks it, this module
fails loud at import time rather than silently producing wrong
addresses (see the import-time check below).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod

from recovery.bech32 import bech32_encode
from recovery.secp256k1 import (
    N,
    compressed_to_point,
    point_add,
    point_to_compressed,
    scalar_mult,
)
from recovery.xpub import _b58decode, _b58encode

# Fail loud at import time if RIPEMD-160 is unavailable: address
# generation depends on HASH160, and a silent fallback would
# produce wrong addresses.
try:
    hashlib.new("ripemd160", b"").digest()
except (ValueError, TypeError) as exc:  # pragma: no cover
    raise ImportError(
        "hashlib lacks RIPEMD-160; address generation is unavailable"
    ) from exc


class BIP32DerivationError(ValueError):
    """Raised when a derivation cannot be performed (hardened index,
    invalid key, or an invalid child tweak)."""


# First hardened index. Any index >= this needs the private key.
HARDENED_OFFSET = 0x80000000


def _hash160(data: bytes) -> bytes:
    """RIPEMD-160(SHA-256(data))."""
    sha = hashlib.sha256(data).digest()
    return hashlib.new("ripemd160", sha).digest()


def parse_xpub(xpub: str) -> dict:
    """Parse an extended public key into its fields.

    Returns a dict with version (bytes), depth (int), fingerprint
    (bytes), child_number (int), chain_code (bytes), pubkey (bytes,
    compressed 33-byte). Raises BIP32DerivationError on a bad key.
    """
    from recovery.xpub import validate_xpub

    signal = validate_xpub(xpub)
    raw = _b58decode(signal.xpub_key)
    payload = raw[:78]
    return {
        "version": payload[:4],
        "depth": payload[4],
        "fingerprint": payload[5:9],
        "child_number": int.from_bytes(payload[9:13], "big"),
        "chain_code": payload[13:45],
        "pubkey": payload[45:78],
        "derivation_type": signal.derivation_type,
    }


def derive_child_pubkey(xpub: str, index: int):
    """CKDpub: derive a non-hardened child public key.

    Returns (child_pubkey_bytes, child_chain_code). The child
    pubkey is 33-byte compressed (0x02/0x03 prefix).
    Raises BIP32DerivationError for a hardened index or an invalid
    tweak (IL == 0 or IL >= N).
    """
    if not 0 <= index < HARDENED_OFFSET:
        raise BIP32DerivationError(
            f"hardened index {index} requires the private key; "
            "only non-hardened derivation (index < 2^31) is allowed"
        )
    parsed = parse_xpub(xpub)
    parent_pubkey = parsed["pubkey"]
    parent_chain = parsed["chain_code"]
    data = parent_pubkey + index.to_bytes(4, "big")
    digest = hmac_mod.new(parent_chain, data, hashlib.sha512).digest()
    tweak, child_chain = digest[:32], digest[32:]
    tweak_int = int.from_bytes(tweak, "big")
    if tweak_int == 0 or tweak_int >= N:
        raise BIP32DerivationError("invalid child tweak (IL == 0 or >= n)")
    parent_point = compressed_to_point(parent_pubkey)
    child_point = point_add(scalar_mult(tweak_int), parent_point)
    if child_point is None:
        raise BIP32DerivationError("child public key is infinity")
    return point_to_compressed(child_point), child_chain


def derive_child_xpub(xpub: str, index: int) -> str:
    """Derive the full child extended public key (xpub string).

    Rebuilds the 78-byte payload: same version, depth+1,
    fingerprint = HASH160(parent pubkey)[:4], child number = index,
    child chain code, child pubkey. Then Base58Check-encodes it.
    """
    parsed = parse_xpub(xpub)
    child_pubkey, child_chain = derive_child_pubkey(xpub, index)
    fingerprint = _hash160(parsed["pubkey"])[:4]
    payload = (
        parsed["version"]
        + bytes([parsed["depth"] + 1])
        + fingerprint
        + index.to_bytes(4, "big")
        + child_chain
        + child_pubkey
    )
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _b58encode(payload + checksum)


def address_from_pubkey(pubkey: bytes, derivation_type: str) -> str:
    """Convert a compressed public key to its address.

    BIP44 (xpub) -> P2PKH ("1..."). BIP49 (ypub) -> P2SH-P2WPKH
    ("3..."). BIP84 (zpub) -> P2WPKH Bech32 ("bc1q...").
    """
    program = _hash160(pubkey)
    if derivation_type == "BIP44":
        return _b58encode(b"\x00" + program + _checksum(b"\x00" + program))
    if derivation_type == "BIP49":
        # Redeem script: 0x0014 || HASH160(pubkey). The P2SH address
        # is HASH160 of that script, prefixed 0x05.
        redeem = b"\x00\x14" + program
        script_hash = _hash160(redeem)
        return _b58encode(b"\x05" + script_hash + _checksum(b"\x05" + script_hash))
    if derivation_type == "BIP84":
        return bech32_encode("bc", 0, program)
    raise BIP32DerivationError(
        f"unknown derivation type {derivation_type!r}"
    )


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def derive_addresses(xpub: str, gap_limit: int = 20, change: bool = False):
    """Derive the receiving addresses for an account-level xpub.

    Derives the external chain (/0, or /1 when change=True) once,
    then addresses 0..gap_limit-1 from it. Returns a list of
    address strings. All indices are non-hardened. gap_limit is
    1-100 here (how many addresses to derive); the user-facing
    xpub gap_limit default (10-100) is enforced in xpub validation,
    not here, so tests and callers can derive a small prefix.
    """
    if not 1 <= gap_limit <= 100:
        raise BIP32DerivationError(
            f"gap_limit must be 1-100, got {gap_limit}"
        )
    parsed = parse_xpub(xpub)
    derivation_type = parsed["derivation_type"]
    chain_index = 1 if change else 0
    chain_xpub = derive_child_xpub(xpub, chain_index)
    addresses = []
    for i in range(gap_limit):
        pubkey, _chain = derive_child_pubkey(chain_xpub, i)
        addresses.append(address_from_pubkey(pubkey, derivation_type))
    return addresses
