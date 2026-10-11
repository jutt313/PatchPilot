"""End-to-end tests for the PatchPilot engine (stdlib unittest, no pytest needed).

Each case runs patchpilot.run_task against a throwaway fixture project in a
temp directory: snapshot -> apply change set -> run tests -> rollback on failure.
"""
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import patchpilot as pp


def make_project(d):
    (d / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (d / "test_app.py").write_text(
        "import unittest\nfrom app import add\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )


class TestEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        make_project(self.proj)
        self.orig = (self.proj / "app.py").read_bytes()

    def tearDown(self):
        self._tmp.cleanup()

    def test_good_change_is_kept(self):
        cs = {"changes": [{"path": "app.py", "action": "write",
                           "content": "def add(a, b):\n    return a + b  # patched\n"}]}
        rep = pp.run_task(self.proj, cs, "python3 -m unittest discover -v", 60)
        self.assertEqual(rep["status"], "success")
        self.assertFalse(rep["rolled_back"])
        self.assertIn("patched", (self.proj / "app.py").read_text())

    def test_failing_change_rolls_back_byte_exact(self):
        cs = {"changes": [{"path": "app.py", "action": "write",
                           "content": "def add(a, b):\n    return a - b\n"}]}
        rep = pp.run_task(self.proj, cs, "python3 -m unittest discover -v", 60)
        self.assertEqual(rep["status"], "rolled_back")
        self.assertTrue(rep["rollback"]["ok"])
        self.assertEqual((self.proj / "app.py").read_bytes(), self.orig)

    def test_new_file_removed_on_rollback(self):
        cs = {"changes": [{"path": "extra.py", "action": "write", "content": "x = 1\n"}]}
        rep = pp.run_task(self.proj, cs, "false", 60)  # command that always fails
        self.assertFalse((self.proj / "extra.py").exists())
        self.assertIn("extra.py", rep["rollback"]["restored"])

    def test_timeout_triggers_rollback(self):
        cs = {"changes": [{"path": "app.py", "action": "write", "content": "# x\n"}]}
        rep = pp.run_task(self.proj, cs, "sleep 5", 1)
        self.assertTrue(rep["tests"]["timed_out"])
        self.assertTrue(rep["rolled_back"])
        self.assertEqual((self.proj / "app.py").read_bytes(), self.orig)


if __name__ == "__main__":
    unittest.main()
