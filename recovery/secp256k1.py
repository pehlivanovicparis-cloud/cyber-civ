"""Cyber CIV — Recovery subsystem, secp256k1 elliptic-curve arithmetic.

Pure-Python implementation of the secp256k1 curve used by Bitcoin.
Stdlib only; no C bindings, no external crypto library.

Curve: y^2 = x^3 + 7 (mod p), where p = 2^256 - 2^32 - 977.
Order n, generator point G are the standard secp256k1 parameters.

Points are (x, y) tuples of ints. The point at infinity is None.
All arithmetic is over the finite field F_p. The modular inverse
uses Python's built-in pow(a, -1, p) (C-backed, constant-time
enough for this use; this is key derivation, not signing).
"""

from __future__ import annotations

# Prime modulus: 2^256 - 2^32 - 977.
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
# Order of the generator point.
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
# Generator point.
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _mod_inverse(a: int, modulus: int = P) -> int:
    """Modular inverse of a mod modulus. Raises ZeroDivisionError
    if a is 0 mod modulus (no inverse exists)."""
    return pow(a % modulus, -1, modulus)


def point_add(p1, p2):
    """Add two curve points. Either may be None (infinity).

    Handles the point at infinity without a division by zero:
    infinity + Q = Q, and P + (-P) = infinity (detected by
    x1 == x2 and y1 == -y2 mod p, before any slope is computed).
    """
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        return point_double(p1)
    slope = ((y2 - y1) * _mod_inverse(x2 - x1)) % P
    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return (x3, y3)


def point_double(p):
    """Double a curve point. Returns None (infinity) when y == 0."""
    if p is None:
        return None
    x, y = p
    if y == 0:
        return None
    slope = ((3 * x * x) * _mod_inverse(2 * y)) % P
    x3 = (slope * slope - 2 * x) % P
    y3 = (slope * (x - x3) - y) % P
    return (x3, y3)


def scalar_mult(k: int, point=G):
    """Multiply a curve point by a scalar (double-and-add).

    k is reduced mod N first (the group order), so k == N gives the
    point at infinity, and k == 0 gives infinity as well.
    """
    k = k % N
    result = None
    addend = point
    while k:
        if k & 1:
            result = point_add(result, addend)
        addend = point_double(addend)
        k >>= 1
    return result


def point_to_compressed(point) -> bytes:
    """Serialize a curve point to its 33-byte compressed form.

    Prefix 0x02 if y is even, 0x03 if y is odd, then the 32-byte
    big-endian x coordinate.
    """
    if point is None:
        raise ValueError("cannot compress the point at infinity")
    x, y = point
    prefix = b"\x02" if y % 2 == 0 else b"\x03"
    return prefix + x.to_bytes(32, "big")


def compressed_to_point(data: bytes):
    """Parse a 33-byte compressed public key into a curve point."""
    if len(data) != 33 or data[0] not in (0x02, 0x03):
        raise ValueError("not a compressed public key")
    x = int.from_bytes(data[1:], "big")
    if x >= P:
        raise ValueError("x coordinate out of range")
    # y^2 = x^3 + 7. p % 4 == 3, so sqrt is y = (x^3+7)^((p+1)/4).
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if (y * y) % P != y_sq:
        raise ValueError("no square root: not a curve point")
    # Pick the root matching the prefix parity.
    if (y % 2 == 0) != (data[0] == 0x02):
        y = P - y
    return (x, y)
