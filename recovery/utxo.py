"""Cyber CIV — Recovery subsystem, UTXO indexer client.

Queries a public, unauthenticated blockchain indexer for an
address's balance and UTXOs. Stdlib only (urllib.request). No API
key, no account, no login.

Primary: Blockstream Esplora (https://blockstream.info/api).
Fallback: mempool.space (https://mempool.space/api). The base URL
is configurable so failover changes no crypto or parsing code.

Endpoints (Esplora shape, shared by mempool.space):
- GET /address/<address> -> {chain_stats: {funded_txo_sum,
  spent_txo_sum, tx_count, ...}, mempool_stats: {...}}
- GET /address/<address>/utxo -> [{txid, vout, status, value}]

Balance (satoshis) = chain funded sum - chain spent sum.
Network errors, non-200 responses, bad JSON, or a missing field
raise IndexerError — a clean exception, never a crash.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://blockstream.info/api"
FALLBACK_BASE_URL = "https://mempool.space/api"

# Seconds to wait for the indexer before giving up.
REQUEST_TIMEOUT = 15.0


class IndexerError(ValueError):
    """Raised when the indexer cannot be reached or returns
    unusable data."""


@dataclass(frozen=True)
class AddressInfo:
    """Balance and UTXO state for one address."""

    address: str
    balance_sats: int
    tx_count: int
    utxos: list = field(default_factory=list)


def _get_json(url: str):
    """GET a URL and parse the JSON body. Raises IndexerError on
    any failure (network, HTTP status, bad JSON)."""
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "CyberCIV-Recovery/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise IndexerError(
            f"indexer HTTP {exc.code} for {url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise IndexerError(f"indexer unreachable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise IndexerError(f"indexer timed out: {url}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise IndexerError(f"indexer returned invalid JSON: {url}") from exc


def get_address_info(address: str, base_url: str = DEFAULT_BASE_URL):
    """Fetch balance + UTXOs for one address.

    Returns an AddressInfo. Raises IndexerError on any failure.
    """
    if not address or not isinstance(address, str):
        raise IndexerError("address must be a non-empty string")
    base = base_url.rstrip("/")
    summary = _get_json(f"{base}/address/{address}")
    try:
        chain = summary["chain_stats"]
        funded = int(chain["funded_txo_sum"])
        spent = int(chain["spent_txo_sum"])
        tx_count = int(chain["tx_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexerError(
            f"indexer response missing chain_stats for {address}"
        ) from exc
    # The UTXO list is supplementary. Esplora returns 400 for
    # addresses with an excessive number of UTXOs (e.g. the genesis
    # address), so a failure here must not kill the balance lookup.
    # Return the balance + tx count with an empty UTXO list.
    try:
        utxos = _get_json(f"{base}/address/{address}/utxo")
    except IndexerError:
        utxos = []
    if not isinstance(utxos, list):
        raise IndexerError(
            f"indexer UTXO response is not a list for {address}"
        )
    return AddressInfo(
        address=address,
        balance_sats=funded - spent,
        tx_count=tx_count,
        utxos=utxos,
    )
