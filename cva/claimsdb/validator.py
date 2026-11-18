"""Whitelist validator: emitted text must stay inside the claims DB.

Deterministic matching strategy (claims-db spec, design D6):

- Tokens are lowercased, accent-stripped, and split on any
  non-alphanumeric character.
- Vocabulary = every normalized token from claim texts and claim tags.
- A token is flagged when it looks like claim content (length >= 3 and not
  purely numeric) but matches neither the vocabulary nor the conservative
  ``STOPWORDS`` set below (CV section headers + ES/EN function words) —
  minimizing false positives on rendered boilerplate.
- Numbers and metric values are never flagged; enforcement is token-level,
  not fact-level.
- Output is the sorted set of offending tokens: stable, unique, comparable.

The function is pure: no disk access, no network, no LLM — safe to run
post-render on every emitted artifact.
"""

import re
import unicodedata
from typing import TYPE_CHECKING

from cva.claimsdb.loader import ClaimsDB

if TYPE_CHECKING:
    from cva.cvgen.master import MasterCv

MIN_TOKEN_LENGTH = 3

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# EN + ES + CA function words and CV section headers (rendered boilerplate).
# CA rows (v3 part B): months + section-header words of the Catalan master.
_STOPWORDS_TEXT = """
the and for with from into over per all any are was were have has its this
that these those professional summary experience education projects skills
languages certifications stack tools contact name date entry title areas
covered los las del una que con por para sin sobre desde hasta entre como
todo bajo mas pero sus este esta estos estas perfil formacion experiencia
proyectos habilidades idiomas certificaciones herramientas resumen contacto
nombre fecha profesional lenguajes programacion programming data
enero febrero marzo abril mayo junio julio agosto septiembre octubre
noviembre diciembre jan feb mar apr may jun jul aug sep sept oct nov dec
actualidad
gener febrer marc maig juny juliol agost setembre novembre desembre
actualitat certificacions eines idiomes llenguatges
"""

STOPWORDS: frozenset[str] = frozenset(_STOPWORDS_TEXT.split())


def whitelist_violations(
    text: str, db: ClaimsDB, *, master: "MasterCv | None" = None
) -> list[str]:
    """Return the sorted tokens in ``text`` that carry no claim in ``db``.

    With ``master`` (a ``cva.cvgen.master.MasterCv``) the owner-authored
    master leaf texts join the approved vocabulary: the v3 Catalan master
    renders the owner's dict text, which is deliberately not claims-DB
    vocabulary (claim curation stays with the owner). Lazy import keeps
    this module free of cvgen coupling for the pure-text callers.
    """
    vocabulary = _vocabulary(db)
    if master is not None:
        from cva.cvgen.master import iter_leaves

        for leaf in iter_leaves(master):
            vocabulary |= frozenset(_tokens(leaf.text))
    flagged = {
        token
        for token in _tokens(text)
        if len(token) >= MIN_TOKEN_LENGTH
        and token not in STOPWORDS
        and not token.isdigit()
        and token not in vocabulary
    }
    return sorted(flagged)


def _vocabulary(db: ClaimsDB) -> frozenset[str]:
    """Vocabulary from claim text/tags, skipping matching-only `soft` claims.

    Soft-skill claims (claims-db spec) exist only to be matched against
    requirements; no rendered CV content may originate from them. Letting
    their wording into the whitelist vocabulary would silently widen what
    the validator accepts as "in the DB" for rendered text.
    """
    tokens: set[str] = set()
    for claim in db.claims:
        if claim.category == "soft":
            continue
        tokens.update(_tokens(claim.text))
        for tag in claim.tags:
            tokens.update(_tokens(tag))
    return frozenset(tokens)


def _tokens(text: str) -> list[str]:
    lowered = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(char for char in lowered if not unicodedata.combining(char))
    return [token for token in _TOKEN_SPLIT.split(stripped) if token]
