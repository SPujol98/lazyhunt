"""Wall classifier table-driven behavior (offer-ingest spec, design D1/D2).

Fixtures are minimal synthetic bodies encoding the REAL marker shapes from
docs/spike_guest_evidence.md — not KB dumps:

- shell body (guest search fragment, spike verdict WALLED): 404 + ~320 KB page
  whose detectable markers are ``<title>LinkedIn</title>``, zero
  ``data-entity-urn`` and zero JSON-LD JobPosting.
- fragment body (guest jobPosting endpoint, spike verdict PARTIAL): 200 HTML
  fragment with description markup, NO shell title, zero urn, zero JSON-LD.
- public-view body (spike verdict PARTIAL): 200 page with ``data-entity-urn``
  markers and a job title, zero JSON-LD.
"""

import pytest

from cva.wall import WallKind, classify

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4459804598"
VIEW_URL = "https://www.linkedin.com/jobs/view/4459804598/"
NON_WWW_VIEW_URL = "https://linkedin.com/jobs/view/4406534440"
SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPagePostings/search"
    "?keywords=ingeniero+de+ia&geoId=105088894&start=0"
)
AUTHWALL_URL = "https://www.linkedin.com/authwall?trk=guest_job_page&originalReferer=&sessionRedirect="

# Minimal replica of the walled search fragment (all three shell markers present).
SHELL_BODY = (
    '<!DOCTYPE html><html lang="en"><head><title>LinkedIn</title></head>'
    '<body><div id="app-js-root"></div>'
    "<script>window.__initialState={};</script></body></html>"
)

# Minimal replica of the guest jobPosting HTML fragment (spike: 200, ~20 KB,
# description present, no shell markers, no JSON-LD).
FRAGMENT_BODY = (
    '<h2 class="top-card-layout__title font-sans">Senior AI Engineer</h2>'
    '<span class="topcard__flavor">Acme Corp</span>'
    '<div class="show-more-less-html__markup"><p>Build retrieval systems.</p></div>'
)

# Minimal replica of the public /jobs/view page (spike: 200, 37 data-entity-urn,
# no JSON-LD, no shell title).
PUBLIC_VIEW_BODY = (
    "<html><head><title>Data Scientist job at Initech</title></head><body>"
    '<div data-entity-urn="urn:li:job:4459804598"></div>'
    '<div data-entity-urn="urn:li:job:4459804599"></div>'
    "</body></html>"
)

JSONLD_BODY = (
    "<html><head>"
    '<script type="application/ld+json">'
    '{"@context":"https://schema.org","@type":"JobPosting",'
    '"title":"AI Engineer","hiringOrganization":{"name":"Acme"},'
    '"description":"Build LLM systems."}'
    "</script></head><body></body></html>"
)

CASES = [
    # RATE_LIMITED — 999/429 win over everything (priority order).
    pytest.param(999, "", GUEST_URL, WallKind.RATE_LIMITED, id="999-rate-limited"),
    pytest.param(
        429, SHELL_BODY, GUEST_URL, WallKind.RATE_LIMITED, id="429-beats-shell"
    ),
    # LOGIN_WALL — 3xx or authwall/login final URL.
    pytest.param(302, "", AUTHWALL_URL, WallKind.LOGIN_WALL, id="302-authwall"),
    pytest.param(
        301,
        SHELL_BODY,
        "https://www.linkedin.com/uas/login?session_redirect=%2Fjobs%2Fview",
        WallKind.LOGIN_WALL,
        id="301-login-url-beats-shell",
    ),
    pytest.param(
        200,
        "",
        AUTHWALL_URL,
        WallKind.LOGIN_WALL,
        id="200-authwall-final-url",
    ),
    # JS_SHELL — shell markers with 404 (spike replica) and 200.
    pytest.param(
        404, SHELL_BODY, SEARCH_URL, WallKind.JS_SHELL, id="404-shell-spike-replica"
    ),
    pytest.param(200, SHELL_BODY, GUEST_URL, WallKind.JS_SHELL, id="200-shell"),
    # NOT_FOUND — bare 404 without shell markers.
    pytest.param(
        404,
        "<html><body>Page not found</body></html>",
        GUEST_URL,
        WallKind.NOT_FOUND,
        id="404-plain-body",
    ),
    pytest.param(404, "", GUEST_URL, WallKind.NOT_FOUND, id="404-empty-body"),
    # TRANSIENT — 5xx/transport (status 0 encodes transport failure).
    pytest.param(503, "", GUEST_URL, WallKind.TRANSIENT, id="503-transient"),
    pytest.param(
        500, "Internal Server Error", GUEST_URL, WallKind.TRANSIENT, id="500-transient"
    ),
    pytest.param(0, "", GUEST_URL, WallKind.TRANSIENT, id="0-transport-failure"),
    pytest.param(
        503, SHELL_BODY, GUEST_URL, WallKind.TRANSIENT, id="503-shell-still-transient"
    ),
    # OK — parseable content: spike fragment, JSON-LD block, public-view urns.
    pytest.param(200, FRAGMENT_BODY, GUEST_URL, WallKind.OK, id="200-spike-fragment"),
    pytest.param(200, JSONLD_BODY, VIEW_URL, WallKind.OK, id="200-jsonld-jobposting"),
    pytest.param(
        200, PUBLIC_VIEW_BODY, VIEW_URL, WallKind.OK, id="200-public-view-urns"
    ),
]


@pytest.mark.parametrize(("status", "body", "final_url", "expected"), CASES)
def test_classify_table(
    status: int, body: str, final_url: str, expected: WallKind
) -> None:
    verdict = classify(status, body, final_url)
    assert verdict.kind is expected
    assert verdict.detail  # every verdict carries non-empty detail


def test_shell_marker_conjunction_jobposting_jsonld_defeats_shell() -> None:
    """A JobPosting JSON-LD block breaks the zero-JSON-LD shell marker."""
    body = SHELL_BODY.replace(
        "<body>",
        '<body><script type="application/ld+json">'
        '{"@type":"JobPosting","description":"x"}</script>',
    )
    assert classify(200, body, VIEW_URL).kind is WallKind.OK


def test_shell_marker_conjunction_entity_urn_defeats_shell() -> None:
    """A data-entity-urn marker breaks the zero-urn shell marker."""
    body = SHELL_BODY.replace(
        "<body>", '<body><div data-entity-urn="urn:li:job:4459804598"></div>'
    )
    assert classify(200, body, VIEW_URL).kind is WallKind.OK


# Synthetic replica of the live reCAPTCHA challenge shell served at the
# non-www /jobs/view/{id} form (owner probe: HTTP 200, ~21 KB, base href
# google.com/recaptcha, zero description, no ``<title>LinkedIn</title>``).
RECAPTCHA_CHALLENGE_BODY = (
    "<!DOCTYPE html><html><head>"
    '<base href="https://google.com/recaptcha">'
    "<title>Security Verification</title></head>"
    '<body><div class="challenge-container">Verify you are human.</div>'
    "</body></html>"
)


def test_recaptcha_challenge_shell_classifies_ok_known_miss() -> None:
    """KNOWN MISS, pinned as approval reality (owner-flagged, live probe).

    The challenge shell carries none of the three shell markers (no exact
    ``<title>LinkedIn</title>``), so classify() returns OK and ingest fails
    later with OfferParseError. The guest-first fetch strategy in
    ``cva.ingest.guest`` deliberately does NOT depend on reclassifying this
    shape; if the classifier ever learns challenge shells, update this test
    consciously.
    """
    verdict = classify(200, RECAPTCHA_CHALLENGE_BODY, NON_WWW_VIEW_URL)
    assert verdict.kind is WallKind.OK


# --- Per-source markers: InfoJobs (infojobs-source, design D6) ---------------

INFOJOBS_URL = (
    "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
    "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
)

# Captured challenge/empty-state evidence (29 KB shell served server-side):
# exact <title>InfoJobs</title>, zero JobPosting JSON-LD, zero ij- content
# tokens. Never OK — the server fetch is dead; the extension inline text is
# the production path (Path B).
INFOJOBS_CHALLENGE_BODY = (
    "<!DOCTYPE html><html><head><title>InfoJobs</title></head>"
    '<body><div class="ij-challenge">Verifica que eres humano</div>'
    "</body></html>"
)

# Content bodies: JobPosting JSON-LD (server Path A) or the ij-Template /
# ij-Box content tokens the rendered offer page carries.
INFOJOBS_JSONLD_BODY = (
    "<html><head><title>Oferta de empleo</title>"
    '<script type="application/ld+json">'
    '{"@type":"JobPosting","title":"Ingeniero IA",'
    '"description":"Imprescindible Python."}'
    "</script></head><body></body></html>"
)
INFOJOBS_TOKEN_BODY = (
    "<html><head><title>Oferta de empleo</title></head>"
    '<body><div class="ij-Template--card"><p>Imprescindible Python.</p></div>'
    "</body></html>"
)
INFOJOBS_TITLE_WITH_TOKEN_BODY = (
    "<html><head><title>InfoJobs</title></head>"
    '<body><div class="ij-Box"><p>Imprescindible Python.</p></div>'
    "</body></html>"
)


def test_infojobs_challenge_shell_is_never_ok() -> None:
    """Challenge shell (title + zero JSON-LD + zero ij- tokens) → JS_SHELL."""
    verdict = classify(200, INFOJOBS_CHALLENGE_BODY, INFOJOBS_URL, source="infojobs")
    assert verdict.kind is WallKind.JS_SHELL
    assert verdict.detail


def test_infojobs_challenge_shell_404_is_js_shell_not_not_found() -> None:
    """Status priority unchanged: shell markers turn a 404 into JS_SHELL."""
    verdict = classify(404, INFOJOBS_CHALLENGE_BODY, INFOJOBS_URL, source="infojobs")
    assert verdict.kind is WallKind.JS_SHELL


@pytest.mark.parametrize(
    "body",
    [INFOJOBS_JSONLD_BODY, INFOJOBS_TOKEN_BODY, INFOJOBS_TITLE_WITH_TOKEN_BODY],
    ids=["jsonld-content", "ij-template-token", "shell-title-plus-ij-token"],
)
def test_infojobs_content_body_classifies_ok(body: str) -> None:
    """A body carrying the offer content (JSON-LD or ij- tokens) is OK — a
    content token defeats the shell even with the shell title present."""
    assert classify(200, body, INFOJOBS_URL, source="infojobs").kind is WallKind.OK


def test_infojobs_rate_limited_wins_over_markers() -> None:
    """429 stays RATE_LIMITED regardless of body (status priority unchanged)."""
    verdict = classify(429, INFOJOBS_CHALLENGE_BODY, INFOJOBS_URL, source="infojobs")
    assert verdict.kind is WallKind.RATE_LIMITED


def test_infojobs_plain_404_without_markers_is_not_found() -> None:
    body = "<html><body>Oferta no encontrada</body></html>"
    verdict = classify(404, body, INFOJOBS_URL, source="infojobs")
    assert verdict.kind is WallKind.NOT_FOUND


def test_linkedin_classification_is_the_keyword_default() -> None:
    """source defaults to linkedin: the legacy call shape is unchanged."""
    assert classify(200, SHELL_BODY, GUEST_URL).kind is WallKind.JS_SHELL
    assert classify(200, INFOJOBS_CHALLENGE_BODY, GUEST_URL).kind is WallKind.OK


def test_infojobs_body_classified_as_linkedin_is_not_shell() -> None:
    """The InfoJobs title alone means nothing to the LinkedIn marker set."""
    verdict = classify(200, INFOJOBS_CHALLENGE_BODY, INFOJOBS_URL, source="linkedin")
    assert verdict.kind is WallKind.OK
