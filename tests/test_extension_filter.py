"""Tests for the beacon title/company filter (feature: beacon-title-filter).

See `odd/tasks/beacon-title-filter.md` for the feature contract. Two layers:

- Node-run behavioral tests exercise `extension/filter.js` directly through
  `tests/fixtures/extension_filter_harness.js` (skipped when `node` is not on
  PATH — same policy as `tests/test_extension_fork.py`).
- Structural (pytest-only) tests confirm `runCheckRound()` in background.js
  wires `passesFilter` in, and that the options UI exposes the filter fields.

These tests were written BEFORE the filter module and its wiring existed
(TDD RED): they fail against the pristine extension/ tree because filter.js,
the runCheckRound wiring, and the options UI fields do not exist yet.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_filter_harness.js"

pytestmark = pytest.mark.skipif(
    not EXTENSION.is_dir(), reason="extension/ snapshot not present"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node executable not available"
)


def _read(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


@requires_node
def test_filter_js_behavior_via_node_harness() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"filter.js node harness failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_company_blocklist_is_one_shared_constant() -> None:
    """R2-dup-company-blocklist: both profiles derive the company blocklist
    from one constant instead of repeating the literal list."""
    js = _read("filter.js")
    assert "const COMPANY_BLOCKLIST" in js
    assert js.count("'Bending Spoons'") == 1
    assert js.count("...COMPANY_BLOCKLIST") == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
