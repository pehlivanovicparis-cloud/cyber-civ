#!/usr/bin/env python3
"""Tests for adapter_check.py — the fine-tuned adapter readiness gate.

Stdlib unittest only (the codebase is stdlib-only; pytest is not assumed).
Run:  python3 -m unittest test_adapter_check -v
  or: python3 test_adapter_check.py

All artifacts are dummy files in temp dirs — the real ~361 MB weights file
is never created, and nothing here touches the real adapter directory
except one read-only check that it is (today) correctly reported NOT ready.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_check as ac

SMALL = 16  # min_weights_bytes override so dummy files stay tiny


def make_adapter_dir(td: Path, *, weights_bytes: int | None = 64,
                     peft_type: str = "LORA", corrupt_config: bool = False,
                     skip: tuple[str, ...] = ()) -> Path:
    d = td / "adapter"
    d.mkdir()
    if "adapter_config.json" not in skip:
        if corrupt_config:
            (d / "adapter_config.json").write_text("{not json")
        else:
            (d / "adapter_config.json").write_text(json.dumps(
                {"peft_type": peft_type, "r": 64, "lora_alpha": 16}))
    for name in ("tokenizer_config.json", "tokenizer.json"):
        if name not in skip:
            (d / name).write_text("{}")
    if weights_bytes is not None and ac.WEIGHTS_FILE not in skip:
        (d / ac.WEIGHTS_FILE).write_bytes(b"\0" * weights_bytes)
    return d


class CheckAdapterTests(unittest.TestCase):
    def test_ready_with_dummy_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td))
            st = ac.check_adapter(d, min_weights_bytes=SMALL)
            self.assertEqual(st.state, "READY")
            self.assertTrue(st.ready)
            self.assertEqual(st.missing, [])
            self.assertEqual(st.problems, [])

    def test_missing_weights_is_incomplete_and_names_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td), weights_bytes=None)
            st = ac.check_adapter(d, min_weights_bytes=SMALL)
            self.assertEqual(st.state, "INCOMPLETE")
            self.assertIn(ac.WEIGHTS_FILE, st.missing)
            self.assertTrue(any("Kaggle" in p for p in st.problems))

    def test_undersized_weights_flagged_as_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td), weights_bytes=4)
            st = ac.check_adapter(d, min_weights_bytes=SMALL)
            self.assertEqual(st.state, "INCOMPLETE")
            self.assertTrue(any("placeholder or truncated" in p
                                for p in st.problems))

    def test_missing_directory_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            st = ac.check_adapter(Path(td) / "nope", min_weights_bytes=SMALL)
            self.assertEqual(st.state, "MISSING")
            self.assertFalse(st.ready)

    def test_corrupt_config_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td), corrupt_config=True)
            st = ac.check_adapter(d, min_weights_bytes=SMALL)
            self.assertEqual(st.state, "INCOMPLETE")
            self.assertTrue(any("does not parse" in p for p in st.problems))

    def test_non_lora_config_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td), peft_type="IA3")
            st = ac.check_adapter(d, min_weights_bytes=SMALL)
            self.assertEqual(st.state, "INCOMPLETE")

    def test_require_raises_loudly_when_not_ready(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td), weights_bytes=None)
            with self.assertRaises(ac.AdapterNotReadyError) as ctx:
                ac.require_adapter(d, min_weights_bytes=SMALL)
            self.assertIn("NOT READY", str(ctx.exception))

    def test_require_returns_status_when_ready(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td))
            st = ac.require_adapter(d, min_weights_bytes=SMALL)
            self.assertTrue(st.ready)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            d = make_adapter_dir(Path(td), weights_bytes=None)
            self.assertEqual(ac.main(["--dir", str(d)]), 0)       # informational
            self.assertEqual(ac.main(["--dir", str(d), "--require"]), 2)
            # A dummy dir with a tiny weights file must also fail the CLI's
            # real size floor — that is the anti-placeholder behaviour.
            rd = Path(td) / "r"
            rd.mkdir()
            (rd / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA"}))
            (rd / "tokenizer_config.json").write_text("{}")
            (rd / "tokenizer.json").write_text("{}")
            (rd / ac.WEIGHTS_FILE).write_bytes(b"\0" * 64)
            self.assertEqual(ac.main(["--dir", str(rd), "--require"]), 2)

    def test_real_adapter_dir_is_not_ready_today(self):
        # Ground truth for this repo until Paris downloads the weights:
        # 5 of 6 files present, adapter_model.safetensors absent.
        if not ac.DEFAULT_ADAPTER_DIR.is_dir():
            self.skipTest("real adapter dir not present on this machine")
        st = ac.check_adapter()
        self.assertFalse(st.ready)
        self.assertIn(ac.WEIGHTS_FILE, st.missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
