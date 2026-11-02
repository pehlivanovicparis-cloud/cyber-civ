"""Tests for the Bitcoin xpub validator (Phase 1 ingest).

Covers: prefix isolation (xpub/ypub/zpub), hard rejection of
extended private keys (xprv/yprv/zprv), Base58Check checksum
validation (a corrupted key fails), structural length checks, and
gap_limit bounds. No derivation happens here — that is Phase 2.

The validator is stdlib-only (hashlib + string parsing). It accepts
an extended PUBLIC key only; an xpub carries zero spending or
signing authority.

Run:  python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import unittest

from recovery.xpub import (
    XpubSignal,
    XpubValidationError,
    validate_xpub,
)

# BIP32 test vector 1 master xpub (seed
# 000102030405060708090a0b0c0d0e0f). Public test vector, not a real
# key. Decodes to 82 bytes, version 0x0488b21e, compressed pubkey.
VALID_XPUB = (
    "xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESF"
    "jqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8"
)


class TestXpubValidation(unittest.TestCase):
    def test_valid_xpub(self):
        result = validate_xpub(VALID_XPUB)
        self.assertIsInstance(result, XpubSignal)
        self.assertEqual(result.xpub_key, VALID_XPUB)
        self.assertEqual(result.derivation_type, "BIP44")
        self.assertEqual(result.gap_limit, 20)

    def test_derivation_type_from_prefix(self):
        # ypub -> BIP49, zpub -> BIP84. Only the prefix changes here;
        # the checksum is recomputed by rebuilding the key with the
        # matching version bytes, so these are structurally valid.
        from recovery.xpub import _b58encode, _b58decode
        import hashlib

        raw = _b58decode(VALID_XPUB)
        payload = bytearray(raw[:78])
        for prefix, version, derivation in (
            ("ypub", bytes.fromhex("049d7cb2"), "BIP49"),
            ("zpub", bytes.fromhex("04b24746"), "BIP84"),
        ):
            payload[:4] = version
            checksum = hashlib.sha256(
                hashlib.sha256(bytes(payload)).digest()
            ).digest()[:4]
            key = _b58encode(bytes(payload) + checksum)
            self.assertTrue(key.startswith(prefix))
            result = validate_xpub(key)
            self.assertEqual(result.derivation_type, derivation)

    def test_gap_limit_bounds(self):
        self.assertEqual(validate_xpub(VALID_XPUB, gap_limit=10).gap_limit, 10)
        self.assertEqual(validate_xpub(VALID_XPUB, gap_limit=100).gap_limit, 100)
        with self.assertRaises(XpubValidationError):
            validate_xpub(VALID_XPUB, gap_limit=9)
        with self.assertRaises(XpubValidationError):
            validate_xpub(VALID_XPUB, gap_limit=101)

    def test_private_key_rejected(self):
        # Any extended private key prefix is a hard security failure,
        # checked before any other validation.
        for bad in (
            "xprv9s21ZrQH143K3QTDL4LXw2F9HLesUjvQz8t3gSWTu1ZbE9TqY7k5f6",
            "yprvAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "zprvAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        ):
            with self.assertRaises(XpubValidationError) as ctx:
                validate_xpub(bad)
            self.assertIn("private", str(ctx.exception).lower())

    def test_bad_prefix(self):
        with self.assertRaises(XpubValidationError):
            validate_xpub("abc123notakey")
        with self.assertRaises(XpubValidationError):
            # A bare Bitcoin address is not an xpub.
            validate_xpub("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")

    def test_corrupted_checksum(self):
        # Flip one character in the middle of the valid xpub. The
        # Base58Check checksum must catch it.
        corrupted = VALID_XPUB[:40] + (
            "B" if VALID_XPUB[40] != "B" else "C"
        ) + VALID_XPUB[41:]
        self.assertNotEqual(corrupted, VALID_XPUB)
        with self.assertRaises(XpubValidationError):
            validate_xpub(corrupted)

    def test_truncated_key(self):
        with self.assertRaises(XpubValidationError):
            validate_xpub(VALID_XPUB[:60])

    def test_empty_and_whitespace(self):
        with self.assertRaises(XpubValidationError):
            validate_xpub("")
        with self.assertRaises(XpubValidationError):
            validate_xpub("   ")
        # Surrounding whitespace is stripped, not fatal.
        result = validate_xpub("  " + VALID_XPUB + "\n")
        self.assertEqual(result.xpub_key, VALID_XPUB)

    def test_invalid_base58_character(self):
        # '0', 'O', 'I', 'l' are not in the Base58 alphabet.
        bad = "xpub" + "0" * 107
        with self.assertRaises(XpubValidationError):
            validate_xpub(bad)

    def test_base58_leading_zeroes_preserved(self):
        # Leading zero bytes must round-trip as leading "1"s;
        # byte alignment must never shift or strip. (Pre-freeze
        # review.)
        from recovery.xpub import _b58decode, _b58encode
        for raw in (b"\x00", b"\x00\x00abc", b"\x00\x00\x00\x01\x02"):
            self.assertEqual(_b58decode(_b58encode(raw)), raw)
        self.assertEqual(_b58encode(b"\x00"), "1")
        self.assertEqual(_b58decode("1"), b"\x00")
        self.assertEqual(_b58decode("1112"), b"\x00\x00\x00\x01")


if __name__ == "__main__":
    unittest.main()
