"""Company research for cover letters: 30-day per-company cache, URL-only facts,
sources shown next to the letter, graceful degradation. No network."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

import bot
from cva.llm import LlmResponseError
from cva.match.interview_prep import CompanyFact, CompanyFacts
from cva.notifier import cover_letter_sources_message
from cva.tracker.repo import OfferInput
from tests.test_cover_letter_bot import _press
from tests.test_cover_letter_service import ES_LETTER, _service
from tests.test_cover_letter_v2 import ES_PARTS

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
GOOD = {
    "fact": "Acme fabrica agentes de IA.",
    "source_url": "https://acme.example/about",
}
NO_URL = {"fact": "Acme tiene 500 empleados.", "source_url": ""}
BAD_URL = {"fact": "Acme es líder.", "source_url": "acme.example/no-scheme"}


class _WebLlm:
    """Fake provider: structured letters plus a web-search facts completion."""

    model = "fake"
    supports_web_search = True

    def __init__(self, *facts: dict | Exception, items: list[dict] | None = None):
        self.facts = list(facts) or [{"items": items if items is not None else [GOOD]}]
        self.web_calls = 0
        self.last_user = ""

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.last_user = user
        return schema.model_validate(ES_PARTS)

    async def web_json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.web_calls += 1
        answer = self.facts[min(self.web_calls, len(self.facts)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return schema.model_validate(answer)


def _setup(tmp_path: Path, llm: _WebLlm, clock: list[datetime] | None = None):
    service, repo, oid = _service(tmp_path, llm)
    if clock is not None:
        service._now = lambda: clock[0]  # type: ignore[method-assign]
    return service, repo, oid


def _letter(service, oid):
    return asyncio.run(service.cover_letters(oid))


def test_facts_with_url_reach_the_prompt_and_the_result(tmp_path: Path) -> None:
    llm = _WebLlm()
    service, _repo, oid = _setup(tmp_path, llm)
    result = _letter(service, oid)
    assert result.facts == [CompanyFact(**GOOD)] and result.thin_company is False
    assert "Acme fabrica agentes de IA." in llm.last_user
    assert "https://acme.example/about" in llm.last_user
    assert result.text == ES_LETTER  # the fixed text is unaffected by research


def test_facts_without_a_real_url_are_never_used(tmp_path: Path) -> None:
    llm = _WebLlm(items=[GOOD, NO_URL, BAD_URL])
    service, _repo, oid = _setup(tmp_path, llm)
    result = _letter(service, oid)
    assert [f.fact for f in result.facts] == [GOOD["fact"]]
    assert "500 empleados" not in llm.last_user and "líder" not in llm.last_user


def test_nothing_usable_means_thin_company_and_letter_from_the_offer(
    tmp_path: Path,
) -> None:
    llm = _WebLlm(items=[NO_URL])
    service, _repo, oid = _setup(tmp_path, llm)
    result = _letter(service, oid)
    assert result.facts == [] and result.thin_company is True
    assert "none found" in llm.last_user and result.text == ES_LETTER


def test_cache_hit_researches_once_per_company(tmp_path: Path) -> None:
    llm = _WebLlm()
    service, _repo, oid = _setup(tmp_path, llm, [T0])
    _letter(service, oid)
    second = _letter(service, oid)
    assert llm.web_calls == 1
    assert second.facts == [CompanyFact(**GOOD)]


def test_cache_key_is_the_normalised_company_across_offers(tmp_path: Path) -> None:
    llm = _WebLlm()
    service, repo, oid = _setup(tmp_path, llm, [T0])
    other = repo.upsert_offer(
        OfferInput(
            title="Backend",
            company=" ACME S.L. ",
            url="https://x/2",
            text="Buscamos Backend en Valencia. Python.",
            source="link",
            profile="ai",
        )
    )
    _letter(service, oid)
    _letter(service, other)
    assert llm.web_calls == 1


def test_cache_persists_across_service_instances(tmp_path: Path) -> None:
    first_llm = _WebLlm()
    service, _repo, oid = _setup(tmp_path, first_llm, [T0])
    _letter(service, oid)
    # A new service on the same database (a bot restart) reuses the entry.
    second_llm = _WebLlm()
    again, _repo2, oid2 = _service(tmp_path, second_llm)
    again._now = lambda: T0  # type: ignore[method-assign]
    assert _letter(again, oid2).facts == [CompanyFact(**GOOD)]
    assert second_llm.web_calls == 0


def test_cache_expires_after_30_days(tmp_path: Path) -> None:
    llm = _WebLlm({"items": [GOOD]}, {"items": [{**GOOD, "fact": "Acme es nueva."}]})
    clock = [T0]
    service, _repo, oid = _setup(tmp_path, llm, clock)
    _letter(service, oid)
    clock[0] = T0 + timedelta(days=29, hours=23)
    assert _letter(service, oid).facts[0].fact == GOOD["fact"] and llm.web_calls == 1
    clock[0] = T0 + timedelta(days=30, minutes=1)
    refreshed = _letter(service, oid)
    assert llm.web_calls == 2 and refreshed.facts[0].fact == "Acme es nueva."


def test_an_empty_successful_search_is_cached_too(tmp_path: Path) -> None:
    llm = _WebLlm(items=[])
    service, _repo, oid = _setup(tmp_path, llm, [T0])
    _letter(service, oid)
    _letter(service, oid)
    assert llm.web_calls == 1


def test_web_failure_degrades_gracefully_and_is_not_cached(tmp_path: Path) -> None:
    llm = _WebLlm(LlmResponseError("down"), {"items": [GOOD]})
    service, _repo, oid = _setup(tmp_path, llm, [T0])
    first = _letter(service, oid)  # no exception to the owner
    assert first.facts == [] and first.thin_company and first.text == ES_LETTER
    assert _letter(service, oid).facts == [CompanyFact(**GOOD)]
    assert llm.web_calls == 2


def test_a_failed_refresh_serves_the_expired_entry(tmp_path: Path) -> None:
    llm = _WebLlm({"items": [GOOD]}, RuntimeError("boom"))
    clock = [T0]
    service, _repo, oid = _setup(tmp_path, llm, clock)
    _letter(service, oid)
    clock[0] = T0 + timedelta(days=40)
    assert _letter(service, oid).facts == [CompanyFact(**GOOD)]


def test_corrupt_cache_entry_is_ignored(tmp_path: Path) -> None:
    llm = _WebLlm()
    service, repo, oid = _setup(tmp_path, llm, [T0])
    repo.set_state("letter_company_facts:acme", "{not json")
    assert _letter(service, oid).facts == [CompanyFact(**GOOD)]
    assert json.loads(repo.get_state("letter_company_facts:acme") or "{}")["items"]


def test_provider_without_web_search_gives_a_thin_letter_and_no_cache(
    tmp_path: Path,
) -> None:
    llm = _WebLlm()
    llm.supports_web_search = False
    service, repo, oid = _setup(tmp_path, llm)
    assert _letter(service, oid).thin_company is True
    assert llm.web_calls == 0 and repo.get_state("letter_company_facts:acme") is None


def test_unknown_company_is_not_researched(tmp_path: Path) -> None:
    llm = _WebLlm()
    service, repo, _oid = _setup(tmp_path, llm)
    other = repo.upsert_offer(
        OfferInput(
            title="AI",
            company="",
            url="https://x/3",
            text="Buscamos AI Engineer en Valencia. Python y RAG.",
            source="link",
            profile="ai",
        )
    )
    assert _letter(service, other).thin_company is True
    assert llm.web_calls == 0


def test_prep_still_gets_company_facts_from_the_shared_search(tmp_path: Path) -> None:
    from cva.match.interview_prep import PrepInputs

    llm = _WebLlm()
    service, _repo, _oid = _setup(tmp_path, llm)
    inputs = PrepInputs(
        stage="rrhh",
        title="AI",
        company="Acme",
        posting_text="",
        cover_letter=None,
        cv_variant=None,
        judge=None,
        claims_digest="",
        stories=[],
        notes=[],
        salary_text="",
    )
    facts = asyncio.run(service._company_facts(inputs))
    assert facts == CompanyFacts(items=[CompanyFact(**GOOD)])
    llm.supports_web_search = False
    assert asyncio.run(service._company_facts(inputs)) == CompanyFacts()


# --- shown to the owner ------------------------------------------------------------


def test_sources_message_lists_each_fact_with_its_url() -> None:
    text = cover_letter_sources_message([CompanyFact(**GOOD)])
    assert "Fuentes" in text
    assert (
        "Acme fabrica agentes de IA." in text and "https://acme.example/about" in text
    )


def test_sources_message_without_facts_says_paragraph_two_is_thin() -> None:
    text = cover_letter_sources_message([])
    assert "No encontré datos fiables" in text and "párrafo 2" in text


def test_bot_sends_the_letter_then_its_sources(tmp_path: Path) -> None:
    service, _repo, oid = _setup(tmp_path, _WebLlm())
    replies = _press(service, f"cl:{oid}")
    assert replies[1][0].startswith("✉️ Carta") and ES_LETTER in replies[1][0]
    assert "https://acme.example/about" in replies[2][0]
    assert len(replies) == 3


def test_bot_tells_the_owner_when_the_company_paragraph_is_thin(tmp_path: Path) -> None:
    service, _repo, oid = _setup(tmp_path, _WebLlm(items=[]))
    replies = _press(service, f"cl:{oid}")
    assert "No encontré datos fiables" in replies[2][0]


def test_bot_chosen_letter_is_stored_after_research(tmp_path: Path) -> None:
    service, repo, oid = _setup(tmp_path, _WebLlm())
    _press(service, f"cl:{oid}")
    _press(service, f"clc:{oid}:1:a")
    assert repo.get_offer(oid).carta_presentacion == ES_LETTER


def test_bot_module_exposes_the_sources_notice() -> None:
    assert bot.cover_letter_sources_message is cover_letter_sources_message
