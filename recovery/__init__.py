"""Cyber CIV — Recovery subsystem.

A real-world backup / recovery system for banking, crypto, and
fintech accounts, built on the Cyber CIV recovery concepts and
shipped as a deployable subsystem of the Cyber CIV project.

Modules:
  engine  — the core recovery engine (accounts, incidents, pool,
            guard, RecoveryAgent, deterministic fallback, audit log).
  service — the HTTP API server (stdlib only, no dependencies).
  cli     — the command-line entry point (demo / serve / test).
  demo    — an end-to-end demo scenario (bank + crypto + fintech).

Usage:
  python3 -m recovery.cli demo          # run the demo scenario
  python3 -m recovery.cli serve         # start the HTTP API server
  python3 -m recovery.cli test          # run the test suite
"""

from .engine import (
    Account,
    Incident,
    RecoveryPool,
    AuditLog,
    Guard,
    RecoveryAgent,
    RecoveryEngine,
)

__all__ = [
    "Account",
    "Incident",
    "RecoveryPool",
    "AuditLog",
    "Guard",
    "RecoveryAgent",
    "RecoveryEngine",
]
