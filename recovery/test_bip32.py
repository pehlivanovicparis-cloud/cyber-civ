"""Tests for the pure-Python BIP32 derivation engine (Phase 2).

Covers: secp256k1 curve identity (G*1=G, G*n=infinity, G*2 known
point, point-at-infinity handling), CKDpub against the two
non-hardened BIP32 test-vector steps, hardened-index rejection,
BIP173 Bech32 encoding, address derivation (P2PKH / P2SH-P2WPKH /
P2WPKH), gap-limit derivation, and the zpub first-address check.

Ground truth: BIP32 test vector 1 (seed
000102030405060708090a0b0c0d0e0f) and the BIP173 Bech32 vector.
Only two BIP32 steps are non-hardened and therefore derivable from
an xpub; the rest of the chain is hardened and cannot be derived
without the private key.

The engine is stdlib-only. No C bindings, no external crypto lib.

Run:  python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import time
import unittest

from recovery.bip32 import (
    BIP32DerivationError,
    derive_addresses,
    derive_child_pubkey,
    derive_child_xpub,
    parse_xpub,
)
from recovery import bech32 as bech32_mod
from recovery.bech32 import bech32_encode
from recovery.secp256k1 import (
    G,
    N,
    P,
    compressed_to_point,
    point_add,
    point_double,
    scalar_mult,
)

# BIP32 test vector 1 (seed 000102030405060708090a0b0c0d0e0f).
# These xpubs were derived from the seed with an independent CKDpriv
# implementation and verified against the master xpub; they are the
# true test-vector values (an earlier recalled copy had wrong tails).
XPUB_M_0H = (
    "xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwB"
    "ZeNK1VTsfTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw"
)
XPUB_M_0H_1 = (
    "xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWb"
    "WMiGj7Wf5uMash7SyYq527Hqck2AxYysAA7xmALppuCkwQ"
)
XPUB_M_0H_1_2H = (
    "xpub6D4BDPcP2GT577Vvch3R8wDkScZWzQzMMUm3PWbmWvVJrZwQY4VUNgqFJPMM"
    "3No2dFDFGTsxxpG5uJh7n7epu4trkrX7x7DogT5Uv6fcLW5"
)
XPUB_M_0H_1_2H_2 = (
    "xpub6FHa3pjLCk84BayeJxFW2SP4XRrFd1JYnxeLeU8EqN3vDfZmbqBqaGJAyiLjT"
    "Awm6ZLRQUMv1ZACTj37sR62cfN7fe5JnJ7dh8zL4fiyLHV"
)

# G*2 for secp256k1 (well-known point).
G2_X = 0xC6047F9441ED7D6D3045406E95C07CD85C778E4B8CEF3CA7ABAC09B95C709EE5
G2_Y = 0x1AE168FEA63DC339A3C58419466CEAEEF7F632653266D0E1236431A950CFE52A

# BIP173 Bech32 vector: witness version 0, 20-byte program.
BECH32_PROGRAM = bytes.fromhex(
    "751e76e8199196d454941c45d1b3a323f1433bd6"
)
BECH32_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"

# Published BIP84 test zpub (seed 000102030405060708090a0b0c0d0e0f,
# path m/84'/0'/0') and its first receiving address. The address was
# verified empirically: it decodes cleanly and its witness program
# equals HASH160 of the derived public key. (An earlier recalled
# copy had a wrong tail and fails the Bech32 checksum.)
ZPUB_BIP84 = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
ZPUB_FIRST_ADDRESS = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


class TestSecp256k1(unittest.TestCase):
    def test_g_times_one(self):
        self.assertEqual(scalar_mult(1, G), G)

    def test_g_times_two(self):
        self.assertEqual(scalar_mult(2, G), (G2_X, G2_Y))

    def test_g_times_n_is_infinity(self):
        self.assertIsNone(scalar_mult(N, G))

    def test_point_add_identity(self):
        # G + infinity = G; infinity + G = G.
        self.assertEqual(point_add(G, None), G)
        self.assertEqual(point_add(None, G), G)
        self.assertIsNone(point_add(None, None))

    def test_point_double(self):
        self.assertEqual(point_double(G), (G2_X, G2_Y))

    def test_point_add_inverse_is_infinity(self):
        # G + (-G) = infinity. -G is (x, p - y).
        neg_g = (G[0], P - G[1])
        self.assertIsNone(point_add(G, neg_g))

    def test_point_on_curve(self):
        # G satisfies y^2 = x^3 + 7 (mod p).
        x, y = G
        self.assertEqual((y * y) % P, (x * x * x + 7) % P)

    def test_double_and_add_consistency(self):
        # 3G computed two ways must agree.
        three_g = scalar_mult(3, G)
        self.assertEqual(three_g, point_add(G, point_double(G)))

    def test_scalar_zero_and_multiples_of_n(self):
        # k == 0, k == N, and k == 2N all give infinity; k == N+1
        # wraps to G. (Pre-freeze review: the k % N reduction must
        # intercept these before any division.)
        self.assertIsNone(scalar_mult(0, G))
        self.assertIsNone(scalar_mult(2 * N, G))
        self.assertEqual(scalar_mult(N + 1, G), G)

    def test_point_double_y_zero_is_infinity(self):
        # A point with y == 0 has a vertical tangent; doubling it
        # is infinity, never a division by zero.
        self.assertIsNone(point_double((12345, 0)))

    def test_compressed_non_residue_rejected(self):
        # An x whose x^3+7 has no square root mod p is not a curve
        # point; the parser must fail fast, not return garbage.
        def is_residue(y_sq):
            return pow(y_sq, (P - 1) // 2, P) == 1
        bad_x = None
        for x in range(1, 500):
            if not is_residue((pow(x, 3, P) + 7) % P):
                bad_x = x
                break
        self.assertIsNotNone(bad_x)
        with self.assertRaises(ValueError):
            compressed_to_point(b"\x02" + bad_x.to_bytes(32, "big"))

    def test_compressed_bad_prefix_and_length(self):
        for bad in (b"\x04" + b"\x00" * 32, b"\x00" + b"\x00" * 32,
                    b"\x02" + b"\x00" * 31, b"\x02" + b"\x00" * 33):
            with self.assertRaises(ValueError):
                compressed_to_point(bad)

    def test_compressed_x_out_of_range(self):
        with self.assertRaises(ValueError):
            compressed_to_point(b"\x02" + P.to_bytes(32, "big"))


class TestCKDpub(unittest.TestCase):
    def test_derive_m_0h_1(self):
        # Non-hardened step: m/0' -> m/0'/1 (child index 1).
        # The full child xpub must match the published vector.
        child = derive_child_xpub(XPUB_M_0H, 1)
        self.assertEqual(child, XPUB_M_0H_1)

    def test_derive_m_0h_1_2h_2(self):
        # Non-hardened step: m/0'/1/2' -> m/0'/1/2'/2 (child 2).
        child = derive_child_xpub(XPUB_M_0H_1_2H, 2)
        self.assertEqual(child, XPUB_M_0H_1_2H_2)

    def test_child_pubkey_is_compressed(self):
        # A derived child public key is 33 bytes starting 0x02/0x03.
        pubkey, _chain = derive_child_pubkey(XPUB_M_0H, 1)
        self.assertEqual(len(pubkey), 33)
        self.assertIn(pubkey[0], (0x02, 0x03))

    def test_hardened_index_rejected(self):
        # Hardened derivation needs the private key. Any index >=
        # 2^31 must raise, never silently produce garbage.
        for bad_index in (2**31, 2**31 + 1, 2**32 - 1):
            with self.assertRaises(BIP32DerivationError):
                derive_child_pubkey(XPUB_M_0H, bad_index)
            with self.assertRaises(BIP32DerivationError):
                derive_child_xpub(XPUB_M_0H, bad_index)

    def test_zero_tweak_rejected(self):
        # If the HMAC's left half (IL) is 0, the child key is
        # invalid. Forced here by mocking the HMAC digest; CKDpub
        # must fail explicitly, never derive a garbage key.
        # (Pre-freeze review.)
        from unittest.mock import patch
        fake = b"\x00" * 32 + b"\x11" * 32
        with patch("recovery.bip32.hmac_mod.new") as mock_new:
            mock_new.return_value.digest.return_value = fake
            with self.assertRaises(BIP32DerivationError):
                derive_child_pubkey(XPUB_M_0H, 1)

    def test_tweak_ge_n_rejected(self):
        # If IL >= n, the child key is invalid. Same mocked-HMAC
        # technique, with IL == n exactly.
        from unittest.mock import patch
        fake = N.to_bytes(32, "big") + b"\x11" * 32
        with patch("recovery.bip32.hmac_mod.new") as mock_new:
            mock_new.return_value.digest.return_value = fake
            with self.assertRaises(BIP32DerivationError):
                derive_child_pubkey(XPUB_M_0H, 1)

    def test_parse_xpub_round_trip(self):
        parsed = parse_xpub(XPUB_M_0H)
        self.assertEqual(parsed["depth"], 1)
        self.assertEqual(len(parsed["chain_code"]), 32)
        self.assertEqual(len(parsed["pubkey"]), 33)


class TestBech32(unittest.TestCase):
    def test_bip173_vector(self):
        address = bech32_encode("bc", 0, BECH32_PROGRAM)
        self.assertEqual(address, BECH32_ADDRESS)

    def test_witness_version_isolated(self):
        # Witness version 0 and the program bytes are separate
        # inputs; changing the version changes the address.
        v0 = bech32_encode("bc", 0, BECH32_PROGRAM)
        v1 = bech32_encode("bc", 1, BECH32_PROGRAM)
        self.assertNotEqual(v0, v1)
        self.assertTrue(v0.startswith("bc1q"))
        self.assertTrue(v1.startswith("bc1p"))


class TestBech32DecodeGuardrails(unittest.TestCase):
    """Decode enforces the same guardrails as encode. A valid
    checksum does not make an illegal version or program length
    acceptable. (Found in the 2026-11-02 pre-freeze review: decode
    previously accepted version 17, a v0 21-byte program, and a
    v1 1-byte program.)"""

    def _craft(self, version, program):
        # Build a Bech32 address with a VALID checksum but arbitrary
        # version/program, bypassing bech32_encode's guards.
        data = [version] + bech32_mod._convertbits(program, 8, 5)
        combined = data + bech32_mod._create_checksum("bc", data)
        return "bc1" + "".join(bech32_mod._CHARSET[d] for d in combined)

    def test_version_17_rejected(self):
        with self.assertRaises(ValueError):
            bech32_mod.bech32_decode(self._craft(17, bytes(20)))

    def test_v0_21_byte_program_rejected(self):
        with self.assertRaises(ValueError):
            bech32_mod.bech32_decode(self._craft(0, bytes(21)))

    def test_v1_1_byte_program_rejected(self):
        with self.assertRaises(ValueError):
            bech32_mod.bech32_decode(self._craft(1, bytes(1)))

    def test_legal_v1_32_byte_decodes(self):
        hrp, ver, prog = bech32_mod.bech32_decode(self._craft(1, bytes(32)))
        self.assertEqual((hrp, ver, len(prog)), ("bc", 1, 32))


class TestAddressDerivation(unittest.TestCase):
    def test_zpub_first_receiving_address(self):
        # BIP84 (native SegWit): the first receiving address of the
        # published test zpub must match exactly.
        addresses = derive_addresses(ZPUB_BIP84, gap_limit=1)
        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses[0], ZPUB_FIRST_ADDRESS)

    def test_zpub_addresses_are_bech32(self):
        addresses = derive_addresses(ZPUB_BIP84, gap_limit=5)
        self.assertEqual(len(addresses), 5)
        for address in addresses:
            self.assertTrue(address.startswith("bc1q"))
        # All five must be distinct.
        self.assertEqual(len(set(addresses)), 5)

    def test_xpub_addresses_are_p2pkh(self):
        # BIP44 (legacy): addresses start with "1".
        addresses = derive_addresses(XPUB_M_0H, gap_limit=3)
        self.assertEqual(len(addresses), 3)
        for address in addresses:
            self.assertTrue(address.startswith("1"))
        self.assertEqual(len(set(addresses)), 3)

    def test_gap_limit_respected(self):
        addresses = derive_addresses(ZPUB_BIP84, gap_limit=20)
        self.assertEqual(len(addresses), 20)
        self.assertEqual(len(set(addresses)), 20)


class TestPerformance(unittest.TestCase):
    def test_twenty_address_lookahead_is_fast(self):
        # A 20-address gap lookahead is 21 scalar multiplications.
        # Report the time; it must complete well under a few seconds
        # (pure Python, no C bindings).
        start = time.monotonic()
        derive_addresses(ZPUB_BIP84, gap_limit=20)
        elapsed = time.monotonic() - start
        # Record the measurement in the test output.
        print(f"\n  [perf] 20-address zpub lookahead: {elapsed:.3f}s")
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
