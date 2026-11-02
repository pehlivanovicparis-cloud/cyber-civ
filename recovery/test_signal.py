"""Unit tests for the Cyber CIV Recovery signal monitor.

The SignalMonitor reads public on-chain data (crypto) and validates
bank signals. Tests use a mocked RPC layer so they do not depend on
live network access.

Run:  python3 -m recovery.cli test
       python3 -m unittest discover -s recovery -p "test_*.py" -t .
"""

import unittest

from recovery.signal import SignalMonitor, _is_address, _is_tx_hash


class _FakeMonitor(SignalMonitor):
    """SignalMonitor with a canned RPC layer (no network)."""

    def __init__(self, rpc_results):
        super().__init__(rpc_urls=())
        self._rpc_results = rpc_results
        self._rpc_calls = []

    def _rpc(self, method, params):
        self._rpc_calls.append((method, params))
        return self._rpc_results.get((method, tuple(params)))


def _fake_tx():
    return {
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x2222222222222222222222222222222222222222",
        "value": hex(int(12.5 * 1e18)),
        "blockNumber": "0x100",
    }


class TestSignalDetection(unittest.TestCase):
    def test_tx_hash_is_detected(self):
        self.assertTrue(_is_tx_hash(
            "0x" + "ab" * 32))
        self.assertFalse(_is_tx_hash("0x1234"))
        self.assertFalse(_is_tx_hash("not-a-hash"))

    def test_address_is_detected(self):
        self.assertTrue(_is_address(
            "0x" + "ab" * 20))
        self.assertFalse(_is_address("0x" + "ab" * 32))
        self.assertFalse(_is_address("not-an-address"))


class TestCryptoSensing(unittest.TestCase):
    def test_tx_hash_resolves_and_senses_addresses(self):
        m = _FakeMonitor({
            ("eth_getTransactionByHash", (
                "0x" + "ab" * 32,)): _fake_tx(),
            ("eth_getBalance", (
                "0x1111111111111111111111111111111111111111",
                "latest")): hex(int(3.0 * 1e18)),
            ("eth_getTransactionCount", (
                "0x1111111111111111111111111111111111111111",
                "latest")): hex(42),
            ("eth_getBalance", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(int(0.5 * 1e18)),
            ("eth_getTransactionCount", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(7),
        })
        r = m.sense("0x" + "ab" * 32)
        self.assertEqual(r.signal_type, "crypto_tx")
        self.assertEqual(r.resolved["from"],
                         "0x1111111111111111111111111111111111111111")
        self.assertEqual(r.resolved["to"],
                         "0x2222222222222222222222222222222222222222")
        self.assertAlmostEqual(r.resolved["value_eth"], 12.5)
        self.assertEqual(len(r.addresses), 2)
        self.assertEqual(r.addresses[0].balance_eth, 3.0)
        self.assertEqual(r.addresses[0].transaction_count, 42)
        # 12.5 ETH is a large transfer (>= 10.0).
        kinds = [s.kind for s in r.signals]
        self.assertIn("LARGE_TRANSFER", kinds)
        self.assertIn("VALUE_MOVED", kinds)
        self.assertEqual(r.errors, [])

    def test_small_transfer_is_not_large(self):
        tx = _fake_tx()
        tx["value"] = hex(int(0.5 * 1e18))
        m = _FakeMonitor({
            ("eth_getTransactionByHash", ("0x" + "ab" * 32,)): tx,
            ("eth_getBalance", (
                "0x1111111111111111111111111111111111111111",
                "latest")): hex(int(3.0 * 1e18)),
            ("eth_getTransactionCount", (
                "0x1111111111111111111111111111111111111111",
                "latest")): hex(42),
            ("eth_getBalance", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(int(0.5 * 1e18)),
            ("eth_getTransactionCount", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(7),
        })
        r = m.sense("0x" + "ab" * 32)
        kinds = [s.kind for s in r.signals]
        self.assertNotIn("LARGE_TRANSFER", kinds)
        self.assertIn("VALUE_MOVED", kinds)

    def test_unknown_tx_hash_is_an_error(self):
        m = _FakeMonitor({})
        r = m.sense("0x" + "ab" * 32)
        self.assertEqual(r.signal_type, "crypto_tx")
        self.assertTrue(r.errors)
        self.assertEqual(r.addresses, [])

    def test_address_with_no_activity(self):
        m = _FakeMonitor({
            ("eth_getBalance", (
                "0x3Ae6AB2f00674eBAa004aAD36A51f4367C35E2E6",
                "latest")): "0x0",
            ("eth_getTransactionCount", (
                "0x3Ae6AB2f00674eBAa004aAD36A51f4367C35E2E6",
                "latest")): "0x0",
        })
        r = m.sense("0x3Ae6AB2f00674eBAa004aAD36A51f4367C35E2E6")
        self.assertEqual(r.signal_type, "crypto_address")
        self.assertEqual(r.resolved["balance_eth"], 0.0)
        self.assertEqual(r.resolved["transaction_count"], 0)
        kinds = [s.kind for s in r.signals]
        self.assertIn("NO_ACTIVITY", kinds)


class TestBankSensing(unittest.TestCase):
    def test_valid_routing_number_passes_checksum(self):
        # 021000021 is JPMorgan Chase's routing number; it passes
        # the ABA checksum.
        m = SignalMonitor(rpc_urls=())
        r = m.sense("bank:021000021:123456789")
        self.assertEqual(r.signal_type, "bank_account")
        self.assertEqual(r.resolved["routing_number"], "021000021")
        self.assertEqual(r.resolved["account_last4"], "6789")
        kinds = [s.kind for s in r.signals]
        self.assertIn("BANK_SIGNAL_VALID", kinds)
        self.assertEqual(r.errors, [])

    def test_bad_routing_number_fails_checksum(self):
        m = SignalMonitor(rpc_urls=())
        r = m.sense("bank:021000022:123456789")
        kinds = [s.kind for s in r.signals]
        self.assertIn("INVALID_ROUTING", kinds)

    def test_malformed_bank_signal_is_an_error(self):
        m = SignalMonitor(rpc_urls=())
        r = m.sense("bank:abc:def")
        self.assertTrue(r.errors)

    def test_bank_reference_is_recorded(self):
        m = SignalMonitor(rpc_urls=())
        r = m.sense("ref:WIRE-2026-1101-8842")
        self.assertEqual(r.signal_type, "bank_reference")
        self.assertEqual(r.resolved["reference"], "WIRE-2026-1101-8842")
        kinds = [s.kind for s in r.signals]
        self.assertIn("BANK_REFERENCE", kinds)

    def test_unrecognized_signal_is_an_error(self):
        m = SignalMonitor(rpc_urls=())
        r = m.sense("hello world")
        self.assertEqual(r.signal_type, "unknown")
        self.assertTrue(r.errors)

    def test_bare_routing_account_is_a_bank_signal(self):
        # A bare "<routing>:<account>" (no "bank:" prefix) is read
        # as a bank signal.
        m = SignalMonitor(rpc_urls=())
        r = m.sense("021000021:123456789")
        self.assertEqual(r.signal_type, "bank_account")
        self.assertEqual(r.resolved["routing_number"], "021000021")
        kinds = [s.kind for s in r.signals]
        self.assertIn("BANK_SIGNAL_VALID", kinds)

    def test_angle_brackets_are_stripped(self):
        # Users paste the example format with brackets still around
        # the values, e.g. "<021000021>:<123456789>".
        m = SignalMonitor(rpc_urls=())
        r = m.sense("<021000021>:<123456789>")
        self.assertEqual(r.signal_type, "bank_account")
        self.assertEqual(r.resolved["routing_number"], "021000021")
        kinds = [s.kind for s in r.signals]
        self.assertIn("BANK_SIGNAL_VALID", kinds)

    def test_bank_prefix_with_brackets_is_stripped(self):
        m = SignalMonitor(rpc_urls=())
        r = m.sense("bank:<021000021>:<123456789>")
        self.assertEqual(r.signal_type, "bank_account")
        kinds = [s.kind for s in r.signals]
        self.assertIn("BANK_SIGNAL_VALID", kinds)


class TestSignalToEngineWiring(unittest.TestCase):
    """A LARGE_TRANSFER signal from a registered account auto-opens
    a recovery incident for that account."""

    def _engine_with_address(self, address, balance):
        from recovery.engine import Account, RecoveryEngine
        engine = RecoveryEngine()
        engine.add_account(Account(
            "CRYPTO-1", "crypto", "Test User", balance, "ETH",
            address=address))
        return engine

    def test_large_transfer_opens_incident(self):
        from recovery.signal import report_signals_to_engine
        from_addr = "0x1111111111111111111111111111111111111111"
        tx = _fake_tx()  # 12.5 ETH from from_addr
        m = _FakeMonitor({
            ("eth_getTransactionByHash", ("0x" + "ab" * 32,)): tx,
            ("eth_getBalance", (from_addr, "latest")):
                hex(int(20.0 * 1e18)),
            ("eth_getTransactionCount", (from_addr, "latest")): hex(42),
            ("eth_getBalance", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(int(0.5 * 1e18)),
            ("eth_getTransactionCount", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(7),
        })
        engine = self._engine_with_address(from_addr, 20.0)
        r = m.sense("0x" + "ab" * 32)
        opened = report_signals_to_engine(engine, r)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].victim_id, "CRYPTO-1")
        self.assertEqual(opened[0].amount_stolen, 12.5)
        self.assertEqual(opened[0].amount_frozen, 6.25)  # 50% frozen
        self.assertEqual(opened[0].kind, "unauthorized_transfer")
        # The victim's balance was drained by the stolen amount.
        self.assertEqual(engine.accounts["CRYPTO-1"].balance, 7.5)
        # The frozen amount is in the pool.
        self.assertEqual(engine.pool.balance, 6.25)

    def test_unregistered_address_opens_nothing(self):
        from recovery.signal import report_signals_to_engine
        from_addr = "0x1111111111111111111111111111111111111111"
        tx = _fake_tx()
        m = _FakeMonitor({
            ("eth_getTransactionByHash", ("0x" + "ab" * 32,)): tx,
            ("eth_getBalance", (from_addr, "latest")):
                hex(int(20.0 * 1e18)),
            ("eth_getTransactionCount", (from_addr, "latest")): hex(42),
            ("eth_getBalance", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(int(0.5 * 1e18)),
            ("eth_getTransactionCount", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(7),
        })
        # Engine has no account with that address.
        from recovery.engine import RecoveryEngine
        engine = RecoveryEngine()
        r = m.sense("0x" + "ab" * 32)
        opened = report_signals_to_engine(engine, r)
        self.assertEqual(opened, [])
        self.assertEqual(engine.pool.balance, 0.0)

    def test_small_transfer_opens_nothing(self):
        from recovery.signal import report_signals_to_engine
        from_addr = "0x1111111111111111111111111111111111111111"
        tx = _fake_tx()
        tx["value"] = hex(int(0.5 * 1e18))  # small, no LARGE_TRANSFER
        m = _FakeMonitor({
            ("eth_getTransactionByHash", ("0x" + "ab" * 32,)): tx,
            ("eth_getBalance", (from_addr, "latest")):
                hex(int(20.0 * 1e18)),
            ("eth_getTransactionCount", (from_addr, "latest")): hex(42),
            ("eth_getBalance", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(int(0.5 * 1e18)),
            ("eth_getTransactionCount", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(7),
        })
        engine = self._engine_with_address(from_addr, 20.0)
        r = m.sense("0x" + "ab" * 32)
        opened = report_signals_to_engine(engine, r)
        self.assertEqual(opened, [])

    def test_already_open_incident_is_not_duplicated(self):
        from recovery.signal import report_signals_to_engine
        from_addr = "0x1111111111111111111111111111111111111111"
        tx = _fake_tx()
        m = _FakeMonitor({
            ("eth_getTransactionByHash", ("0x" + "ab" * 32,)): tx,
            ("eth_getBalance", (from_addr, "latest")):
                hex(int(20.0 * 1e18)),
            ("eth_getTransactionCount", (from_addr, "latest")): hex(42),
            ("eth_getBalance", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(int(0.5 * 1e18)),
            ("eth_getTransactionCount", (
                "0x2222222222222222222222222222222222222222",
                "latest")): hex(7),
        })
        engine = self._engine_with_address(from_addr, 20.0)
        r = m.sense("0x" + "ab" * 32)
        first = report_signals_to_engine(engine, r)
        second = report_signals_to_engine(engine, r)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
