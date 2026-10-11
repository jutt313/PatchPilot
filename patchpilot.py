#!/usr/bin/env python3
"""PatchPilot: edit code, run tests, roll back owned changes on failure.

Pipeline: task -> snapshot -> apply patches -> run tests -> report
          (on test failure: restore exactly the files PatchPilot changed)
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SNAPSHOT_DIR = ".patchpilot"
REPORT_VERSION = 1


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Snapshot:
    """Byte-exact record of files before PatchPilot touches them."""

    def __init__(self, root: Path):
        self.root = root
        self.entries = {}  # relative path -> {"sha256":..., "content_b64":..., "existed": bool}

    def record(self, rel_path: str, existed: bool, content: bytes):
        import base64
        self.entries[rel_path] = {
            "sha256": sha256_bytes(content),
            "content_b64": base64.b64encode(content).decode("ascii"),
            "existed": existed,
        }

    def save(self) -> Path:
        snap_dir = self.root / SNAPSHOT_DIR
        snap_dir.mkdir(exist_ok=True)
        path = snap_dir / f"snapshot-{int(time.time() * 1000)}.json"
        import base64
        payload = {
            "version": REPORT_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "files": {p: {**e, "content_b64": base64.b64decode(e["content_b64"]).hex() if False else e["content_b64"]}
                      for p, e in self.entries.items()},
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path


class Rollback:
    """Restore exactly the files PatchPilot owns; never touch user edits."""

    def __init__(self, root: Path, snapshot: Snapshot):
        self.root = root
        self.snapshot = snapshot
        self.restored = []
        self.failed = []

    def run(self) -> bool:
        import base64
        ok = True
        for rel, entry in self.snapshot.entries.items():
            target = self.root / rel
            try:
                if entry["existed"]:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(base64.b64decode(entry["content_b64"]))
                else:
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                self.restored.append(rel)
            except OSError as exc:
                ok = False
                self.failed.append({"path": rel, "error": str(exc)})
        return ok


class TestRunner:
    """Runs the configured test command with a timeout; captures result."""

    def __init__(self, command: str, timeout_s: int = 300):
        self.command = command
        self.timeout_s = timeout_s

    def run(self, cwd: Path) -> dict:
        started = time.time()
        try:
            proc = subprocess.run(
                self.command, shell=True, cwd=str(cwd),
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            return {
                "command": self.command,
                "exit_code": proc.returncode,
                "passed": proc.returncode == 0,
                "stdout_tail": proc.stdout[-4000:],
                "stderr_tail": proc.stderr[-4000:],
                "duration_s": round(time.time() - started, 2),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            return {
                "command": self.command, "exit_code": None, "passed": False,
                "stdout_tail": "", "stderr_tail": f"timed out after {self.timeout_s}s",
                "duration_s": self.timeout_s, "timed_out": True,
            }


def apply_change_set(root: Path, change_set: dict, snapshot: Snapshot):
    """Apply a change set: {"changes": [{"path":..., "action":"write"|"delete", "content":...}]}.

    Every touched file is snapshotted (byte-exact) before modification.
    """
    applied = []
    for change in change_set.get("changes", []):
        rel = change["path"]
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError(f"refusing path outside project: {rel}")
        target = root / rel
        existed = target.exists()
        before = target.read_bytes() if existed and target.is_file() else b""
        snapshot.record(rel, existed, before)

        action = change.get("action", "write")
        if action == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.get("content", ""), encoding="utf-8")
        applied.append({"path": rel, "action": action})
    return applied


def run_task(root: Path, change_set: dict, test_command: str, timeout_s: int = 300) -> dict:
    """Full pipeline: snapshot -> apply -> test -> rollback on failure -> report."""
    snapshot = Snapshot(root)
    report = {"version": REPORT_VERSION, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    applied = apply_change_set(root, change_set, snapshot)
    report["applied_changes"] = applied

    result = TestRunner(test_command, timeout_s).run(root)
    report["tests"] = result

    if result["passed"]:
        report["status"] = "success"
        report["rolled_back"] = False
    else:
        rb = Rollback(root, snapshot)
        rollback_ok = rb.run()
        report["status"] = "rolled_back"
        report["rolled_back"] = True
        report["rollback"] = {
            "ok": rollback_ok,
            "restored": rb.restored,
            "failed": rb.failed,
        }
        report["failure_reason"] = (
            f"tests failed (exit {result['exit_code']}); "
            f"{len(rb.restored)} file(s) restored to pre-edit state"
        )
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return report


def load_config(root: Path) -> dict:
    cfg = root / "patchpilot.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8"))
    return {"test_command": "pytest -q", "timeout_s": 300}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="patchpilot", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="apply a change set, test, roll back on failure")
    p_run.add_argument("--changes", required=True, help="path to change-set JSON file (or - for stdin)")
    p_run.add_argument("--test-command", default=None, help="override configured test command")
    p_run.add_argument("--timeout", type=int, default=None)

    p_t = sub.add_parser("test", help="run only the test command")
    p_t.add_argument("--test-command", default=None)

    args = parser.parse_args(argv)
    root = Path.cwd()
    config = load_config(root)

    if args.cmd == "test":
        command = args.test_command or config["test_command"]
        result = TestRunner(command, config["timeout_s"]).run(root)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["passed"] else 1)

    # run
    raw = sys.stdin.read() if args.changes == "-" else Path(args.changes).read_text(encoding="utf-8")
    change_set = json.loads(raw)
    command = args.test_command or config["test_command"]
    timeout = args.timeout or config["timeout_s"]
    report = run_task(root, change_set, command, timeout)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["status"] == "success" else 1)


if __name__ == "__main__":
    main()
