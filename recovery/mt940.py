"""Cyber CIV — Recovery subsystem, MT940 banking-signal parser.

Parses a raw SWIFT MT940 statement snippet the user pastes and
extracts only balance / currency / transaction-velocity state. The
parser is stdlib-only and never stores or returns an account number
or statement number.

Design (see INGEST-PHASE1-DESIGN-2026-11-02.md):
- Tags handled: :60F: (opening balance), :62F: (closing balance),
  :61: (statement lines -> velocity). Every other tag is ignored.
- :25: (account ID) and :28C: (statement number) are stripped at
  ingress by construction: they are never extracted, never stored,
  and never appear in the returned object.
- Amounts use a comma decimal separator (MT940 convention).
  Dates are YYMMDD (value date) and MMDD (entry date).
- Malformed input raises MT940ParseError — a clean exception, never
  a crash. The service layer maps it to a generic error response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


class MT940ParseError(ValueError):
    """Raised when an MT940 snippet is malformed or incomplete."""


@dataclass(frozen=True)
class TransactionVelocity:
    """One statement line: a single dated movement."""

    entry_date: date
    amount: float
    is_credit: bool
    transaction_type: str  # SWIFT type code, e.g. NTRF, NMSC.


@dataclass(frozen=True)
class MT940Signal:
    """Extracted state from an MT940 snippet.

    Holds only balance / currency / velocity. No field holds an
    account number or statement number.
    """

    currency: str  # 3-char ISO code, e.g. USD, EUR.
    opening_balance: float
    closing_balance: float
    statement_period_start: date
    transaction_count: int
    velocity_profile: list = field(default_factory=list)


# :60F: / :62F: balance line: C|D + YYMMDD + currency + amount.
# Amount uses a comma decimal separator, e.g. 1234,56.
_BALANCE_RE = re.compile(
    r"^([CD])(\d{6})([A-Z]{3})(\d+,\d+)$"
)

# :61: statement line: YYMMDD + optional MMDD entry date +
# debit/credit mark + amount + transaction type + reference.
# Debit/credit marks: C, D, RC (reversal of credit = debit),
# RD (reversal of debit = credit), EC, ED.
_STATEMENT_LINE_RE = re.compile(
    r"^(\d{6})(\d{4})?((?:RC|RD|EC|ED|[CD]))(\d+,\d+)"
    r"([SNF][A-Z]{3})(.*)$"
)

# Lines that start a tag look like :60F: or :61: or :25:.
_TAG_RE = re.compile(r"^:(\d{2}[A-Z]?):(.*)$")

# Marks that are credits. RC reverses a credit (so it is a debit);
# RD reverses a debit (so it is a credit).
_CREDIT_MARKS = {"C", "EC", "RD"}


def _parse_amount(raw: str) -> float:
    """MT940 amount: digits with a comma decimal separator."""
    return float(raw.replace(",", "."))


def _parse_yymmdd(raw: str) -> date:
    """YYMMDD -> date. Raises MT940ParseError on an invalid date."""
    try:
        year = 2000 + int(raw[0:2])
        month = int(raw[2:4])
        day = int(raw[4:6])
        return date(year, month, day)
    except ValueError as exc:
        raise MT940ParseError(f"invalid date {raw!r}") from exc


def _parse_balance(content: str, tag: str) -> tuple:
    """Parse a :60F: / :62F: line.

    Returns (signed_amount, currency, period_date). A D mark makes
    the balance negative.
    """
    match = _BALANCE_RE.match(content.strip())
    if not match:
        raise MT940ParseError(f"malformed {tag} balance line")
    mark, raw_date, currency, raw_amount = match.groups()
    amount = _parse_amount(raw_amount)
    if mark == "D":
        amount = -amount
    return amount, currency, _parse_yymmdd(raw_date)


def _parse_statement_line(content: str) -> TransactionVelocity:
    """Parse one :61: line into a TransactionVelocity."""
    match = _STATEMENT_LINE_RE.match(content.strip())
    if not match:
        raise MT940ParseError("malformed :61: statement line")
    raw_value_date, raw_entry_date, mark, raw_amount, tx_type, _ref = (
        match.groups()
    )
    value_date = _parse_yymmdd(raw_value_date)
    if raw_entry_date:
        try:
            entry_date = date(
                value_date.year,
                int(raw_entry_date[0:2]),
                int(raw_entry_date[2:4]),
            )
        except ValueError as exc:
            raise MT940ParseError(
                f"invalid entry date {raw_entry_date!r}"
            ) from exc
    else:
        entry_date = value_date
    return TransactionVelocity(
        entry_date=entry_date,
        amount=_parse_amount(raw_amount),
        is_credit=mark in _CREDIT_MARKS,
        transaction_type=tx_type,
    )


def parse_mt940(text: str) -> MT940Signal:
    """Parse a raw MT940 snippet into an MT940Signal.

    Extracts currency, opening/closing balances, the statement
    period start, and one velocity entry per :61: line. The account
    ID (:25:) and statement number (:28C:) are never extracted.
    Raises MT940ParseError on malformed or incomplete input.
    """
    if not text or not text.strip():
        raise MT940ParseError("empty MT940 input")

    opening = None
    closing = None
    opening_currency = None
    closing_currency = None
    period_start = None
    velocity: list = []

    for line in text.splitlines():
        match = _TAG_RE.match(line.strip())
        if not match:
            # Continuation of a free-text field (:86: etc.). Ignored.
            continue
        tag, content = match.groups()
        if tag == "60F":
            amount, cur, period_date = _parse_balance(content, tag)
            if opening is not None:
                raise MT940ParseError("duplicate :60F: opening balance")
            opening = amount
            opening_currency = cur
            period_start = period_date
        elif tag == "62F":
            amount, cur, _period_date = _parse_balance(content, tag)
            if closing is not None:
                raise MT940ParseError("duplicate :62F: closing balance")
            closing = amount
            closing_currency = cur
        elif tag == "61":
            velocity.append(_parse_statement_line(content))
        # All other tags (:20:, :25:, :28C:, :86:, :64:, ...) are
        # ignored. :25: and :28C: are dropped here by construction.

    if opening is None:
        raise MT940ParseError("missing :60F: opening balance")
    if closing is None:
        raise MT940ParseError("missing :62F: closing balance")
    # Currency check is order-independent: a mismatch is caught
    # whether :60F: or :62F: appears first in the file.
    if opening_currency != closing_currency:
        raise MT940ParseError(
            "currency mismatch between :60F: and :62F:"
        )

    return MT940Signal(
        currency=opening_currency,
        opening_balance=opening,
        closing_balance=closing,
        statement_period_start=period_start,
        transaction_count=len(velocity),
        velocity_profile=velocity,
    )
