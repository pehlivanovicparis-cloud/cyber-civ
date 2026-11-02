"""Tests for the MT940 banking-signal parser (Phase 1 ingest).

Covers: strict SWIFT tag parsing (:60F: opening, :62F: closing,
:61: statement lines), extraction of balances/currency/velocity,
absolute scrubbing of account identifiers (:25:, :28C:), and
graceful failure on malformed input.

The parser is stdlib-only. It accepts a raw MT940 text snippet the
user pastes; it extracts only balance/currency/velocity state and
never stores or returns an account number or statement number.

Run:  python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import unittest
from datetime import date

from recovery.mt940 import (
    MT940ParseError,
    MT940Signal,
    parse_mt940,
)

# A structurally valid MT940 statement block. Opening 1234.56 USD,
# one debit of 500.00 (NTRF), one credit of 200.00 (NTRF), closing
# 934.56 USD. The account number and statement number are present
# in the input and must NOT survive into the output.
VALID_BLOCK = """\
:20:STARTUMSE
:25:DE89370400440532013000
:28C:00000/001
:60F:C230101USD1234,56
:61:2301020102D500,00NTRFNONREF
:86:Payment for services
:61:2301030103C200,00NTRFNONREF
:86:Refund
:62F:C230103USD934,56
"""

ACCOUNT_NUMBER = "DE89370400440532013000"
STATEMENT_NUMBER = "00000/001"


class TestMT940Parsing(unittest.TestCase):
    def test_opening_and_closing_balances(self):
        result = parse_mt940(VALID_BLOCK)
        self.assertIsInstance(result, MT940Signal)
        self.assertEqual(result.currency, "USD")
        self.assertAlmostEqual(result.opening_balance, 1234.56)
        self.assertAlmostEqual(result.closing_balance, 934.56)

    def test_statement_period_start(self):
        result = parse_mt940(VALID_BLOCK)
        self.assertEqual(result.statement_period_start, date(2023, 1, 1))

    def test_transaction_velocity(self):
        result = parse_mt940(VALID_BLOCK)
        self.assertEqual(result.transaction_count, 2)
        self.assertEqual(len(result.velocity_profile), 2)
        first, second = result.velocity_profile
        # Debit of 500.00 on 2023-01-02, type NTRF.
        self.assertEqual(first.entry_date, date(2023, 1, 2))
        self.assertAlmostEqual(first.amount, 500.00)
        self.assertFalse(first.is_credit)
        self.assertEqual(first.transaction_type, "NTRF")
        # Credit of 200.00 on 2023-01-03, type NTRF.
        self.assertEqual(second.entry_date, date(2023, 1, 3))
        self.assertAlmostEqual(second.amount, 200.00)
        self.assertTrue(second.is_credit)
        self.assertEqual(second.transaction_type, "NTRF")

    def test_debit_opening_balance(self):
        # A D-marked opening balance is negative.
        block = VALID_BLOCK.replace(
            ":60F:C230101USD1234,56", ":60F:D230101USD1234,56"
        )
        result = parse_mt940(block)
        self.assertAlmostEqual(result.opening_balance, -1234.56)

    def test_account_scrubbing(self):
        # The account number and statement number must not exist
        # anywhere in the returned object or its string form.
        result = parse_mt940(VALID_BLOCK)
        blob = repr(result)
        for field_value in (
            result.currency,
            str(result.opening_balance),
            str(result.closing_balance),
            str(result.statement_period_start),
            str(result.transaction_count),
        ):
            self.assertNotIn(ACCOUNT_NUMBER, field_value)
            self.assertNotIn(STATEMENT_NUMBER, field_value)
        self.assertNotIn(ACCOUNT_NUMBER, blob)
        self.assertNotIn(STATEMENT_NUMBER, blob)
        # And no field of the dataclass holds them.
        self.assertFalse(
            any(
                ACCOUNT_NUMBER in str(v) or STATEMENT_NUMBER in str(v)
                for v in result.__dict__.values()
            )
        )

    def test_other_tags_ignored(self):
        # :20: and :86: carry references/free text (possible PII);
        # they must not leak into the output.
        result = parse_mt940(VALID_BLOCK)
        blob = repr(result)
        self.assertNotIn("STARTUMSE", blob)
        self.assertNotIn("Payment for services", blob)
        self.assertNotIn("Refund", blob)


class TestMT940Malformation(unittest.TestCase):
    def test_empty_input(self):
        with self.assertRaises(MT940ParseError):
            parse_mt940("")

    def test_missing_opening_balance(self):
        block = VALID_BLOCK.replace(":60F:C230101USD1234,56\n", "")
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_missing_closing_balance(self):
        block = VALID_BLOCK.replace(":62F:C230103USD934,56\n", "")
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_truncated_statement_line(self):
        # A :61: line cut off mid-amount.
        block = VALID_BLOCK.replace(
            ":61:2301020102D500,00NTRFNONREF",
            ":61:2301020102D50",
        )
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_invalid_transaction_type(self):
        # A :61: line with a transaction type code that does not
        # start with S, N, or F. (A digit-led code is ambiguous with
        # the amount's decimal places, so it is not a valid test of
        # the type check; XXXX is unambiguous.)
        block = VALID_BLOCK.replace(
            ":61:2301020102D500,00NTRFNONREF",
            ":61:2301020102D500,00XXXXNONREF",
        )
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_bad_amount_format(self):
        # Amount with a dot decimal separator instead of a comma.
        block = VALID_BLOCK.replace(
            ":60F:C230101USD1234,56", ":60F:C230101USD1234.56"
        )
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_bad_date(self):
        # A month value that does not exist.
        block = VALID_BLOCK.replace(
            ":60F:C230101USD1234,56", ":60F:C231301USD1234,56"
        )
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_garbage_input(self):
        with self.assertRaises(MT940ParseError):
            parse_mt940("this is not a SWIFT message at all\n")

    def test_currency_mismatch_swapped_order(self):
        # The mismatch must be caught even when :62F: appears
        # BEFORE :60F: in the file. (Found in the 2026-11-02
        # pre-freeze review: the parser set currency from :62F:
        # first, then :60F: overwrote it without a check.)
        block = (":62F:C230101EUR100,00\n:60F:C230101USD100,00\n")
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)

    def test_swapped_order_matching_currencies(self):
        block = (":62F:C230101USD100,00\n:60F:C230101USD100,00\n")
        result = parse_mt940(block)
        self.assertEqual(result.currency, "USD")

    def test_zero_amounts(self):
        # 0,00 is a legal amount, not a missing value.
        block = (":60F:C230101USD0,00\n"
                 ":61:2301020102D0,00NTRFNONREF\n"
                 ":62F:C230103USD0,00\n")
        result = parse_mt940(block)
        self.assertEqual(result.opening_balance, 0.0)
        self.assertEqual(result.closing_balance, 0.0)
        self.assertEqual(result.velocity_profile[0].amount, 0.0)

    def test_absent_decimal_rejected(self):
        # "1234," and "1234" have no decimal part; both are
        # malformed, not zero.
        for bad in (":60F:C230101USD1234,\n:62F:C230101USD0,00\n",
                    ":60F:C230101USD1234\n:62F:C230101USD0,00\n"):
            with self.assertRaises(MT940ParseError):
                parse_mt940(bad)

    def test_leap_year_dates(self):
        # 2024 is a leap year (Feb 29 exists); 2023 is not.
        good = ":60F:C240229USD100,00\n:62F:C240229USD100,00\n"
        result = parse_mt940(good)
        self.assertEqual(
            (result.statement_period_start.year,
             result.statement_period_start.month,
             result.statement_period_start.day),
            (2024, 2, 29),
        )
        bad = ":60F:C230229USD100,00\n:62F:C230229USD100,00\n"
        with self.assertRaises(MT940ParseError):
            parse_mt940(bad)

    def test_duplicate_opening_balance(self):
        block = (":60F:C230101USD100,00\n:60F:C230101USD200,00\n"
                 ":62F:C230101USD100,00\n")
        with self.assertRaises(MT940ParseError):
            parse_mt940(block)


if __name__ == "__main__":
    unittest.main()
