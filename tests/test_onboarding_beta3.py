"""beta.3 questionnaire fixes (beta3-ux T3/T4, S1/S2): driven like
``tests/test_onboarding.py`` through the bot's public handlers."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from telegram.error import BadRequest

import bot
from cva.cvgen.base_pdfs import verified_base_pdf
from cva.i18n import t
from cva.onboarding import flow, starter
from cva.profile_identity import ProfileError
from cva.profile_persona import load_letter_bases
from tests.test_bot import FakeDocument, FakeQuery, FakeUpdate
from tests.test_onboarding import Driver, _setup_mode
from tests.test_onboarding_cv_upload import _pdf, _send, _to_cv_step

NOT_MODIFIED = (
    "Message is not modified: specified new message content and reply markup "
    "are exactly the same as a current content and reply markup of the message"
)


class _SameMessageQuery(FakeQuery):
    """Telegram refuses an edit that leaves the message as it is."""

    def __init__(self, data: str, error: str = NOT_MODIFIED) -> None:
        super().__init__(data)
        self.error = error

    async def edit_message_text(self, text: str, reply_markup: object = None) -> None:
        raise BadRequest(self.error)


def _press_raw(d: Driver, query: FakeQuery) -> None:
    asyncio.run(bot.setup_onboarding_callback(FakeUpdate(query=query), d.context))


def test_an_unchanged_message_edit_is_ignored_and_the_button_answered(
    tmp_path: Path,
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    query = _SameMessageQuery("ob:lang:noop")
    _press_raw(d, query)  # no BadRequest escapes
    assert query.answered
    assert d.context.user_data[flow.STATE_KEY]["step"] == "lang"


def test_other_bad_requests_still_propagate(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    with pytest.raises(BadRequest):
        _press_raw(d, _SameMessageQuery("ob:lang:noop", "Message to edit not found"))


# --- T3: questionnaire fixes ------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_TABLE = REPO_ROOT / "profiles" / "demo" / "salary_reference.toml"


def _answers(d: Driver) -> dict[str, Any]:
    answers: dict[str, Any] = d.context.user_data[flow.STATE_KEY]["a"]
    return answers


def _to_background(d: Driver, lang: str = "en", level: str = "junior") -> None:
    d.start()
    d.press(f"go:{lang}")
    d.text("Alex Rivera")
    d.text("alex@example.com")
    d.press("skip")
    d.text("Barcelona")
    d.text("linkedin.com/in/alex")
    for fid in ("ai", "backend"):  # exactly AI
        chosen = _answers(d).get("families") or []
        if (fid == "ai") != (fid in chosen):
            d.press(f"t:{fid}")
    d.press("done")
    d.press(f"go:{level}")
    assert d.step == "spoken_langs"
    d.press("t:en")
    d.press("done")
    d.press("go:C1")
    assert d.step == "skills"


def _to_education(d: Driver) -> None:
    _to_background(d)
    d.text("Python, SQL")
    d.press("go:1-2")
    d.press("skip")  # no last role
    assert d.step == "education"


def _to_salary(
    d: Driver,
    *,
    level: str = "junior",
    city: str = "Barcelona",
    country: str = "Spain",
    modes: tuple[str, ...] = ("hybrid",),
) -> None:
    _to_background(d, level=level)
    d.text("Python, SQL")
    d.press("go:1-2")
    d.press("skip")
    d.press("skip")  # no education
    for mode in modes:
        d.press(f"t:{mode}")
    d.press("done")
    d.text(city)
    d.text(country)
    assert d.step == "salary"


def test_education_takes_several_entries_one_claim_each(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_education(d)
    d.text("BSc Computer Science — UPV\nAWS Cloud Practitioner")
    assert _answers(d)["education"] == [
        "BSc Computer Science — UPV",
        "AWS Cloud Practitioner",
    ]
    d.press("back")
    assert d.step == "education"
    d.text("BSc Physics, MSc Data Science")  # commas without line breaks
    assert _answers(d)["education"] == ["BSc Physics", "MSc Data Science"]
    d.press("back")
    d.text("A, B, C, D, E, F")  # six entries: refused
    assert d.step == "education"
    assert t("onboarding.err.education", "en") in d.last
    d.text("BSc Physics, MSc Data Science")
    for _ in range(30):  # every later question: first button that moves on
        if d.step == "summary":
            break
        actions = [a.split(":", 2)[2] for _, a in d.buttons()]
        if d.step == "work_modes":
            d.press("t:remote")
        if d.step in ("work_city", "country"):
            d.text("Spain")
            continue
        d.press(next(a for a in ("keep", "skip", "go:EUR", "done") if a in actions))
    assert "BSc Physics; MSc Data Science" in d.last
    d.press("confirm")
    import json

    claims = json.loads((profile / "claims.json").read_text("utf-8"))
    education = [c for c in claims if c["category"] == "education"]
    assert [(c["id"], c["text"]) for c in education] == [
        ("setup-education-bsc-physics", "BSc Physics"),
        ("setup-education-msc-data-science", "MSc Data Science"),
    ]


def test_an_older_answers_file_with_one_education_line_still_loads(
    tmp_path: Path,
) -> None:
    import json

    from cva.onboarding.prefill import current_answers

    profile = tmp_path / "p"
    profile.mkdir()
    (profile / "onboarding_answers.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "answers": {"education": "BSc Computer Science, University X"},
            }
        ),
        encoding="utf-8",
    )
    assert current_answers(profile)["education"] == [
        "BSc Computer Science, University X"  # kept whole, never re-split
    ]


def test_skills_take_up_to_thirty_with_a_tip(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_background(d)
    assert "10-15" in d.last
    d.text(", ".join(f"skill{i}" for i in range(31)))
    assert d.step == "skills" and t("onboarding.err.skills", "en") in d.last
    d.text(", ".join(f"skill{i}" for i in range(30)))
    assert d.step == "exp_years"
    assert len(_answers(d)["skills"]) == 30


def test_salary_step_suggests_the_reference_low_end_when_the_table_has_it(
    tmp_path: Path,
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    (profile / "salary_reference.toml").write_bytes(DEMO_TABLE.read_bytes())
    _to_salary(d)
    expected = t(
        "onboarding.salary.reference",
        "en",
        role="AI",
        level="Junior",
        city="Barcelona",
        low="27,000",
        high="40,000",
        source="Example value (demo profile, not market data)",
    )
    assert expected in d.last
    assert (t("onboarding.btn.use_salary", "en", amount="27,000"), "ob:salary:use") in (
        d.buttons()
    )
    d.press("use")
    assert _answers(d)["min_salary"] == 27000
    assert d.step == "currency"


@pytest.mark.parametrize(
    ("table", "level", "city", "country", "modes"),
    [
        (False, "junior", "Barcelona", "Spain", ("hybrid",)),  # no table
        (True, "senior", "Barcelona", "Spain", ("hybrid",)),  # senior: no rows
        (True, "junior", "Valencia", "Spain", ("onsite",)),  # unknown city
        (True, "junior", "Barcelona", "France", ("hybrid",)),  # not Spain
    ],
)
def test_salary_step_invents_nothing_without_a_reference(
    tmp_path: Path,
    table: bool,
    level: str,
    city: str,
    country: str,
    modes: tuple[str, ...],
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    if table:
        (profile / "salary_reference.toml").write_bytes(DEMO_TABLE.read_bytes())
    _to_salary(d, level=level, city=city, country=country, modes=modes)
    assert t("onboarding.salary.no_reference", "en") in d.last
    assert "ob:salary:use" not in [a for _, a in d.buttons()]
    d.press("use")  # a stale button changes nothing
    assert d.step == "salary"


@pytest.mark.parametrize(
    ("country", "ticked"),
    [
        ("España", "EUR"),
        ("Germany", "EUR"),
        ("United Kingdom", "GBP"),
        ("USA", "USD"),
        ("Suiza", "CHF"),
        ("Japan", "EUR"),
    ],
)
def test_currency_buttons_default_from_the_country(
    tmp_path: Path, country: str, ticked: str
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_salary(d, country=country)
    d.text("40000")
    assert d.step == "currency"
    labels = [label for label, _ in d.buttons()]
    assert f"✅ {ticked}" in labels
    assert t("onboarding.btn.other_currency", "en") in labels
    for code in ("EUR", "USD", "GBP", "CHF"):
        assert code in labels or f"✅ {code}" in labels


def test_currency_other_takes_a_typed_iso_code(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_salary(d)
    d.text("40000")
    d.press("other")
    assert t("onboarding.q.currency_other", "en") in d.last
    d.text("sek")
    assert _answers(d)["currency"] == "SEK"
    assert d.step == "offer_langs"


def test_profile_endpoint_education_accepts_a_list_or_the_old_string(
    tmp_path: Path,
) -> None:
    import shutil

    from cva.onboarding import profile_edit

    profile = tmp_path / "me"
    shutil.copytree(REPO_ROOT / "profiles" / "demo", profile)
    (profile / "onboarding_answers.json").unlink(missing_ok=True)
    values = {
        "spoken_languages": [{"language": "en", "level": "C1"}],
        "skills": ["Python"],
        "experience": {"years": "3-5", "role": None, "company": None},
    }
    out = profile_edit.save(
        profile,
        {
            "section": "background",
            "values": {**values, "education": ["BSc CS, UPV", "MSc AI"]},
        },
        "en",
    )
    view = out["profile"]
    assert view["sections"]["background"]["education"] == "BSc CS, UPV; MSc AI"
    ids = [c["id"] for c in view["claims"]["setup"]]
    assert "setup-education-bsc-cs-upv" in ids and "setup-education-msc-ai" in ids
    # The Options page posts the GET string back unchanged: same entries.
    again = profile_edit.save(
        profile,
        {
            "section": "background",
            "values": {**values, "education": "BSc CS, UPV; MSc AI"},
        },
        "en",
    )
    assert again["profile"]["sections"]["background"]["education"] == (
        "BSc CS, UPV; MSc AI"
    )
    assert view["limits"]["skills"] == 30
    with pytest.raises(profile_edit.InvalidProfile) as bad:
        profile_edit.parse_request(
            {"section": "background", "values": {**values, "education": 7}}, []
        )
    assert bad.value.fields == {"education": "type"}


# --- T4: the base-CV step, CVs and a cover letter from the answers ------------------------


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def _actions(d: Driver) -> list[str]:
    return [a.split(":", 2)[2] for _, a in d.buttons()]


@pytest.mark.parametrize("lang", ["en", "es"])
def test_cv_step_explains_and_offers_upload_create_or_later(
    tmp_path: Path, lang: str
) -> None:
    d, _, _ = _setup_mode(tmp_path, lang)  # type: ignore[arg-type]
    _to_cv_step(d, lang)  # type: ignore[arg-type]
    assert "×" in d.last  # one per position type × language
    labels = [label for label, _ in d.buttons()]
    for key in ("cv_upload", "cv_create", "cv_later", "cv_more", "skip_all"):
        assert t(f"onboarding.btn.{key}", lang) in labels  # type: ignore[arg-type]
    d.press("upload")
    assert t("onboarding.cv.upload_hint", lang) in d.last  # type: ignore[arg-type]
    taken, _ = _send(d, FakeDocument(_pdf()))  # the upload path is unchanged
    assert taken and "✅ AI · ES" in d.last


def test_create_one_renders_a_template_stored_like_an_upload(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("create")
    for tpl in ("classic", "modern", "compact"):
        assert t(f"onboarding.cv.tpl.{tpl}", "en") in d.last
        assert f"tpl:{tpl}" in _actions(d)
    assert "starting point" in d.last
    d.press("tpl:modern")
    assert "✅ AI · ES ✨ Modern" in d.last
    assert (profile / ".onboarding_uploads" / "AI_ES.pdf").is_file()
    assert "AI · EN 👈" in d.last
    d.press("skip_all")
    d.press("skip_all_yes")
    assert d.step == "summary"
    d.press("confirm")
    assert "AI · ES" in d.replies[-1] and "lazyhunt" in d.replies[-1]
    pdf = verified_base_pdf("AI_ES", profile / "cv_base_pdfs")  # manifest sha256
    text = _pdf_text(pdf)
    assert "José Pérez" in text and "Python" in text and "Backend Developer" in text


@pytest.mark.parametrize("lang", ["en", "es"])
def test_skip_all_warns_that_the_bot_cannot_send_any_cv(
    tmp_path: Path, lang: str
) -> None:
    d, _, _ = _setup_mode(tmp_path, lang)  # type: ignore[arg-type]
    _to_cv_step(d, lang)  # type: ignore[arg-type]
    d.press("skip_all")
    assert d.step == "cv_pdfs"
    warning = t(
        "onboarding.cv.skip_warning",
        lang,  # type: ignore[arg-type]
        slots="AI · ES, AI · EN, Data Analyst · ES, Data Analyst · EN",
    )
    assert warning in d.last
    if lang == "en":
        assert "Without a CV the bot cannot send any — create one now?" in d.last
    d.press("create")  # straight to the templates of the first slot
    assert "AI · ES" in d.last and "tpl:classic" in _actions(d)
    d.press("list")
    d.press("skip_all")
    d.press("skip_all_yes")
    assert d.step == "summary"


def test_create_another_version_adds_a_position_type_or_language(
    tmp_path: Path,
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_cv_step(d)  # AI + Data Analyst, es + en
    d.press("more")
    actions = _actions(d)
    assert "add:backend:es" in actions and "add:ai:ca" in actions
    assert "add:ai:es" not in actions  # already a slot
    d.press("add:backend:es")
    assert "Backend · ES 👈" in d.last
    assert "backend" in _answers(d)["families"]
    d.press("more")
    d.press("add:ai:ca")
    assert "ca" in _answers(d)["offer_langs"] and "AI · CA 👈" in d.last


def test_base_cover_letter_is_created_on_confirm_from_the_answers(
    tmp_path: Path,
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("letter")
    d.press("skip_all")
    d.press("skip_all_yes")
    assert t("onboarding.letter.summary", "en") in d.last
    assert not (profile / "cover_letter_base.toml").exists()  # only on Confirm
    d.press("confirm")
    assert t("onboarding.letter.saved", "en") in d.replies[-1]
    bases = load_letter_bases(profile / "cover_letter_base.toml")
    assert set(bases) == {"es", "en", "ca"}
    assert bases["en"].signoff.endswith("José Pérez")
    assert "Python" in bases["en"].paragraph1
    assert "Backend Developer" in bases["en"].paragraph1  # the last role given


def test_an_existing_cover_letter_is_never_offered_or_overwritten(
    tmp_path: Path,
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    (profile / "cover_letter_base.toml").write_text("# mine\n", encoding="utf-8")
    _to_cv_step(d)
    assert "letter" not in _actions(d)
    assert starter.write_cover_letter(profile, {"name": "X"}) is False
    assert (profile / "cover_letter_base.toml").read_text("utf-8") == "# mine\n"


def test_a_failed_cover_letter_undoes_the_whole_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("create")
    d.press("tpl:classic")
    d.press("letter")
    d.press("skip_all")
    d.press("skip_all_yes")

    def refuse(path: Path) -> None:
        raise ProfileError("letter refused")

    monkeypatch.setattr(starter, "load_letter_bases", refuse)
    d.press("confirm")
    assert d.step == "summary"  # kept: Confirm can be pressed again
    written = [
        p
        for p in profile.rglob("*")
        if p.is_file() and ".onboarding_uploads" not in p.parts
    ]
    assert written == []  # every file put back (none existed)
    monkeypatch.undo()
    d.press("confirm")
    assert (profile / "cover_letter_base.toml").is_file()
    verified_base_pdf("AI_ES", profile / "cv_base_pdfs")


def test_confirm_reply_has_a_place_for_one_more_line_and_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Pair", callback_data="x")]])
    monkeypatch.setattr(
        flow,
        "CONFIRM_REPLY_EXTRAS",
        [lambda env, lang: flow.ConfirmExtra("Pair the extension now.", markup)],
    )
    d, _, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("skip_all")
    d.press("skip_all_yes")
    d.press("confirm")
    # The extra comes before the closing "next step" (pair before suggesting).
    reply = d.replies[-1]
    assert "Pair the extension now." in reply
    assert reply.index("Pair the extension now.") < reply.index(
        t("onboarding.next_step", "en")
    )
    assert d.markup is markup
