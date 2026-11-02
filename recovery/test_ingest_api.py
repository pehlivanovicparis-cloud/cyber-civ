"""Integration tests for the /ingest API endpoints (Phase 2 wiring).

Covers: POST /ingest/mt940 (valid block -> 200 with balances and
velocity and NO account numbers in the response; malformed -> 400;
missing text -> 400; no auth -> 401; oversized body -> 413) and
POST /ingest/xpub (valid zpub -> 200 with derived addresses and
mocked indexer balances; xprv -> 400; bad xpub -> 400; bad
gap_limit -> 400; no auth -> 401). The indexer is mocked — no live
network in these tests.

The ingest endpoints are read-only sensing (like /sense): they do
not change engine state and do not persist. Results return
in-memory in the response.

Run:  python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import json
import unittest
from unittest.mock import patch

from recovery.service import serve
from recovery.test_hardening import _Client, _ServerMixin, _auth_header
from recovery.utxo import AddressInfo

VALID_MT940 = """\
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

ZPUB = (
    "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1A"
    "DqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
)
ZPUB_FIRST_ADDRESS = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def _fake_indexer(address, base_url=None):
    """Mock indexer: the first zpub address has a balance, the rest
    are empty. No live network."""
    if address == ZPUB_FIRST_ADDRESS:
        return AddressInfo(
            address=address, balance_sats=123456, tx_count=178,
            utxos=[{"txid": "a" * 64, "vout": 0, "value": 123456}],
        )
    return AddressInfo(
        address=address, balance_sats=0, tx_count=0, utxos=[]
    )


class TestIngestMT940(unittest.TestCase, _ServerMixin):
    def test_valid_block(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, body = client.post(
            "/ingest/mt940", {"text": VALID_MT940}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["currency"], "USD")
        self.assertAlmostEqual(body["opening_balance"], 1234.56)
        self.assertAlmostEqual(body["closing_balance"], 934.56)
        self.assertEqual(body["statement_period_start"], "2023-01-01")
        self.assertEqual(body["transaction_count"], 2)
        self.assertEqual(len(body["velocity_profile"]), 2)
        first = body["velocity_profile"][0]
        self.assertEqual(first["entry_date"], "2023-01-02")
        self.assertAlmostEqual(first["amount"], 500.00)
        self.assertFalse(first["is_credit"])
        self.assertEqual(first["transaction_type"], "NTRF")

    def test_no_account_numbers_in_response(self):
        # The account number and statement number must not appear
        # anywhere in the JSON response body.
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, raw = client._req(
            "POST", "/ingest/mt940",
            body={"text": VALID_MT940},
        )
        self.assertEqual(status, 200)
        text = raw.decode()
        self.assertNotIn(ACCOUNT_NUMBER, text)
        self.assertNotIn(STATEMENT_NUMBER, text)
        self.assertNotIn("STARTUMSE", text)
        self.assertNotIn("Payment for services", text)

    def test_malformed_block(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, body = client.post(
            "/ingest/mt940", {"text": "not a SWIFT message"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_missing_text(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, body = client.post("/ingest/mt940", {})
        self.assertEqual(status, 400)

    def test_requires_auth(self):
        base = self._start(auth=("user", "pass"), rate_limiter=False)
        client = _Client(base)
        status, _, _ = client.post(
            "/ingest/mt940", {"text": VALID_MT940}
        )
        self.assertEqual(status, 401)
        authed = _Client(base, auth=_auth_header("user", "pass"))
        status, _, body = authed.post(
            "/ingest/mt940", {"text": VALID_MT940}
        )
        self.assertEqual(status, 200)

    def test_oversized_body(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        big = "x" * 70000
        status, _, _ = client.post("/ingest/mt940", {"text": big})
        self.assertEqual(status, 413)

    def test_does_not_mutate_engine_state(self):
        # Ingest is read-only sensing: the engine cycle must not
        # advance and the accounts must be unchanged (the engine
        # starts with a RECOVERY-WALLET account by default).
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        _, _, before = client.get("/health")
        _, _, summary_before = client.get("/summary")
        client.post("/ingest/mt940", {"text": VALID_MT940})
        _, _, after = client.get("/health")
        _, _, summary_after = client.get("/summary")
        self.assertEqual(before["cycle"], after["cycle"])
        self.assertEqual(
            summary_before["accounts"], summary_after["accounts"]
        )


class TestIngestXpub(unittest.TestCase, _ServerMixin):
    def test_valid_zpub(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        with patch(
            "recovery.service.get_address_info",
            side_effect=_fake_indexer,
        ):
            status, _, body = client.post(
                "/ingest/xpub", {"xpub": ZPUB, "gap_limit": 10}
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["xpub"], ZPUB)
        self.assertEqual(body["derivation_type"], "BIP84")
        self.assertEqual(body["gap_limit"], 10)
        self.assertEqual(len(body["addresses"]), 10)
        self.assertEqual(body["addresses"][0]["address"],
                         ZPUB_FIRST_ADDRESS)
        self.assertEqual(body["addresses"][0]["balance_sats"], 123456)
        self.assertEqual(body["addresses"][0]["tx_count"], 178)
        self.assertEqual(body["total_balance_sats"], 123456)
        self.assertEqual(body["total_tx_count"], 178)
        self.assertEqual(body["addresses_with_activity"], 1)
        self.assertEqual(body["errors"], [])

    def test_default_gap_limit(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        with patch(
            "recovery.service.get_address_info",
            side_effect=_fake_indexer,
        ):
            status, _, body = client.post("/ingest/xpub", {"xpub": ZPUB})
        self.assertEqual(status, 200)
        self.assertEqual(body["gap_limit"], 20)
        self.assertEqual(len(body["addresses"]), 20)

    def test_private_key_rejected(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, body = client.post(
            "/ingest/xpub",
            {"xpub": "xprv9s21ZrQH143K3QTDL4LXw2F9HLesUjvQz8t3gSWTu1ZbE9TqY7k5f6"},
        )
        self.assertEqual(status, 400)
        self.assertIn("private", body["error"].lower())

    def test_bad_xpub(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, body = client.post(
            "/ingest/xpub", {"xpub": "notakey"}
        )
        self.assertEqual(status, 400)

    def test_bad_gap_limit(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, _ = client.post(
            "/ingest/xpub", {"xpub": ZPUB, "gap_limit": 5}
        )
        self.assertEqual(status, 400)
        status, _, _ = client.post(
            "/ingest/xpub", {"xpub": ZPUB, "gap_limit": 101}
        )
        self.assertEqual(status, 400)

    def test_missing_xpub(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        status, _, body = client.post("/ingest/xpub", {})
        self.assertEqual(status, 400)

    def test_requires_auth(self):
        base = self._start(auth=("user", "pass"), rate_limiter=False)
        client = _Client(base)
        status, _, _ = client.post("/ingest/xpub", {"xpub": ZPUB})
        self.assertEqual(status, 401)

    def test_indexer_failure_is_per_address(self):
        # If the indexer fails for every address, the derivation
        # still succeeds; each address reports balance None and the
        # failure is listed in "errors".
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        from recovery.utxo import IndexerError

        def _down(address, base_url=None):
            raise IndexerError("indexer unreachable")

        with patch(
            "recovery.service.get_address_info", side_effect=_down
        ):
            status, _, body = client.post(
                "/ingest/xpub", {"xpub": ZPUB, "gap_limit": 10}
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["total_balance_sats"], 0)
        self.assertEqual(len(body["addresses"]), 10)
        self.assertTrue(
            all(a["balance_sats"] is None for a in body["addresses"])
        )
        self.assertEqual(len(body["errors"]), 10)

    def test_does_not_mutate_engine_state(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        _, _, before = client.get("/health")
        with patch(
            "recovery.service.get_address_info",
            side_effect=_fake_indexer,
        ):
            client.post("/ingest/xpub", {"xpub": ZPUB, "gap_limit": 10})
        _, _, after = client.get("/health")
        self.assertEqual(before["cycle"], after["cycle"])


class TestIngestIdempotency(unittest.TestCase, _ServerMixin):
    """Calling an ingest endpoint twice with identical payloads
    must leave the engine completely untouched. The endpoints are
    read-only sensing, so a client-side retry (after a timeout or
    a dropped connection) can never duplicate state or race the
    persistence layer."""

    def test_xpub_twice_is_idempotent(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        _, _, before = client.get("/summary")
        cycle_before = before["cycle"]
        with patch(
            "recovery.service.get_address_info",
            side_effect=_fake_indexer,
        ):
            s1, _, b1 = client.post(
                "/ingest/xpub", {"xpub": ZPUB, "gap_limit": 10}
            )
            s2, _, b2 = client.post(
                "/ingest/xpub", {"xpub": ZPUB, "gap_limit": 10}
            )
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertEqual(b1, b2)
        _, _, after = client.get("/summary")
        self.assertEqual(after["cycle"], cycle_before)
        self.assertEqual(after["accounts"], before["accounts"])
        self.assertEqual(after["transfers"], before["transfers"])

    def test_mt940_twice_is_idempotent(self):
        base = self._start(auth=False, rate_limiter=False)
        client = _Client(base)
        _, _, before = client.get("/summary")
        cycle_before = before["cycle"]
        s1, _, b1 = client.post("/ingest/mt940", {"text": VALID_MT940})
        s2, _, b2 = client.post("/ingest/mt940", {"text": VALID_MT940})
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertEqual(b1, b2)
        _, _, after = client.get("/summary")
        self.assertEqual(after["cycle"], cycle_before)
        self.assertEqual(after["accounts"], before["accounts"])
        self.assertEqual(after["transfers"], before["transfers"])


if __name__ == "__main__":
    unittest.main()
