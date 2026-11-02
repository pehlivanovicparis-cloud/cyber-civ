"""Cyber CIV — Recovery subsystem, command-line entry point.

Commands:
  python3 -m recovery.cli demo
      Run the end-to-end demo scenario (bank + crypto + fintech).

  python3 -m recovery.cli serve [--host HOST] [--port PORT]
      Start the HTTP API server. Defaults to 127.0.0.1:8000
      (localhost only). See recovery/README.md before binding to
      0.0.0.0.

  python3 -m recovery.cli test
      Run the recovery test suite (engine + service + signal).

  python3 -m recovery.cli sense <signal>
      Sense money from a mere signal — no addresses or logins needed.
      signal: a transaction hash (0x + 64 hex), a wallet address
      (0x + 40 hex), 'bank:<routing>:<account>', or 'ref:<reference>'.

Run from the Cyber CIV repo root.
"""

from __future__ import annotations

import argparse
import sys
import unittest


def _cmd_demo(_args) -> int:
    from .demo import main
    main()
    return 0


def _cmd_serve(args) -> int:
    from .service import run_server
    run_server(host=args.host, port=args.port)
    return 0


def _cmd_test(_args) -> int:
    loader = unittest.TestLoader()
    # top_level_dir="." so the tests import as recovery.test_* (a
    # package), which keeps their relative imports working.
    suite = loader.discover("recovery", pattern="test_*.py",
                            top_level_dir=".")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def _cmd_sense(args) -> int:
    from .signal import SignalMonitor

    result = SignalMonitor().sense(args.signal)
    print(f"signal: {result.signal}")
    print(f"type: {result.signal_type}")
    if result.resolved:
        print(f"resolved: {result.resolved}")
    for addr in result.addresses:
        print(f"address {addr.address}: "
              f"{addr.balance_eth:.6f} ETH "
              f"(txs: {addr.transaction_count})")
    for sig in result.signals:
        print(f"[{sig.severity}] {sig.kind}: {sig.message}")
    for err in result.errors:
        print(f"error: {err}")
    return 0 if not result.errors else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="recovery",
        description="Cyber CIV Recovery subsystem.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="run the demo scenario")

    serve_p = sub.add_parser("serve", help="start the HTTP API server")
    serve_p.add_argument("--host", default="127.0.0.1",
                         help="bind address (default 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=8000,
                         help="port (default 8000)")

    sub.add_parser("test", help="run the recovery test suite")

    sense_p = sub.add_parser(
        "sense", help="sense money from a mere signal")
    sense_p.add_argument(
        "signal",
        help="a tx hash, wallet address, 'bank:<routing>:<account>', "
             "or 'ref:<reference>'")

    args = parser.parse_args(argv)
    if args.command == "demo":
        return _cmd_demo(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "test":
        return _cmd_test(args)
    if args.command == "sense":
        return _cmd_sense(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
