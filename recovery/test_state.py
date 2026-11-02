"""Unit tests for the Cyber CIV Recovery state persistence.

Tests save/load round-trips and a restart simulation: build an
engine, run it, save it, load it into a fresh engine, and verify
the loaded engine resumes exactly where the original left off —
same balances, same incidents, same audit chain, and it can keep
running cycles.

Run:  python3 -m recovery.cli test
"""

import json
import tempfile
import unittest
from pathlib import Path

from recovery.engine import Account, RecoveryEngine
from recovery.state import load_state, save_state


def _build_engine() -> RecoveryEngine:
    """An engine with accounts, an incident, a recovery, and a
    transfer — enough state to catch a lossy round-trip."""
    engine = RecoveryEngine()
    engine.add_account(Account(
        "BANK-1", "bank", "Alice", 10000.0, "USD"))
    engine.add_account(Account(
        "CRYPTO-1", "crypto", "Alice", 25.0, "ETH",
        address="0x1111111111111111111111111111111111111111"))
    engine.report_incident("BANK-1", 1200.0, 600.0, kind="phishing")
    engine.report_incident("CRYPTO-1", 4.0, 2.5, kind="api_breach")
    engine.run_cycle()  # agent recovers BANK-1 (600)
    engine.run_cycle(force_null_target=True)  # guard rejects; fallback recovers CRYPTO-1 (2.5)
    engine.transfer("BANK-1", "0xSafeWallet7f3a9c2e1b", 500.0)
    return engine


class TestPersistence(unittest.TestCase):
    def test_round_trip_preserves_state(self):
        engine = _build_engine()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(engine, path)
            loaded = load_state(path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        # Balances.
        self.assertEqual(loaded.accounts["BANK-1"].balance, 8900.0)
        self.assertEqual(loaded.accounts["CRYPTO-1"].balance, 23.5)
        # Address preserved.
        self.assertEqual(
            loaded.accounts["CRYPTO-1"].address,
            "0x1111111111111111111111111111111111111111")
        # Pool conserved and empty after full recovery.
        self.assertEqual(loaded.pool.balance, 0.0)
        # Wallet totals.
        self.assertEqual(loaded.wallet_total_deposited, 602.5)
        self.assertEqual(loaded.wallet_total_released, 602.5)
        # Totals.
        summary = loaded.summary()
        self.assertEqual(summary["total_recovered"], 602.5)
        self.assertEqual(summary["total_frozen"], 602.5)
        self.assertEqual(summary["total_transferred"], 500.0)
        # Cycle and counters.
        self.assertEqual(loaded.cycle, 2)
        self.assertEqual(loaded._next_incident_id, 3)
        self.assertEqual(loaded._next_transfer_id, 2)
        # Audit chain still valid after the round-trip.
        self.assertTrue(loaded.log.verify())
        self.assertEqual(len(loaded.log.entries),
                         len(engine.log.entries))

    def test_loaded_engine_keeps_running(self):
        """A restart simulation: after loading, the engine can take
        a new incident and recover it in the next cycle."""
        engine = _build_engine()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(engine, path)
            loaded = load_state(path)
        assert loaded is not None
        loaded.report_incident("BANK-1", 500.0, 300.0, kind="phishing")
        result = loaded.run_cycle()
        self.assertEqual(result["agent_recovered"], 3)
        self.assertEqual(loaded.pool.balance, 0.0)
        self.assertTrue(loaded.log.verify())
        self.assertEqual(loaded.summary()["total_recovered"], 902.5)

    def test_load_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_state(Path(tmp) / "nope.json"))

    def test_load_corrupt_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not valid json")
            with self.assertRaises(ValueError):
                load_state(path)

    def test_load_wrong_version_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"version": 99}))
            with self.assertRaises(ValueError):
                load_state(path)

    def test_save_is_atomic_no_temp_left_behind(self):
        engine = _build_engine()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(engine, path)
            leftovers = list(Path(tmp).glob("state.json.tmp.*"))
            self.assertEqual(leftovers, [])
            self.assertTrue(path.exists())

    def test_guard_cooldown_survives_restart(self):
        """The guard's cooldown is part of the state: after a
        restart, a RECOVER attempted in the very next cycle is still
        on cooldown."""
        engine = RecoveryEngine()
        engine.add_account(Account("BANK-1", "bank", "Alice", 1000.0, "USD"))
        engine.add_account(Account("BANK-2", "bank", "Bob", 1000.0, "USD"))
        engine.report_incident("BANK-1", 100.0, 60.0)
        engine.report_incident("BANK-2", 100.0, 60.0)
        # Cycle 1: agent recovers BANK-1 (60). Guard marks the attempt.
        engine.run_cycle()
        self.assertEqual(engine.guard._last_recover_cycle, 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(engine, path)
            loaded = load_state(path)
        assert loaded is not None
        # Cycle 2 after restart: the agent would target BANK-2, but
        # the guard is still on cooldown from cycle 1, so the
        # RECOVER is rejected and the fallback covers it instead.
        result = loaded.run_cycle()
        self.assertEqual(result["guard"], "RECOVER on cooldown (1 cycle(s) left)")
        self.assertEqual(result["fallback_recovered"], 2)


if __name__ == "__main__":
    unittest.main()
