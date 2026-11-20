"""Tests for the rejudge-backlog Python title filter port
(``cva/title_filter.py``, feature: rejudge-backlog).

Two layers, mirroring ``tests/test_extension_filter.py``'s policy:

- A parity test runs a representative title/company bank through BOTH
  ``extension/filter.js``'s ``passesFilter``/``DEFAULT_TITLE_FILTER`` (via a
  node harness) and the Python port over the SAME inputs, asserting
  identical pass/fail — skipped when ``node`` is not on PATH.
- Plain pytest-only behavioral tests exercise the Python port directly
  (normalize/matches_term/passes_filter), needing no node at all.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cva.title_filter import (
    BACKEND_TITLE_FILTER,
    DEFAULT_COMPANY_BLOCKLIST,
    DEFAULT_TITLE_FILTER,
    TitleFilter,
    matches_term,
    normalize,
    passes_filter,
    with_company_blocklist,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = (
    Path(__file__).resolve().parent / "fixtures" / "title_filter_parity_harness.js"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node executable not available"
)

# Representative titles: the same realistic bank from
# tests/fixtures/extension_filter_harness.js plus a few edge cases
# (None/empty title+company, an include-OR hit, an include-AND miss, an
# exclude-on-company hit) exercised by rejudge-backlog's own tests.
REPRESENTATIVE_JOBS: list[dict[str, str | None]] = [
    {"title": "AI Engineer", "company": "Acme Data"},
    {"title": "Senior AI Engineer", "company": ""},
    {"title": "Junior AI Engineer", "company": ""},
    {"title": "Software engineer IA CIBER", "company": ""},
    {"title": "Ingeniero/Ingeniera de Soluciones de IA", "company": ""},
    {"title": "IA Developer", "company": "Globex Consulting"},
    {"title": "Full Stack Developer AI", "company": ""},
    {"title": "Data Analyst", "company": ""},
    {"title": "Científico de Datos IA", "company": ""},
    {"title": "Machine Learning Engineer", "company": "Acme"},
    {"title": "AI Specialist", "company": "Acme"},  # include AND miss (no role term)
    {"title": "Data Analyst", "company": "AI Corp"},  # include only checks TITLE
    {"title": "AI Engineer", "company": "micro1"},  # exclude on COMPANY
    {"title": None, "company": None},
    {"title": "", "company": ""},
]


@requires_node
def test_python_port_matches_filterjs_on_representative_titles() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), json.dumps(REPRESENTATIVE_JOBS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, (
        f"title filter parity harness failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    js_results = json.loads(result.stdout)
    py_results = [
        passes_filter(job["title"], job["company"], DEFAULT_TITLE_FILTER)
        for job in REPRESENTATIVE_JOBS
    ]
    assert py_results == js_results


@requires_node
def test_python_constants_equal_the_filterjs_defaults() -> None:
    """Both built-in profiles are verbatim copies of filter.js's lists."""
    result = subprocess.run(
        ["node", str(HARNESS), "--dump"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    js = json.loads(result.stdout)
    for name, py in (("ai", DEFAULT_TITLE_FILTER), ("backend", BACKEND_TITLE_FILTER)):
        assert js[name]["enabled"] is py.enabled
        assert js[name]["includeGroups"] == py.include_groups
        assert js[name]["excludeTerms"] == py.exclude_terms


def test_with_company_blocklist_swaps_only_the_blocklist() -> None:
    same = with_company_blocklist(DEFAULT_TITLE_FILTER, DEFAULT_COMPANY_BLOCKLIST)
    assert same is DEFAULT_TITLE_FILTER

    swapped = with_company_blocklist(BACKEND_TITLE_FILTER, ("Acme", "Beta"))
    assert swapped.include_groups == BACKEND_TITLE_FILTER.include_groups
    assert "micro1" not in swapped.exclude_terms
    # New terms take the place of the old block; "Freelance" stays after them.
    assert swapped.exclude_terms[-3:] == ["Acme", "Beta", "Freelance"]
    assert passes_filter("Backend Developer", "Acme", swapped) is False
    assert passes_filter("Backend Developer", "micro1", swapped) is True


def test_normalize_lowercases_strips_accents_and_punctuation() -> None:
    assert normalize("AI Engineer") == "ai engineer"
    assert normalize("Científico") == normalize("Cientifico")
    assert normalize("Full-Stack/Backend") == "full stack backend"
    assert normalize("AI   Engineer  ") == "ai engineer"
    assert normalize(None) == ""
    assert normalize("") == ""


def test_matches_term_is_whole_word_only() -> None:
    assert matches_term(normalize("AI Engineer"), "AI") is True
    assert matches_term(normalize("said"), "AI") is False
    assert matches_term(normalize("maintain the pipeline"), "AI") is False
    assert matches_term(normalize("Media Specialist"), "IA") is False
    assert matches_term(normalize("Ingeniero de IA"), "IA") is True
    assert matches_term(normalize("Full-Stack AI Engineer"), "full stack") is True


def test_passes_filter_disabled_always_true() -> None:
    disabled = TitleFilter(
        enabled=False, include_groups=[["nomatch"]], exclude_terms=["x"]
    )
    assert passes_filter("Random Job Nobody Wants", "Whatever Inc", disabled) is True


def test_passes_filter_include_groups_anded_terms_ored() -> None:
    assert (
        passes_filter("Machine Learning Engineer", "Acme", DEFAULT_TITLE_FILTER) is True
    )
    assert passes_filter("AI Specialist", "Acme", DEFAULT_TITLE_FILTER) is False


def test_passes_filter_include_checks_title_only() -> None:
    assert passes_filter("Data Analyst", "AI Corp", DEFAULT_TITLE_FILTER) is False


def test_passes_filter_exclude_checks_title_and_company() -> None:
    assert passes_filter("Senior AI Engineer", "Acme", DEFAULT_TITLE_FILTER) is False
    assert passes_filter("AI Engineer", "micro1", DEFAULT_TITLE_FILTER) is False


def test_passes_filter_none_title_and_company_do_not_crash() -> None:
    assert passes_filter(None, None, DEFAULT_TITLE_FILTER) is False
