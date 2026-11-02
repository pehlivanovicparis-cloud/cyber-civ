"""Cyber CIV — Recovery subsystem, Bitcoin xpub validator.

Validates an extended PUBLIC key (xpub/ypub/zpub) using only the
standard library (hashlib + string parsing). An xpub lets the engine
derive public addresses to scan UTXO state; it carries zero spending
or signing authority. Extended PRIVATE keys (xprv/yprv/zprv) are
rejected outright.

Design (see INGEST-PHASE1-DESIGN-2026-11-02.md):
- Prefix: must start with xpub, ypub, or zpub.
- Private-key defense: xprv/yprv/zprv raise a clear security error,
  checked before any other validation.
- Base58Check: the decoded key must be exactly 82 bytes (78-byte
  payload + 4-byte checksum); the checksum is the first 4 bytes of
  double-SHA256 of the payload.
- Version bytes must match the prefix (xpub 0x0488b21e, ypub
  0x049d7cb2, zpub 0x04b24746). The key data (last 33 bytes of the
  payload) must start with 0x02 or 0x03 (compressed public key).
- gap_limit: default 20, must be 10-100 inclusive.

No derivation happens here — that is Phase 2 (BIP32 math).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


class XpubValidationError(ValueError):
    """Raised when an xpub string is invalid or is a private key."""


@dataclass(frozen=True)
class XpubSignal:
    """A validated extended public key, ready for Phase 2 derivation."""

    xpub_key: str
    derivation_type: str  # BIP44 (xpub), BIP49 (ypub), BIP84 (zpub).
    gap_limit: int = 20


_B58_ALPHABET = (
    "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
)

# Prefix -> (version bytes, derivation type).
_PUBLIC_PREFIXES = {
    "xpub": (bytes.fromhex("0488b21e"), "BIP44"),
    "ypub": (bytes.fromhex("049d7cb2"), "BIP49"),
    "zpub": (bytes.fromhex("04b24746"), "BIP84"),
}

_PRIVATE_PREFIXES = ("xprv", "yprv", "zprv")

# Decoded extended key: 78-byte payload + 4-byte checksum.
_DECODED_LEN = 82
_PAYLOAD_LEN = 78
# Key data starts at byte 45 of the payload (4 version + 1 depth +
# 4 fingerprint + 4 child number + 32 chain code).
_KEY_DATA_OFFSET = 45


def _b58decode(s: str) -> bytes:
    """Decode a Base58 string to bytes. Raises XpubValidationError
    on a character outside the Base58 alphabet."""
    n = 0
    for ch in s:
        try:
            digit = _B58_ALPHABET.index(ch)
        except ValueError as exc:
            raise XpubValidationError(
                f"invalid Base58 character {ch!r}"
            ) from exc
        n = n * 58 + digit
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def _b58encode(raw: bytes) -> str:
    """Encode bytes to a Base58 string."""
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


def validate_xpub(key: str, gap_limit: int = 20) -> XpubSignal:
    """Validate an extended public key string.

    Returns an XpubSignal with the derivation type inferred from the
    prefix. Raises XpubValidationError on any failure, including a
    private key, a bad prefix, a bad checksum, or a bad length.
    """
    if not isinstance(key, str):
        raise XpubValidationError("xpub must be a string")
    key = key.strip()
    if not key:
        raise XpubValidationError("empty xpub")

    # Private-key defense first: never accept an extended private key.
    if key.startswith(_PRIVATE_PREFIXES):
        raise XpubValidationError(
            "security violation: extended PRIVATE key provided; "
            "only extended public keys (xpub/ypub/zpub) are allowed"
        )

    prefix = None
    for candidate in _PUBLIC_PREFIXES:
        if key.startswith(candidate):
            prefix = candidate
            break
    if prefix is None:
        raise XpubValidationError(
            "invalid format: must be an extended public key "
            "(xpub/ypub/zpub)"
        )

    if not 10 <= gap_limit <= 100:
        raise XpubValidationError(
            f"gap_limit must be 10-100, got {gap_limit}"
        )

    raw = _b58decode(key)
    if len(raw) != _DECODED_LEN:
        raise XpubValidationError(
            f"invalid length: decoded key is {len(raw)} bytes, "
            f"expected {_DECODED_LEN}"
        )
    payload, checksum = raw[:_PAYLOAD_LEN], raw[_PAYLOAD_LEN:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise XpubValidationError(
            "invalid checksum: the key is corrupted or not a valid "
            "Base58Check extended key"
        )

    version, derivation = _PUBLIC_PREFIXES[prefix]
    if payload[:4] != version:
        raise XpubValidationError(
            f"version mismatch: {prefix} prefix but version "
            f"{payload[:4].hex()}"
        )
    if payload[_KEY_DATA_OFFSET] not in (0x02, 0x03):
        raise XpubValidationError(
            "invalid key data: not a compressed public key"
        )

    return XpubSignal(
        xpub_key=key,
        derivation_type=derivation,
        gap_limit=gap_limit,
    )
