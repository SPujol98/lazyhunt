"""A fake extra portal ("otroportal") registered through the sources registry.

Adding a real portal means one ``SourceSpec`` in ``cva.ingest.sources``; the
tests register the same kind of entry for the test's duration.
"""

import re

import pytest

from cva.ingest import sources

FAKE_ID = "4f8a1c2d9e"
FAKE_URL = "https://www.otroportal.example/ingeniero-ia-python/rf-" + FAKE_ID + "/"


def register_fake_portal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sources.SOURCES,
        "otroportal",
        sources.SourceSpec(
            id="otroportal",
            hosts=("otroportal.example",),
            job_id_re=re.compile(r"/rf-([0-9a-f]+)"),
        ),
    )
