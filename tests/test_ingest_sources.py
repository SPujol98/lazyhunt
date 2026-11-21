"""Multi-source offer-URL registry (infojobs-source, design D1/D2).

Pure table tests over ``cva.ingest.sources``: hostname → source dispatch,
per-source job-id shapes, and per-source canonical URLs. InfoJobs canonical
is the payload URL NORMALIZED (D2 — strip query/fragment, force the
``https://www.infojobs.net`` host), never an id-derived shape: the real
canonical is ``https://www.infojobs.net/{city}/{slug}/of-{id}`` and the
city/slug segment is not derivable from the id, so an id-derived guess risks
a dead card link on every InfoJobs card.
"""

import pytest

from cva.ingest.sources import (
    SOURCES,
    allows_server_fetch,
    canonical_url,
    is_inline_source,
    job_id_from_url,
    source_from_url,
)
from tests.fake_portal import FAKE_ID, FAKE_URL, register_fake_portal

INFOJOBS_ID = "0f8d7e6c5b4a39281706f5e4d3c2b1a0"
LINKEDIN_VIEW = "https://www.linkedin.com/jobs/view/4459804598/"
LINKEDIN_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/998877"
INFOJOBS_OFFER = (
    "https://www.infojobs.net/barcelona/ingeniero-ia/of-" + INFOJOBS_ID + "/"
)
INFOJOBS_PROTOCOL_RELATIVE = (
    "//www.infojobs.net/barcelona/ingeniero-ia/of-" + INFOJOBS_ID
)
INFOJOBS_TRACKED = INFOJOBS_OFFER + "?applicantSource=undefined&utm_source=google"
# Real shape (owner's saved page, 2026-10): of-i<30 hex> plus tracking params.
INFOJOBS_REAL_ID = "i5ae057f3fd45baa70a6caf62fca709"
INFOJOBS_REAL = (
    "https://www.infojobs.net/barcelona/programador-php-python-mysql/of-"
    + INFOJOBS_REAL_ID
)
INFOJOBS_TRACKING = "?applicationOrigin=search-filtered%7Celement%7E1&searchId=1"
INFOJOBS_REAL_TRACKED = INFOJOBS_REAL + INFOJOBS_TRACKING
INFOJOBS_NON_WWW = "https://infojobs.net/madrid/data-scientist/of-" + INFOJOBS_ID


# --- source_from_url: hostname dispatch, unknown → None ----------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (LINKEDIN_VIEW, "linkedin"),
        ("https://linkedin.com/jobs/view/42", "linkedin"),
        (LINKEDIN_GUEST, "linkedin"),
        ("https://www.linkedin.com/authwall?trk=x", "linkedin"),
        (INFOJOBS_OFFER, "infojobs"),
        (INFOJOBS_NON_WWW, "infojobs"),
        ("https://www.example.com/jobs/view/42", None),
        ("https://jobbands.infojobs.example.com/of-" + INFOJOBS_ID, None),
        ("not a url", None),
    ],
)
def test_source_from_url_hostname_dispatch(url: str, expected: str | None) -> None:
    assert source_from_url(url) == expected


# --- job_id_from_url: per-source shapes --------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # LinkedIn shapes: numeric regex, byte-identical to pre-registry behavior.
        ("https://www.linkedin.com/jobs/view/4459804598/", "4459804598"),
        ("https://linkedin.com/jobs/view/42", "42"),
        (LINKEDIN_GUEST, "998877"),
        (LINKEDIN_GUEST + "/", "998877"),
        ("https://www.linkedin.com/jobs/view/", None),
        # InfoJobs shape: /of-<32 lowercase hex-ish chars>.
        (INFOJOBS_OFFER, INFOJOBS_ID),
        (INFOJOBS_PROTOCOL_RELATIVE, INFOJOBS_ID),
        (INFOJOBS_REAL, INFOJOBS_REAL_ID),
        (INFOJOBS_REAL_TRACKED, INFOJOBS_REAL_ID),
        ("https://www.infojobs.net/oferta/of-short123/", None),
        # Unknown domain → None (never guesses).
        ("https://example.com/of-" + INFOJOBS_ID, None),
        ("https://example.com/other", None),
    ],
)
def test_job_id_from_url_multi_source(url: str, expected: str | None) -> None:
    assert job_id_from_url(url) == expected


# --- canonical_url: per-source canonical shapes ------------------------------


def test_canonical_url_linkedin_is_id_derived_unchanged() -> None:
    assert canonical_url("linkedin", "4459804598") == (
        "https://linkedin.com/jobs/view/4459804598"
    )


def test_canonical_url_infojobs_is_normalized_original() -> None:
    """D2: canonical = the payload URL normalized (query/fragment stripped,
    https://www.infojobs.net host forced) — the city/slug path is preserved."""
    assert canonical_url("infojobs", INFOJOBS_ID, original=INFOJOBS_TRACKED) == (
        INFOJOBS_OFFER
    )


def test_canonical_url_infojobs_real_id_drops_tracking_params() -> None:
    assert (
        canonical_url("infojobs", INFOJOBS_REAL_ID, original=INFOJOBS_REAL_TRACKED)
        == INFOJOBS_REAL
    )


def test_canonical_url_infojobs_strips_fragment() -> None:
    tracked = INFOJOBS_OFFER + "#sistema-de-sugerencias"
    assert canonical_url("infojobs", INFOJOBS_ID, original=tracked) == INFOJOBS_OFFER


def test_canonical_url_infojobs_normalizes_protocol_relative() -> None:
    assert canonical_url(
        "infojobs", INFOJOBS_ID, original=INFOJOBS_PROTOCOL_RELATIVE
    ) == ("https://www.infojobs.net/barcelona/ingeniero-ia/of-" + INFOJOBS_ID)


def test_canonical_url_infojobs_forces_www_host() -> None:
    assert canonical_url("infojobs", INFOJOBS_ID, original=INFOJOBS_NON_WWW) == (
        "https://www.infojobs.net/madrid/data-scientist/of-" + INFOJOBS_ID
    )


def test_canonical_url_infojobs_requires_original() -> None:
    """The city/slug canonical is not derivable from the id: absent original
    is a programming error, not a silent LinkedIn-shaped guess."""
    with pytest.raises(ValueError, match="original"):
        canonical_url("infojobs", INFOJOBS_ID)
    with pytest.raises(ValueError, match="original"):
        canonical_url("infojobs", INFOJOBS_ID, original="   ")


def test_canonical_url_no_shape_is_valid_for_both_sources() -> None:
    """Spec: each source yields its own canonical shape and no canonical URL
    is valid for both sources."""
    linkedin = canonical_url("linkedin", INFOJOBS_ID)
    infojobs = canonical_url("infojobs", INFOJOBS_ID, original=INFOJOBS_OFFER)
    assert "linkedin.com" in linkedin and "infojobs" not in linkedin
    assert "infojobs.net" in infojobs and "linkedin" not in infojobs


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__]))


# --- registry-driven portals (portal-adapters T4b) ---------------------------------


def test_unregistered_host_is_unknown_until_it_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert source_from_url(FAKE_URL) is None
    register_fake_portal(monkeypatch)
    assert source_from_url(FAKE_URL) == "otroportal"
    assert source_from_url("https://otroportal.example/x") == "otroportal"
    assert source_from_url("https://nototroportal.example/x") is None
    assert job_id_from_url(FAKE_URL) == FAKE_ID


def test_registered_portal_canonical_is_the_normalized_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_fake_portal(monkeypatch)
    assert canonical_url(
        "otroportal", FAKE_ID, original=FAKE_URL + "?utm=x#top"
    ) == FAKE_URL.replace("www.", "")
    with pytest.raises(ValueError):
        canonical_url("otroportal", FAKE_ID)


def test_inline_sources_are_every_portal_but_linkedin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_fake_portal(monkeypatch)
    assert is_inline_source("infojobs") is True
    assert is_inline_source("otroportal") is True
    assert is_inline_source("linkedin") is False
    assert is_inline_source("otro") is False
    assert is_inline_source(None) is False


@pytest.mark.parametrize(
    "variant",
    [
        "https://WWW.Otroportal.example/ingeniero-ia-python/rf-4f8a1c2d9e/",
        "https://otroportal.example:443/ingeniero-ia-python/rf-4f8a1c2d9e/?x=1",
        "http://user:pw@www.otroportal.example/ingeniero-ia-python/rf-4f8a1c2d9e/#f",
        "//www.otroportal.example:443/ingeniero-ia-python/rf-4f8a1c2d9e/",
    ],
)
def test_canonical_host_is_normalized_for_portals_without_canonical_host(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    register_fake_portal(monkeypatch)
    assert canonical_url("otroportal", FAKE_ID, original=variant) == (
        "https://otroportal.example/ingeniero-ia-python/rf-4f8a1c2d9e/"
    )


def test_non_default_port_is_kept_when_normalizing_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_fake_portal(monkeypatch)
    assert canonical_url(
        "otroportal", FAKE_ID, original="https://Otroportal.example:8443/x/rf-1/"
    ) == ("https://otroportal.example:8443/x/rf-1/")


def test_source_registry_is_keyed_by_each_spec_id() -> None:
    assert all(key == spec.id for key, spec in SOURCES.items())
    assert set(SOURCES) == {
        "linkedin",
        "infojobs",
        "indeed",
        "tecnoempleo",
        "jobfluent",
        "manfred",
        "adzuna",
        "foorilla",
    }


# --- Indeed (owner's saved page: /viewjob?jk=<16 hex>) ----------------------------

INDEED_ID = "a1a1a1a1b2b2b2b2"
INDEED_URL = f"https://es.indeed.com/viewjob?jk={INDEED_ID}"


def test_indeed_source_id_and_canonical() -> None:
    tracked = f"https://es.indeed.com/viewjob?jk={INDEED_ID}&from=serp&vjs=3#x"
    assert source_from_url(tracked) == "indeed"
    assert job_id_from_url(tracked) == INDEED_ID
    assert canonical_url("indeed", INDEED_ID) == INDEED_URL


def test_indeed_ignores_urls_without_a_job_key() -> None:
    assert job_id_from_url("https://es.indeed.com/jobs?q=python") is None
    assert job_id_from_url("https://es.indeed.com/viewjob?jk=short") is None
    assert is_inline_source("indeed") is True


# --- Tecnoempleo (in-tab only; id ``rf-<20 hex>``) ---------------------------------

TECNOEMPLEO_ID = "rf-a1b2c3d4e5f6a1b2c3d4"
TECNOEMPLEO_URL = (
    f"https://www.tecnoempleo.com/fullstack-python/api-rest/{TECNOEMPLEO_ID}"
)


def test_tecnoempleo_source_id_and_canonical() -> None:
    tracked = TECNOEMPLEO_URL + "?utm=x#top"
    assert source_from_url(tracked) == "tecnoempleo"
    assert job_id_from_url(tracked) == TECNOEMPLEO_ID
    assert canonical_url("tecnoempleo", TECNOEMPLEO_ID, original=tracked) == (
        TECNOEMPLEO_URL
    )
    assert job_id_from_url("https://www.tecnoempleo.com/ofertas-trabajo/?te=x") is None


def test_in_tab_only_portals_refuse_server_fetch() -> None:
    assert SOURCES["tecnoempleo"].server_fetch is False
    assert SOURCES["indeed"].server_fetch is True
    assert allows_server_fetch("tecnoempleo") is False
    assert allows_server_fetch("linkedin") is True
    assert allows_server_fetch(None) is True
