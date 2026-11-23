"""InfoJobs' current empty-results copy is recognised as a genuine zero
(owner's personal bot raised "3 unconfirmed empty scans" for a search that
truly had no offers in the last 7 days)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="no node")


def _has_marker(text: str) -> bool:
    script = (
        "import { hasNoResultsMarker } from './extension/scan_report.js';"
        f"console.log(JSON.stringify(hasNoResultsMarker({json.dumps(text)})));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    ).stdout
    return bool(json.loads(out))


@requires_node
@pytest.mark.parametrize(
    "text",
    [
        "0 ofertas de Ingeniero IA en Barcelona",
        "Ningún resultado a la vista ¡No pasa nada! Comprueba que esté bien escrito",
        "Ningun resultado a la vista",
    ],
)
def test_the_current_infojobs_empty_copy_is_a_genuine_zero(text: str) -> None:
    assert _has_marker(text)


@requires_node
@pytest.mark.parametrize(
    "text", ["10 ofertas de Ingeniero IA en Barcelona", "120 ofertas de Python"]
)
def test_result_counts_ending_in_zero_are_not_mistaken_for_empty(text: str) -> None:
    assert not _has_marker(text)
