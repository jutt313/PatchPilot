# PatchPilot

PatchPilot edits code, runs tests to verify the change, and safely rolls back exactly the files it changed if tests fail.

## How it works

1. Snapshot: every file PatchPilot is about to touch is recorded byte-exact.
2. Apply: the change set is written to disk.
3. Test: your project's test command runs (configurable, any language) with a timeout.
4. Rollback: if tests fail (or time out), exactly the files PatchPilot changed are restored to their pre-edit bytes. Your own edits are never touched.
5. Report: a JSON report shows what was applied, test output, and rollback details.

## Usage

```bash
# Run a change set against the current project
python3 patchpilot.py run --changes changeset.json

# Or pipe it in
cat changeset.json | python3 patchpilot.py run --changes -

# Run just the tests
python3 patchpilot.py test
```

### Change-set format

```json
{
  "changes": [
    {"path": "src/app.py", "action": "write", "content": "...new file contents..."},
    {"path": "old.py", "action": "delete"}
  ]
}
```

### Configuration

`patchpilot.json` (per project):

```json
{"test_command": "python3 -m pytest -q", "timeout_s": 300}
```

Copy `.env` and adjust `PATCHPILOT_TIMEOUT` / `PATCHPILOT_TEST_COMMAND` if you prefer environment config.

No third-party dependencies; Python 3 standard library only.
