"""Tests for the UTXO indexer client (Phase 2).

Covers: URL construction (Esplora API shape), response parsing
(balance = funded - spent, tx count, UTXO list), error handling
(network failure, bad JSON, non-200), and the configurable base URL
(Blockstream primary, mempool.space fallback).

The HTTP layer is mocked with unittest.mock; no live network call
happens in these tests. A live smoke test is run separately.

Run:  python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import json
import unittest
from unittest.mock import patch

from recovery.utxo import (
    AddressInfo,
    IndexerError,
    get_address_info,
)

# A real Esplora /address payload shape (blockstream.info/api).
ESPLORA_ADDRESS_PAYLOAD = {
    "address": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
    "chain_stats": {
        "funded_txo_count": 3,
        "funded_txo_sum": 150000,
        "spent_txo_count": 1,
        "spent_txo_sum": 50000,
        "tx_count": 3,
    },
    "mempool_stats": {
        "funded_txo_count": 0,
        "funded_txo_sum": 0,
        "spent_txo_count": 0,
        "spent_txo_sum": 0,
        "tx_count": 0,
    },
}

# A real Esplora /address/<addr>/utxo payload shape.
ESPLORA_UTXO_PAYLOAD = [
    {
        "txid": "a" * 64,
        "vout": 0,
        "status": {"confirmed": True, "block_height": 800000,
                   "block_hash": "b" * 64, "block_time": 1700000000},
        "value": 100000,
    },
    {
        "txid": "c" * 64,
        "vout": 1,
        "status": {"confirmed": True, "block_height": 800001,
                   "block_hash": "d" * 64, "block_time": 1700000100},
        "value": 50000,
    },
]

ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


class _FakeResponse:
    """Minimal stand-in for a urllib response object."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen_factory(calls):
    """Build a urlopen mock that serves the Esplora payloads and
    records the URLs it was asked for. Accepts either a URL string
    or a urllib Request object."""
    def _fake_urlopen(url, timeout=None):
        if hasattr(url, "full_url"):
            url = url.full_url
        calls.append(url)
        if url.endswith("/utxo"):
            body = json.dumps(ESPLORA_UTXO_PAYLOAD).encode()
        else:
            body = json.dumps(ESPLORA_ADDRESS_PAYLOAD).encode()
        return _FakeResponse(body)
    return _fake_urlopen


class TestIndexerParsing(unittest.TestCase):
    def test_balance_and_tx_count(self):
        calls = []
        with patch(
            "recovery.utxo.urllib.request.urlopen",
            side_effect=_fake_urlopen_factory(calls),
        ):
            info = get_address_info(ADDRESS)
        self.assertIsInstance(info, AddressInfo)
        self.assertEqual(info.address, ADDRESS)
        # Balance = funded - spent = 150000 - 50000 = 100000 sats.
        self.assertEqual(info.balance_sats, 100000)
        self.assertEqual(info.tx_count, 3)
        self.assertEqual(len(info.utxos), 2)
        self.assertEqual(info.utxos[0]["value"], 100000)
        self.assertEqual(info.utxos[1]["value"], 50000)

    def test_urls_use_esplora_shape(self):
        calls = []
        with patch(
            "recovery.utxo.urllib.request.urlopen",
            side_effect=_fake_urlopen_factory(calls),
        ):
            get_address_info(ADDRESS)
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            calls[0].startswith("https://blockstream.info/api/address/")
        )
        self.assertTrue(calls[0].endswith(ADDRESS))
        self.assertTrue(calls[1].endswith(ADDRESS + "/utxo"))

    def test_configurable_base_url(self):
        # A different base URL (mempool.space fallback) changes only
        # the host, never the path shape or the parsing.
        calls = []
        with patch(
            "recovery.utxo.urllib.request.urlopen",
            side_effect=_fake_urlopen_factory(calls),
        ):
            get_address_info(
                ADDRESS, base_url="https://mempool.space/api"
            )
        self.assertTrue(
            calls[0].startswith("https://mempool.space/api/address/")
        )
        self.assertTrue(calls[1].endswith(ADDRESS + "/utxo"))


class TestIndexerErrors(unittest.TestCase):
    def test_network_failure(self):
        import urllib.error

        with patch(
            "recovery.utxo.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(IndexerError):
                get_address_info(ADDRESS)

    def test_http_error(self):
        import urllib.error

        with patch(
            "recovery.utxo.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://blockstream.info/api/address/x",
                code=404,
                msg="Not Found",
                hdrs=None,
                fp=None,
            ),
        ):
            with self.assertRaises(IndexerError):
                get_address_info(ADDRESS)

    def test_bad_json(self):
        with patch(
            "recovery.utxo.urllib.request.urlopen",
            return_value=_FakeResponse(b"not json at all"),
        ):
            with self.assertRaises(IndexerError):
                get_address_info(ADDRESS)

    def test_missing_fields(self):
        with patch(
            "recovery.utxo.urllib.request.urlopen",
            return_value=_FakeResponse(b'{"address": "x"}'),
        ):
            with self.assertRaises(IndexerError):
                get_address_info(ADDRESS)

    def test_utxo_failure_is_nonfatal(self):
        # Esplora returns 400 on /utxo for addresses with an
        # excessive number of UTXOs (e.g. the genesis address).
        # The balance + tx count must still come back; only the
        # UTXO list is empty.
        import urllib.error

        def _flaky(url, timeout=None):
            target = url.full_url if hasattr(url, "full_url") else url
            if target.endswith("/utxo"):
                raise urllib.error.HTTPError(
                    url=target, code=400, msg="Bad Request",
                    hdrs=None, fp=None,
                )
            return _FakeResponse(
                json.dumps(ESPLORA_ADDRESS_PAYLOAD).encode()
            )

        with patch(
            "recovery.utxo.urllib.request.urlopen",
            side_effect=_flaky,
        ):
            info = get_address_info(ADDRESS)
        self.assertEqual(info.balance_sats, 100000)
        self.assertEqual(info.tx_count, 3)
        self.assertEqual(info.utxos, [])


if __name__ == "__main__":
    unittest.main()
