"""Cyber CIV — Recovery subsystem, Bech32 encoding (BIP173).

Pure-Python Bech32 for SegWit addresses. Stdlib only.

Bech32: a human-readable part (HRP, e.g. "bc"), a "1" separator,
a data part (the witness version + the program converted from
8-bit to 5-bit groups), and a 6-character checksum. The witness
version and the program bytes are separate inputs — the version is
never mixed into the program.

Only encoding is needed for address derivation; decoding is
provided for verification (round-trip checks).
"""

from __future__ import annotations

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

# Bech32 generator polynomial constants.
_GENERATOR = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _polymod(values: list) -> int:
    check = 1
    for value in values:
        top = check >> 25
        check = ((check & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                check ^= _GENERATOR[i]
    return check


def _hrp_expand(hrp: str) -> list:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _create_checksum(hrp: str, data: list) -> list:
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _convertbits(data: bytes, frombits: int, tobits: int, pad: bool = True):
    """Convert a byte string between bit group sizes."""
    acc = 0
    bits = 0
    result = []
    max_value = (1 << tobits) - 1
    for byte in data:
        acc = (acc << frombits) | byte
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            result.append((acc >> bits) & max_value)
    if pad:
        if bits:
            result.append((acc << (tobits - bits)) & max_value)
    elif bits >= frombits or ((acc << (tobits - bits)) & max_value):
        raise ValueError("invalid bit conversion")
    return result


def bech32_encode(hrp: str, witness_version: int, program: bytes) -> str:
    """Encode a SegWit address (BIP173).

    hrp: "bc" for mainnet, "tb" for testnet.
    witness_version: 0 for P2WPKH / P2WSH (native SegWit).
    program: the witness program bytes (20 bytes for P2WPKH).
    """
    if not 0 <= witness_version <= 16:
        raise ValueError(f"witness version {witness_version} out of range")
    if not 2 <= len(program) <= 40:
        raise ValueError(f"program length {len(program)} out of range")
    if witness_version == 0 and len(program) not in (20, 32):
        raise ValueError("version 0 program must be 20 or 32 bytes")
    data = [witness_version] + _convertbits(program, 8, 5)
    combined = data + _create_checksum(hrp, data)
    return hrp + "1" + "".join(_CHARSET[d] for d in combined)


def bech32_decode(address: str):
    """Decode a Bech32 address. Returns (hrp, witness_version,
    program bytes). Raises ValueError on a bad checksum or format."""
    if address.lower() != address and address.upper() != address:
        raise ValueError("mixed-case Bech32 address")
    address = address.lower()
    if "1" not in address:
        raise ValueError("missing Bech32 separator")
    hrp, data_part = address.rsplit("1", 1)
    if not hrp or len(data_part) < 6:
        raise ValueError("invalid Bech32 address")
    try:
        data = [_CHARSET.index(c) for c in data_part]
    except ValueError as exc:
        raise ValueError("invalid Bech32 character") from exc
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("invalid Bech32 checksum")
    payload = data[:-6]
    if not payload:
        raise ValueError("empty Bech32 payload")
    witness_version = payload[0]
    program = bytes(_convertbits(bytes(payload[1:]), 5, 8, pad=False))
    # Same guardrails as encode: a valid checksum does not make an
    # illegal version or program length acceptable.
    if not 0 <= witness_version <= 16:
        raise ValueError(
            f"witness version {witness_version} out of range"
        )
    if not 2 <= len(program) <= 40:
        raise ValueError(f"program length {len(program)} out of range")
    if witness_version == 0 and len(program) not in (20, 32):
        raise ValueError("version 0 program must be 20 or 32 bytes")
    return hrp, witness_version, program
