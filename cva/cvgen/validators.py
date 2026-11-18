"""Post-render validators (cv-generation spec; design D9).

Three deterministic gates over the rendered PDF — no LLM, no network:

1. Page count must be exactly one (pypdf).
2. The extracted text must be claims-whitelist clean (``whitelist_violations``).
3. The contact/identity block must be intact versus the master: every
   alphanumeric token of the header identity leaves must appear in the
   rendered text.

Each validator returns a list of human-readable violations (empty = pass);
the renderer aggregates them into ``RenderResult.violations`` and any
entry blocks delivery naming the failed validator.
"""

import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from cva.claimsdb.loader import ClaimsDB
from cva.claimsdb.validator import whitelist_violations
from cva.cvgen.master import MasterCv
from cva.profile_identity import DEFAULT_IDENTITY, Identity

EXPECTED_PAGES = 1
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def extract_text(pdf_path: Path | str) -> str:
    """Extract the full text of a PDF (all pages) via pypdf."""
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def page_count(pdf_path: Path | str) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def check_page_count(pdf_path: Path | str) -> list[str]:
    """Validator 1: page count == 1."""
    count = page_count(pdf_path)
    if count == EXPECTED_PAGES:
        return []
    return [f"page-count: expected {EXPECTED_PAGES}, got {count}"]


def check_whitelist(
    pdf_path: Path | str, db: ClaimsDB, master: MasterCv | None = None
) -> list[str]:
    """Validator 2: rendered text stays inside the approved vocabulary.

    The vocabulary is the claims DB plus — when the rendered ``master`` is
    given — that master's own owner-authored leaf texts (v3 part B): the
    Catalan AI_CA master renders the owner's hand-tailored Catalan dict, whose tokens
    are deliberately not claims-DB vocabulary (claim curation is the
    owner's call). The render path is deterministic and LLM-free and the
    masters are content-parity-tested against the owner's base PDFs, so
    the gate still blocks anything the engine or a template injects.
    """
    violations = whitelist_violations(extract_text(pdf_path), db, master=master)
    if not violations:
        return []
    return [f"whitelist: unbacked tokens {', '.join(violations)}"]


def check_contact(
    pdf_path: Path | str,
    master: MasterCv,
    identity: Identity = DEFAULT_IDENTITY,
) -> list[str]:
    """Validator 3: the master's identity block survives into the render.

    The identity leaves are the profile's contact claims (ES base ids + EN
    twins; owner amendment 2026-08-30: EN masters reference the `_en` claims).
    """
    identity_claim_ids = identity.contact_claim_ids
    rendered_tokens = _tokens(extract_text(pdf_path))
    expected = [*master.header.contact, master.header.name]
    if master.header.tagline is not None:
        expected.append(master.header.tagline)
    missing: set[str] = set()
    for leaf in expected:
        if leaf.claim_id not in identity_claim_ids:
            continue  # only identity claims form the contact block
        missing.update(
            token for token in _tokens(leaf.text) if token not in rendered_tokens
        )
    if not missing:
        return []
    return [
        f"contact: identity tokens missing from render: {', '.join(sorted(missing))}"
    ]


def _tokens(text: str) -> set[str]:
    lowered = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return {token for token in _TOKEN_SPLIT.split(stripped) if token}
