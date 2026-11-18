"""Multi-source offer-URL registry (infojobs-source, design D1/D2).

One seam for every ``source → id → canonical URL`` derivation the bridge,
service and scheduler already perform through ``cva.ingest.job_id_from_url``:
hostname-based :func:`source_from_url`, per-source :func:`job_id_from_url`
dispatch, and :func:`canonical_url` producing the per-source canonical offer
URL. LinkedIn shapes are byte-identical to the pre-registry behavior — the
numeric regex and the ``https://linkedin.com/jobs/view/{id}`` canonical moved
here verbatim from ``cva.ingest.guest`` — because bridge/service/scheduler
already import the seam and must not churn (D1).

InfoJobs canonical (D2): the payload URL NORMALIZED — query/fragment
stripped, host forced to ``https://www.infojobs.net`` (protocol-relative
forms included) — never an id-derived shape: the real canonical is
``https://www.infojobs.net/{city}/{slug}/of-{id}`` and city/slug is not
derivable from the id, so an id-derived guess risks a dead card link on
every InfoJobs card. Accepted v1 tradeoff: a city/slug rename re-files the
offer (the id stays stored in ``job_id``).

Adding a portal (portal-adapters T4b) means ONE :class:`SourceSpec` entry in
:data:`SOURCES`: its host(s) and job-id regex. The source id doubles as the
stored ``offers.origen``; the bridge validation, the scheduler's inline retry
and the server-side fetch routing read the registry, never a portal name.
"""

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

# A source id is the portal id (``linkedin``, ``infojobs``, ...): the registry
# is open, so it is a plain string rather than a closed Literal.
Source = str


@dataclass(frozen=True)
class SourceSpec:
    """How one portal's offer URLs are recognised and canonicalised.

    ``hosts`` are registrable domains (subdomains match too). ``job_id_re``
    captures the offer id in group 1. ``canonical_template`` (``{id}``) builds
    an id-derived canonical; without it the canonical is the original URL
    normalised (https, query/fragment stripped, host forced to
    ``canonical_host`` when given). ``inline_only`` portals are read in the
    owner's logged-in tab: the bridge requires the inline offer text and the
    server never fetches them beyond one plain GET.
    """

    id: str
    hosts: tuple[str, ...]
    job_id_re: re.Pattern[str]
    canonical_host: str | None = None
    canonical_template: str | None = None
    inline_only: bool = True
    # False for portals read ONLY in the owner's own tab (their robots.txt
    # disallows AI crawlers): `fetch_offer` raises a paste-prompt WallError
    # without any HTTP call. Distinct from `inline_only`, which still allows
    # the single anti-ban GET.
    server_fetch: bool = True
    # False for portals that suggested searches must not pre-select (in-tab
    # only, or search ignores keywords); the owner can still add them by hand.
    suggest_by_default: bool = True
    # Human portal name shown to the owner ("También en: InfoJobs"); empty
    # falls back to the id through :func:`source_label`.
    label: str = ""
    # False for portals where the owner applies with the portal's own profile
    # (no CV is uploaded): cards drop the Generate-CV action and its toggles.
    cv_needed: bool = True


# LinkedIn job-id URL shapes (single source of truth, moved verbatim from
# cva.ingest.guest): the public /jobs/view/{id} page and the guest
# jobPosting/{id} API endpoint.
_LINKEDIN_JOB_ID_RE = re.compile(
    r"linkedin\.com/(?:jobs/view/|jobs-guest/jobs/api/jobPosting/)(\d+)",
    re.IGNORECASE,
)
# InfoJobs offer-id shapes: the real one is ``/of-i<30 hex>`` (owner's saved
# results page, 2026-10); the older spike shape ``/of-<32 lowercase chars>`` is
# still accepted. The id keeps its leading ``i``.
_INFOJOBS_JOB_ID_RE = re.compile(r"/of-(i[0-9a-f]{30}|[a-z0-9]{32})(?![a-z0-9])")

# Indeed job key: 16 hex chars in ``/viewjob?jk=<id>`` (owner's saved page).
_INDEED_JOB_ID_RE = re.compile(r"[?&]jk=([0-9a-f]{16})(?![0-9a-f])")

# Tecnoempleo offer id: ``/rf-<20 hex>`` at the end of the offer path.
_TECNOEMPLEO_JOB_ID_RE = re.compile(r"/(rf-[0-9a-f]{20})(?![0-9a-f])")

# JobFluent offer id: the 6-hex key ending the ``/jobs/<slug>-<6hex>`` path.
_JOBFLUENT_JOB_ID_RE = re.compile(r"/jobs/[^/?#]*-([0-9a-f]{6})(?=[/?#]|$)")

# Manfred offer id: the number in ``/ofertas-empleo/<num>/<slug>``.
_MANFRED_JOB_ID_RE = re.compile(r"/ofertas-empleo/(\d+)(?=[/?#]|$)")

# Adzuna offer id: the number in ``/details/<id>`` (also ``/land/ad/<id>``).
_ADZUNA_JOB_ID_RE = re.compile(r"/(?:details|land/ad)/(\d+)(?=[/?#]|$)")

# foorilla offer id: the digits ending the ``/hiring/jobs/<slug>-<id>/`` path.
_FOORILLA_JOB_ID_RE = re.compile(r"/hiring/jobs/[^/?#]*-(\d+)(?=[/?#]|$)")


# Single source of truth for the portal id: each spec carries its own ``id``
# and SOURCES is derived from it, so an id can never be spelled twice.
# New portals: add one SourceSpec to this list (see docs/portals.md).
_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        id="linkedin",
        label="LinkedIn",
        hosts=("linkedin.com",),
        job_id_re=_LINKEDIN_JOB_ID_RE,
        canonical_template="https://linkedin.com/jobs/view/{id}",
        inline_only=False,
    ),
    SourceSpec(
        id="infojobs",
        label="InfoJobs",
        hosts=("infojobs.net",),
        job_id_re=_INFOJOBS_JOB_ID_RE,
        canonical_host="www.infojobs.net",
    ),
    # Indeed: read in the owner's tab. `server_fetch` stays True on purpose:
    # `/analyze` makes ONE plain GET that usually hits anti-bot and degrades to
    # the paste prompt (WallError).
    SourceSpec(
        id="indeed",
        label="Indeed",
        hosts=("indeed.com",),
        job_id_re=_INDEED_JOB_ID_RE,
        canonical_template="https://es.indeed.com/viewjob?jk={id}",
    ),
    # Tecnoempleo: in-tab reader only. Its robots.txt disallows AI crawlers, so
    # the bot never fetches it (owner decision 2026-10-28): inline text only.
    SourceSpec(
        id="tecnoempleo",
        label="Tecnoempleo",
        hosts=("tecnoempleo.com",),
        job_id_re=_TECNOEMPLEO_JOB_ID_RE,
        canonical_host="www.tecnoempleo.com",
        inline_only=True,
        server_fetch=False,
        suggest_by_default=False,
        # Applied to with the owner's Tecnoempleo profile (owner 2026-10-29).
        cv_needed=False,
    ),
    # JobFluent: in-tab reader only (robots.txt disallows AI crawlers).
    SourceSpec(
        id="jobfluent",
        label="JobFluent",
        hosts=("jobfluent.com",),
        job_id_re=_JOBFLUENT_JOB_ID_RE,
        canonical_host="www.jobfluent.com",
        inline_only=True,
        server_fetch=False,
        suggest_by_default=False,
    ),
    # Manfred: JS-rendered SPA, read in the owner's tab (inline text); the
    # single plain GET is still allowed. Its search ignores keywords, so it is
    # not suggested by default.
    SourceSpec(
        id="manfred",
        label="Manfred",
        hosts=("getmanfred.com",),
        job_id_re=_MANFRED_JOB_ID_RE,
        canonical_host="www.getmanfred.com",
        suggest_by_default=False,
    ),
    # Adzuna: aggregator read only in the owner's tab (Cloudflare blocks plain
    # HTTP clients), so the bot never fetches it and does not suggest it.
    SourceSpec(
        id="adzuna",
        label="Adzuna",
        hosts=("adzuna.es",),
        job_id_re=_ADZUNA_JOB_ID_RE,
        canonical_template="https://www.adzuna.es/details/{id}",
        inline_only=True,
        server_fetch=False,
        suggest_by_default=False,
    ),
    # foorilla: global hiring feed read in the owner's tab. The offer panel
    # (#main2) is filled by htmx, so a plain server GET returns no description:
    # no server fetch. Not suggested by default (the feed is worldwide).
    SourceSpec(
        id="foorilla",
        label="foorilla",
        hosts=("foorilla.com",),
        job_id_re=_FOORILLA_JOB_ID_RE,
        canonical_host="foorilla.com",
        inline_only=True,
        server_fetch=False,
        suggest_by_default=False,
    ),
)

SOURCES: dict[str, SourceSpec] = {spec.id: spec for spec in _SPECS}


def source_from_url(url: str) -> Source | None:
    """Hostname-based source dispatch; ``None`` for unknown domains (D1)."""
    host = (urlsplit(url).hostname or "").lower()
    for spec in SOURCES.values():
        if any(host == h or host.endswith(f".{h}") for h in spec.hosts):
            return spec.id
    return None


def source_label(source: str) -> str:
    """Human portal name for a stored ``origen``/source id; the id if unknown."""
    spec = SOURCES.get(source)
    return (spec.label if spec else "") or source


def _spec_for(source: str | None, url: str | None) -> SourceSpec | None:
    """The offer's portal: the stored ``origen`` wins, the URL is the fallback."""
    spec = SOURCES.get(source) if source else None
    if spec is None and url:
        portal = source_from_url(url)
        spec = SOURCES.get(portal) if portal else None
    return spec


def needs_cv(source: str | None, url: str | None = None) -> bool:
    """False when the offer's portal is applied to without a CV.

    Unknown portals and pastes keep the CV (True).
    """
    spec = _spec_for(source, url)
    return spec is None or spec.cv_needed


def no_cv_label(source: str | None, url: str | None = None) -> str | None:
    """Portal label when its offers are applied to without a CV, else ``None``."""
    spec = _spec_for(source, url)
    if spec is None or spec.cv_needed:
        return None
    return spec.label or spec.id


def allows_server_fetch(source: str | None) -> bool:
    """False for a registered portal that must never be fetched server-side."""
    spec = SOURCES.get(source) if source else None
    return spec is None or spec.server_fetch


def is_inline_source(source: str | None) -> bool:
    """True for a registered portal whose offer text travels inline."""
    spec = SOURCES.get(source) if source else None
    return spec is not None and spec.inline_only


def job_id_from_url(url: str) -> str | None:
    """Per-source job id from the registered portal's id regex."""
    source = source_from_url(url)
    if source is None:
        return None
    match = SOURCES[source].job_id_re.search(url)
    return match.group(1) if match else None


def _normalized_netloc(parts: SplitResult) -> str:
    """Lowercase host without userinfo, ``www.`` or a default port."""
    host = (parts.hostname or "").removeprefix("www.")
    try:
        port = parts.port
    except ValueError:  # malformed port: drop it rather than guess
        port = None
    if port is not None and port not in (80, 443):
        return f"{host}:{port}"
    return host


def canonical_url(source: Source, job_id: str, *, original: str | None = None) -> str:
    """Canonical offer URL per source (D1/D2).

    LinkedIn: id-derived ``https://linkedin.com/jobs/view/{id}`` (unchanged).
    Every other portal: the original URL normalized — strip query/fragment,
    https, host forced to the spec's ``canonical_host`` (InfoJobs:
    ``www.infojobs.net``) or normalized (lowercase, no userinfo, no default
    port, no leading ``www.``) — so every card links the exact page the
    beacon found. ``original`` is mandatory for those: ``ValueError`` when
    missing/blank.
    """
    spec = SOURCES[source]
    if spec.canonical_template is not None:
        return spec.canonical_template.format(id=job_id)
    if original is None or not original.strip():
        raise ValueError(
            f"{source} canonical URL requires the original offer URL: the "
            "path is not derivable from the job id (D2)"
        )
    raw = original.strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parts = urlsplit(raw)
    host = spec.canonical_host or _normalized_netloc(parts)
    return urlunsplit(("https", host, parts.path or "/", "", ""))
