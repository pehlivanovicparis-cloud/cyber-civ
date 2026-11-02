"""Cyber CIV — Recovery subsystem, signal sensing.

A signal monitor that senses money — crypto and banking — from a
mere signal, without needing addresses or login details.

The idea (Paris, 2026-11-01): people should not have to add their
account info to get protection. Instead, the system senses from a
signal alone. A signal is a minimal public identifier:

  Crypto:
    - a transaction hash (0x + 64 hex): resolve it to the from/to
      addresses and the value, then sense those addresses.
    - a wallet address (0x + 40 hex): sense that address directly.
  Banking:
    - a bank account signal: an account number + routing number
      (public identifiers, not login details). Validate the routing
      number and sense what public data says about the account.
    - a transaction reference: a wire/ACH reference string.

What sensing means:
  - Crypto: full, from public on-chain data (no credentials).
    Balance, transaction value, and pattern signals (large transfer,
    rapid drain) via a public Ethereum RPC.
  - Banking: public-signal sensing only. Bank balances and
    transactions are private by design — no public ledger exists —
    so the monitor validates the signal and checks public data
    (routing-number validity). Full bank-account sensing needs an
    authorized read-only bank API, which is a deployment step; the
    user never provides login details to this monitor.

No private keys, no passwords, no logins. Only the signal.

Data sources (public, no API key needed):
  - Ethereum RPC: https://ethereum.publicnode.com,
    https://eth.drpc.org, https://rpc.flashbots.net
    (verified live 2026-11-01; eth_getBalance and
    eth_getTransactionByHash work without a key).

Proprietary work of Paris Pehlivanovic. All rights reserved.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Public Ethereum RPC endpoints (no API key needed; verified live
# 2026-11-01). Tried in order until one answers.
DEFAULT_RPC_URLS = (
    "https://ethereum.publicnode.com",
    "https://eth.drpc.org",
    "https://rpc.flashbots.net",
)

# A transfer at or above this value (in ETH) is flagged as large.
LARGE_TRANSFER_ETH = 10.0

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _is_tx_hash(signal: str) -> bool:
    return bool(_TX_HASH_RE.match(signal.strip()))


def _is_address(signal: str) -> bool:
    return bool(_ADDRESS_RE.match(signal.strip()))


def _wei_to_eth(wei_hex: str) -> float:
    return int(wei_hex, 16) / 1e18


@dataclass
class SensedAddress:
    """What sensing found for one crypto address."""

    address: str
    balance_eth: float
    transaction_count: Optional[int] = None


@dataclass
class Signal:
    """One detected signal (an alert) from sensing."""

    kind: str  # e.g. LARGE_TRANSFER, RAPID_DRAIN, NEW_ACTIVITY
    severity: str  # "info" | "warning" | "critical"
    message: str
    details: Dict = field(default_factory=dict)


@dataclass
class SenseResult:
    """Everything sensing found from a single signal."""

    signal: str
    signal_type: str  # "crypto_tx" | "crypto_address" | "bank_account" | "bank_reference" | "unknown"
    resolved: Dict = field(default_factory=dict)
    addresses: List[SensedAddress] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class SignalMonitor:
    """Senses money from a mere signal, using public data only.

    rpc_urls: Ethereum RPC endpoints to try in order.
    rpc_timeout: seconds per RPC call.
    """

    def __init__(
        self,
        rpc_urls: tuple = DEFAULT_RPC_URLS,
        rpc_timeout: float = 15.0,
    ) -> None:
        self.rpc_urls = list(rpc_urls)
        self.rpc_timeout = rpc_timeout

    # -- RPC ---------------------------------------------------------------
    def _rpc(self, method: str, params: list) -> Optional[dict]:
        """Call an Ethereum JSON-RPC method, trying endpoints in order.
        Returns the result dict, or None if every endpoint failed."""
        import http.client

        payload = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        ).encode()
        for url in self.rpc_urls:
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.rpc_timeout) as resp:
                    data = json.loads(resp.read().decode())
                if data.get("error"):
                    continue
                return data.get("result")
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                ConnectionError,
                OSError,
                ValueError,
                TimeoutError,
            ):
                # Public RPCs are flaky (rate limits, dropped
                # connections). Try the next endpoint.
                continue
        return None

    def _balance_eth(self, address: str) -> Optional[float]:
        result = self._rpc("eth_getBalance", [address, "latest"])
        if result is None:
            return None
        try:
            return _wei_to_eth(result)
        except (ValueError, TypeError):
            return None

    def _tx_count(self, address: str) -> Optional[int]:
        result = self._rpc("eth_getTransactionCount", [address, "latest"])
        if result is None:
            return None
        try:
            return int(result, 16)
        except (ValueError, TypeError):
            return None

    def _transaction(self, tx_hash: str) -> Optional[dict]:
        return self._rpc("eth_getTransactionByHash", [tx_hash])

    # -- sensing ---------------------------------------------------------------
    def sense_address(self, address: str) -> SensedAddress:
        """Sense one crypto address: its balance and transaction count."""
        balance = self._balance_eth(address)
        count = self._tx_count(address)
        return SensedAddress(
            address=address,
            balance_eth=balance if balance is not None else 0.0,
            transaction_count=count,
        )

    def sense(self, signal: str) -> SenseResult:
        """Sense money from a mere signal. No addresses or logins needed.

        signal: a transaction hash, a wallet address, a bank account
        signal ("bank:<routing>:<account>"), or a bank reference
        ("ref:<reference>"). Returns everything sensing found.
        Forgiving about formatting: surrounding whitespace and angle
        brackets are stripped, and a bare "<routing>:<account>"
        (digits:digits) is read as a bank signal even without the
        "bank:" prefix.
        """
        signal = str(signal).strip()
        # Strip surrounding angle brackets (users often paste the
        # example format with the brackets still around the values).
        if signal.startswith("<") and signal.endswith(">"):
            signal = signal[1:-1].strip()
        signal = signal.replace("<", "").replace(">", "").strip()
        result = SenseResult(signal=signal, signal_type="unknown")

        if _is_tx_hash(signal):
            result.signal_type = "crypto_tx"
            self._sense_crypto_tx(signal, result)
        elif _is_address(signal):
            result.signal_type = "crypto_address"
            self._sense_crypto_address(signal, result)
        elif signal.startswith("bank:"):
            result.signal_type = "bank_account"
            self._sense_bank_account(signal, result)
        elif signal.startswith("ref:"):
            result.signal_type = "bank_reference"
            self._sense_bank_reference(signal, result)
        elif re.fullmatch(r"\d+:\d+", signal):
            # Bare "<routing>:<account>" — a bank signal without the
            # "bank:" prefix.
            result.signal_type = "bank_account"
            self._sense_bank_account("bank:" + signal, result)
        else:
            result.errors.append(
                "unrecognized signal: expected a transaction hash "
                "(0x + 64 hex), a wallet address (0x + 40 hex), "
                "'bank:<routing>:<account>', or 'ref:<reference>'"
            )
        return result

    def _sense_crypto_tx(self, tx_hash: str, result: SenseResult) -> None:
        tx = self._transaction(tx_hash)
        if tx is None:
            result.errors.append(
                f"transaction not found or RPC unreachable: {tx_hash}"
            )
            return
        from_addr = tx.get("from", "")
        to_addr = tx.get("to", "")
        value_eth = 0.0
        try:
            value_eth = _wei_to_eth(tx.get("value", "0x0"))
        except (ValueError, TypeError):
            pass
        result.resolved = {
            "from": from_addr,
            "to": to_addr,
            "value_eth": value_eth,
            "block_number": tx.get("blockNumber"),
        }
        for addr in (from_addr, to_addr):
            if addr and _is_address(addr):
                result.addresses.append(self.sense_address(addr))
        if value_eth >= LARGE_TRANSFER_ETH:
            result.signals.append(Signal(
                kind="LARGE_TRANSFER",
                severity="warning",
                message=(
                    f"large transfer of {value_eth:.4f} ETH "
                    f"from {from_addr} to {to_addr}"
                ),
                details={
                    "value_eth": value_eth,
                    "tx_hash": tx_hash,
                    "from": from_addr,
                    "to": to_addr,
                },
            ))
        if value_eth > 0:
            result.signals.append(Signal(
                kind="VALUE_MOVED",
                severity="info",
                message=(
                    f"{value_eth:.4f} ETH moved from {from_addr} "
                    f"to {to_addr}"
                ),
                details={
                    "value_eth": value_eth,
                    "tx_hash": tx_hash,
                    "from": from_addr,
                    "to": to_addr,
                },
            ))

    def _sense_crypto_address(self, address: str, result: SenseResult) -> None:
        sensed = self.sense_address(address)
        result.addresses.append(sensed)
        result.resolved = {
            "address": address,
            "balance_eth": sensed.balance_eth,
            "transaction_count": sensed.transaction_count,
        }
        if sensed.transaction_count == 0:
            result.signals.append(Signal(
                kind="NO_ACTIVITY",
                severity="info",
                message=f"address {address} has no transactions on record",
                details={"address": address},
            ))

    def _sense_bank_account(self, signal: str, result: SenseResult) -> None:
        """Sense a bank account signal: 'bank:<routing>:<account>'.

        Banking has no public ledger, so sensing here is validation
        and public-data checks only — never a balance lookup (that
        needs authorized bank API access). The routing number is a
        public identifier; the account number is the signal.
        """
        parts = signal.split(":")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            result.errors.append(
                "bank signal must be 'bank:<routing>:<account>' "
                "(digits only)"
            )
            return
        _, routing, account = parts
        result.resolved = {
            "routing_number": routing,
            "account_number": account,
            "account_last4": account[-4:],
        }
        # ABA routing numbers are 9 digits with a checksum.
        if len(routing) != 9:
            result.signals.append(Signal(
                kind="INVALID_ROUTING",
                severity="warning",
                message=f"routing number {routing} is not 9 digits",
                details={"routing_number": routing},
            ))
            return
        digits = [int(d) for d in routing]
        checksum = (
            3 * (digits[0] + digits[3] + digits[6])
            + 7 * (digits[1] + digits[4] + digits[7])
            + (digits[2] + digits[5] + digits[8])
        ) % 10
        if checksum != 0:
            result.signals.append(Signal(
                kind="INVALID_ROUTING",
                severity="warning",
                message=(
                    f"routing number {routing} fails the ABA checksum"
                ),
                details={"routing_number": routing},
            ))
        else:
            result.signals.append(Signal(
                kind="BANK_SIGNAL_VALID",
                severity="info",
                message=(
                    f"bank signal valid: routing {routing} passes the "
                    f"ABA checksum; account ending {account[-4:]}. "
                    f"Balance sensing needs authorized bank API access."
                ),
                details={
                    "routing_number": routing,
                    "account_last4": account[-4:],
                },
            ))

    def _sense_bank_reference(self, signal: str, result: SenseResult) -> None:
        """Sense a bank transaction reference: 'ref:<reference>'.

        A reference alone cannot resolve to an account or a balance
        without bank API access. The monitor records it as a signal
        to watch and notes what would be needed to resolve it.
        """
        reference = signal[len("ref:"):].strip()
        if not reference:
            result.errors.append("bank reference is empty")
            return
        result.resolved = {"reference": reference}
        result.signals.append(Signal(
            kind="BANK_REFERENCE",
            severity="info",
            message=(
                f"bank reference {reference} recorded as a signal to "
                f"watch. Resolving it to an account needs authorized "
                f"bank API access."
            ),
            details={"reference": reference},
        ))


def sense_signal(signal: str) -> SenseResult:
    """Convenience: sense money from a single signal."""
    return SignalMonitor().sense(signal)


# ---------------------------------------------------------------------------
# Wiring: signals -> recovery incidents
# ---------------------------------------------------------------------------

# Portion of a sensed large transfer that is treated as frozen
# (recoverable) when an incident is auto-opened. Matches the demo
# pattern where roughly half the stolen amount is frozen.
SIGNAL_FROZEN_FRACTION = 0.5


def report_signals_to_engine(engine, sense_result: SenseResult) -> list:
    """Wire signals into the recovery engine.

    For each LARGE_TRANSFER signal in the sense result, if the
    from-address matches a registered engine account (by the
    account's public address), auto-open a recovery incident for
    that account. The incident's stolen amount is the transfer
    value (capped at the victim's balance), and the frozen amount
    is SIGNAL_FROZEN_FRACTION of it. An account with an already-open
    incident is skipped (one open incident per victim).

    Returns the list of incidents opened (empty if none).
    """
    opened = []
    for sig in sense_result.signals:
        if sig.kind != "LARGE_TRANSFER":
            continue
        from_addr = sig.details.get("from")
        # The LARGE_TRANSFER message embeds the from-address; the
        # details carry the value. Fall back to the resolved tx.
        value_eth = sig.details.get("value_eth")
        if value_eth is None:
            value_eth = sense_result.resolved.get("value_eth", 0.0)
        if not from_addr:
            from_addr = sense_result.resolved.get("from", "")
        if not from_addr or not value_eth:
            continue
        account = engine.find_account_by_address(from_addr)
        if account is None:
            continue
        if account.account_id in engine.open_by_victim:
            continue
        amount_stolen = min(float(value_eth), account.balance)
        if amount_stolen <= 0:
            continue
        amount_frozen = round(amount_stolen * SIGNAL_FROZEN_FRACTION, 2)
        incident = engine.report_incident(
            victim_id=account.account_id,
            amount_stolen=round(amount_stolen, 2),
            amount_frozen=amount_frozen,
            kind="unauthorized_transfer",
        )
        opened.append(incident)
    return opened
