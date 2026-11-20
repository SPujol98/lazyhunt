"""``GET``/``POST /profile`` on the bridge (onboarding-v2 T10, S9/S10).

The extension Options page edits, after onboarding, the preferences the
questionnaire sets and the ``setup-*`` claims through these endpoints. They
are driven here through a real bridge (loopback, ephemeral port, httpx) over
a copy of the demo profile and a real ``JobService``: same secret header as
the other endpoints, the same write path as ``/setup <section>``
(``write_answers(section=...)``, the ``setup-*`` claims merge, the
all-or-nothing snapshot) and the live reload of the preferences.
"""

import hashlib
import json
import shutil
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cva import bridge
from cva.config import Settings
from cva.onboarding import commit, profile_edit
from cva.service import JobService

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PROFILE = REPO_ROOT / "profiles" / "demo"
SECRET = "s3cret-profile"
OWNER = 4242

BACKGROUND = {
    "spoken_languages": [
        {"language": "es", "level": "native"},
        {"language": "en", "level": "C1"},
        {"language": "Basque", "level": "B1"},
    ],
    "skills": ["Python", "SQL", "C++"],
    "experience": {"years": "3-5", "role": "Data Engineer", "company": "Acme"},
    "education": "BSc Computer Science",
}


def _auth(value: str = SECRET) -> dict[str, str]:
    return {"X-CVA-Bridge-Secret": value}


def _hashes(profile: Path) -> dict[str, str]:
    return {
        str(p.relative_to(profile)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(profile.rglob("*"))
        if p.is_file()
    }


def _prefs(profile: Path) -> dict[str, Any]:
    return tomllib.loads((profile / "preferences.toml").read_text("utf-8"))


@pytest.fixture()
def live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> Iterator[SimpleNamespace]:
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "me"
    shutil.copytree(DEMO_PROFILE, profile)
    (profile / "onboarding_answers.json").unlink(missing_ok=True)
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=OWNER,
        CVA_BRIDGE_SECRET=SECRET,
        bridge_port=0,
        ui_language="en",
    )
    service = JobService.from_settings(settings, db_path=tmp_path / "app.db")
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot=None, bot_data={})
    handle = bridge.register(app, settings, service)
    assert handle is not None
    base = f"http://127.0.0.1:{handle.port}"
    yield SimpleNamespace(base=base, profile=profile, service=service)
    handle.stop()


def _get(live: SimpleNamespace, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("headers", _auth())
    return httpx.get(f"{live.base}/profile", **kwargs)


def _post(live: SimpleNamespace, body: Any, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("headers", _auth())
    return httpx.post(f"{live.base}/profile", json=body, timeout=10, **kwargs)


# --- rule: authenticated exactly like the other endpoints ---------------------------


@pytest.mark.parametrize("headers", [{}, _auth("wrong")])
def test_profile_without_the_secret_is_401_and_writes_nothing(
    live: SimpleNamespace, headers: dict[str, str]
) -> None:
    before = _hashes(live.profile)
    assert _get(live, headers=headers).status_code == 401
    resp = _post(
        live, {"section": "level", "values": {"level": "mid"}}, headers=headers
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}
    assert _hashes(live.profile) == before


def test_a_browser_preflight_is_never_accepted(live: SimpleNamespace) -> None:
    """A web page can only send the secret header after a CORS preflight; the
    bridge answers none, so the browser never sends the real request."""
    resp = httpx.request(
        "OPTIONS",
        f"{live.base}/profile",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-cva-bridge-secret,content-type",
        },
    )
    assert resp.status_code >= 400
    assert "access-control-allow-origin" not in resp.headers


def test_a_web_page_origin_is_refused_even_with_the_secret(
    live: SimpleNamespace,
) -> None:
    before = _hashes(live.profile)
    headers = {**_auth(), "Origin": "https://evil.example"}
    assert _get(live, headers=headers).status_code == 403
    resp = _post(
        live, {"section": "level", "values": {"level": "mid"}}, headers=headers
    )
    assert resp.status_code == 403
    assert _hashes(live.profile) == before
    ext = {**_auth(), "Origin": "chrome-extension://abcdefghijklmnop"}
    assert _get(live, headers=ext).status_code == 200


@pytest.mark.parametrize(
    "path", ["/profile/", "/profile?x=1", "/PROFILE", "/profile%2F..%2Fconfig"]
)
def test_other_paths_are_404(live: SimpleNamespace, path: str) -> None:
    assert httpx.get(f"{live.base}{path}", headers=_auth()).status_code == 404


# --- rule: GET shows the questionnaire values and the claims ------------------------


def test_get_profile_returns_the_current_values_and_claims(
    live: SimpleNamespace,
) -> None:
    resp = _get(live)
    assert resp.status_code == 200
    body = resp.json()
    sections = body["sections"]
    assert sections["level"] == {"level": None}
    assert sections["offer_langs"] == {"offer_langs": ["es", "en", "ca"]}
    assert sections["salary"] == {"min_salary": None, "currency": None}
    assert sections["location"] == {
        "work_modes": [],
        "work_city": None,
        "country": None,
    }
    assert sections["background"] == {
        "spoken_languages": [],
        "skills": [],
        "experience": None,
        "education": None,
    }
    assert sections["positions"]["custom_titles"] == []
    assert body["claims"]["setup"] == []
    other = {c["id"]: c for c in body["claims"]["other"]}
    assert "identity-contact" in other
    assert set(other["identity-contact"]) == {"id", "category", "text"}
    options = body["options"]
    assert options["levels"] == ["junior", "mid", "senior"]
    assert [f["id"] for f in options["families"]] == ["ai", "backend"]
    assert body["limits"]["skills"] == 20


# --- rule: POST writes through the /setup <section> path and reloads ---------------


def test_post_level_writes_only_that_key_and_reloads_the_bot(
    live: SimpleNamespace,
) -> None:
    before = _hashes(live.profile)
    old = _prefs(live.profile)
    resp = _post(live, {"section": "level", "values": {"level": "senior"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["profile"]["sections"]["level"] == {"level": "senior"}
    new = _prefs(live.profile)
    assert new["search"]["level"] == "senior"
    assert {k: v for k, v in new.items() if k != "search"} == old
    assert live.service.loaded_preferences.search.level == "senior"
    changed = {rel for rel, h in _hashes(live.profile).items() if before.get(rel) != h}
    assert changed <= {
        "preferences.toml",
        "preferences.toml.bak",
        "onboarding_answers.json",
    }
    record = json.loads((live.profile / "onboarding_answers.json").read_text("utf-8"))
    assert record["section"] == "level"


def test_post_every_preferences_section(live: SimpleNamespace) -> None:
    posts = [
        (
            "location",
            {
                "work_modes": ["remote", "hybrid"],
                "work_city": "Valencia",
                "country": "Spain",
            },
        ),
        ("salary", {"min_salary": 40000, "currency": "EUR"}),
        ("offer_langs", {"offer_langs": ["en", "es"]}),
        (
            "positions",
            {
                "families": ["ai"],
                "kept_families": [],
                "custom_titles": ["Data Analyst"],
            },
        ),
    ]
    for section, values in posts:
        resp = _post(live, {"section": section, "values": values})
        assert resp.status_code == 200, (section, resp.text)
    prefs = _prefs(live.profile)
    assert prefs["search"]["work_modes"] == ["remote", "hybrid"]
    assert prefs["location"]["city"] == "Valencia"
    assert prefs["location"]["country"] == "Spain"
    assert prefs["search"]["min_salary"] == 40000
    assert prefs["gates"]["allowed_languages"] == ["es", "en"]
    assert set(prefs["families"]) == {"ai", "data_analyst"}
    assert prefs["families"]["data_analyst"]["title_terms"] == ["Data Analyst"]
    view = _get(live).json()["sections"]
    assert view["salary"] == {"min_salary": 40000, "currency": "EUR"}
    assert view["positions"]["families"] == ["ai"]
    assert view["positions"]["kept_families"] == ["data_analyst"]
    # Skip the salary: both keys go away, as with the Telegram Skip.
    assert (
        _post(
            live,
            {"section": "salary", "values": {"min_salary": None, "currency": None}},
        ).status_code
        == 200
    )
    assert "min_salary" not in _prefs(live.profile)["search"]


def test_post_background_replaces_setup_claims_and_keeps_hand_written_bytes(
    live: SimpleNamespace,
) -> None:
    claims_path = live.profile / "claims.json"
    old = claims_path.read_text("utf-8")
    hand_written = old[: old.rstrip().rstrip("]").rstrip().rfind("}") + 1]
    resp = _post(live, {"section": "background", "values": BACKGROUND})
    assert resp.status_code == 200, resp.text
    new = claims_path.read_text("utf-8")
    assert new.startswith(hand_written)
    ids = [c["id"] for c in json.loads(new)]
    setup = [cid for cid in ids if cid.startswith("setup-")]
    assert setup == [
        "setup-lang-es",
        "setup-lang-en",
        "setup-lang-basque",
        "setup-skill-python",
        "setup-skill-sql",
        "setup-skill-cplusplus",
        "setup-experience",
        "setup-education",
    ]
    view = resp.json()["profile"]
    assert [c["id"] for c in view["claims"]["setup"]] == setup
    assert view["sections"]["background"] == BACKGROUND
    # Edit (drop a skill, change a level): only the setup-* claims change.
    edited = {
        **BACKGROUND,
        "skills": ["Python"],
        "spoken_languages": [{"language": "en", "level": "C2"}],
    }
    assert _post(live, {"section": "background", "values": edited}).status_code == 200
    again = claims_path.read_text("utf-8")
    assert again.startswith(hand_written)
    after = [c for c in json.loads(again) if c["id"].startswith("setup-")]
    assert [c["id"] for c in after] == [
        "setup-lang-en",
        "setup-skill-python",
        "setup-experience",
        "setup-education",
    ]
    # A preferences section never touches claims.json.
    assert (
        _post(live, {"section": "level", "values": {"level": "mid"}}).status_code == 200
    )
    assert claims_path.read_text("utf-8") == again


def test_a_typed_common_language_name_becomes_its_code(live: SimpleNamespace) -> None:
    values = {
        **BACKGROUND,
        "spoken_languages": [{"language": "English", "level": "C1"}],
    }
    resp = _post(live, {"section": "background", "values": values})
    assert resp.status_code == 200, resp.text
    langs = resp.json()["profile"]["sections"]["background"]["spoken_languages"]
    assert langs == [{"language": "en", "level": "C1"}]


# --- rule: every field validated like the questionnaire; nothing written otherwise ---


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({"section": "level", "values": {"level": "boss"}}, {"level": "choice"}),
        ({"section": "level", "values": {"level": 3}}, {"level": "type"}),
        ({"section": "level", "values": {}}, {"level": "required"}),
        (
            {"section": "level", "values": {"level": "mid", "x": 1}},
            {"x": "unknown_field"},
        ),
        ({"section": "nope", "values": {}}, {"section": "choice"}),
        (
            {"section": "level", "values": {"level": "mid"}, "extra": 1},
            {"extra": "unknown_field"},
        ),
        ({"section": "level", "values": []}, {"values": "type"}),
        ([], {"body": "type"}),
        (
            {"section": "salary", "values": {"min_salary": True, "currency": "EUR"}},
            {"min_salary": "type"},
        ),
        (
            {"section": "salary", "values": {"min_salary": 10**10, "currency": "EUR"}},
            {"min_salary": "salary"},
        ),
        (
            {"section": "salary", "values": {"min_salary": -1, "currency": "EUR"}},
            {"min_salary": "salary"},
        ),
        (
            {"section": "salary", "values": {"min_salary": 1000, "currency": "EURO"}},
            {"currency": "currency"},
        ),
        (
            {
                "section": "location",
                "values": {"work_modes": [], "work_city": "X", "country": "Y"},
            },
            {"work_modes": "pick_one"},
        ),
        (
            {
                "section": "location",
                "values": {"work_modes": ["moon"], "work_city": "X", "country": "Y"},
            },
            {"work_modes": "choice"},
        ),
        (
            {
                "section": "location",
                "values": {
                    "work_modes": ["remote"],
                    "work_city": "a\x00b",
                    "country": "Y",
                },
            },
            {"work_city": "text"},
        ),
        (
            {
                "section": "location",
                "values": {
                    "work_modes": ["remote"],
                    "work_city": "x" * 101,
                    "country": "Y",
                },
            },
            {"work_city": "text"},
        ),
        (
            {"section": "offer_langs", "values": {"offer_langs": ["xx"]}},
            {"offer_langs": "choice"},
        ),
        (
            {"section": "offer_langs", "values": {"offer_langs": []}},
            {"offer_langs": "pick_one"},
        ),
        (
            {
                "section": "positions",
                "values": {"families": [], "kept_families": [], "custom_titles": []},
            },
            {"families": "pick_one"},
        ),
        (
            {
                "section": "positions",
                "values": {
                    "families": ["../x"],
                    "kept_families": [],
                    "custom_titles": [],
                },
            },
            {"families": "choice"},
        ),
        (
            {
                "section": "positions",
                "values": {
                    "families": [],
                    "kept_families": ["frontend"],
                    "custom_titles": [],
                },
            },
            {"kept_families": "choice"},
        ),
        (
            {
                "section": "positions",
                "values": {
                    "families": [],
                    "kept_families": [],
                    "custom_titles": ["a\nb"],
                },
            },
            {"custom_titles": "titles"},
        ),
        (
            {
                "section": "background",
                "values": {**BACKGROUND, "skills": [f"s{i}" for i in range(21)]},
            },
            {"skills": "skills"},
        ),
        (
            {"section": "background", "values": {**BACKGROUND, "skills": ["x" * 51]}},
            {"skills": "skills"},
        ),
        (
            {"section": "background", "values": {**BACKGROUND, "skills": ["a, b"]}},
            {"skills": "skills"},
        ),
        (
            {"section": "background", "values": {**BACKGROUND, "spoken_languages": []}},
            {"spoken_languages": "pick_one"},
        ),
        (
            {
                "section": "background",
                "values": {
                    **BACKGROUND,
                    "spoken_languages": [{"language": "es", "level": "Z9"}],
                },
            },
            {"spoken_languages": "choice"},
        ),
        (
            {
                "section": "background",
                "values": {**BACKGROUND, "spoken_languages": [{"language": "es"}]},
            },
            {"spoken_languages": "type"},
        ),
        (
            {
                "section": "background",
                "values": {**BACKGROUND, "experience": {"years": "99"}},
            },
            {"experience": "choice"},
        ),
        (
            {
                "section": "background",
                "values": {
                    **BACKGROUND,
                    "experience": {"years": "1-2", "role": None, "company": "Acme"},
                },
            },
            {"experience": "company_without_role"},
        ),
        (
            {
                "section": "background",
                "values": {**BACKGROUND, "education": "\x1b[31m"},
            },
            {"education": "text"},
        ),
    ],
)
def test_invalid_values_are_400_with_field_errors_and_write_nothing(
    live: SimpleNamespace, body: Any, fields: dict[str, str]
) -> None:
    before = _hashes(live.profile)
    resp = _post(live, body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["fields"] == fields
    assert _hashes(live.profile) == before


def test_oversized_body_is_413_and_writes_nothing(live: SimpleNamespace) -> None:
    before = _hashes(live.profile)
    big = {
        "section": "level",
        "values": {"level": "mid"},
        "pad": "x" * bridge.MAX_BODY_BYTES,
    }
    resp = _post(live, big)
    assert resp.status_code == 413
    assert _hashes(live.profile) == before


def test_body_must_be_json(live: SimpleNamespace) -> None:
    before = _hashes(live.profile)
    resp = httpx.post(
        f"{live.base}/profile",
        content=b'{"section":"level","values":{"level":"mid"}}',
        headers={**_auth(), "Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    resp = httpx.post(
        f"{live.base}/profile",
        content=b"{not json",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert _hashes(live.profile) == before


@pytest.mark.parametrize(
    "content",
    [
        b"[" * 100_000 + b"]" * 100_000,  # nested deeper than the decoder can go
        b'{"a":' * 20_000 + b"1" + b"}" * 20_000,
    ],
)
def test_deeply_nested_json_is_400_and_writes_nothing(
    live: SimpleNamespace, content: bytes
) -> None:
    before = _hashes(live.profile)
    resp = httpx.post(
        f"{live.base}/profile",
        content=content,
        headers={**_auth(), "Content-Type": "application/json"},
        timeout=10,
    )
    assert resp.status_code == 400
    assert resp.json()["error"].startswith("invalid json")
    assert _hashes(live.profile) == before


# --- rule: all-or-nothing, like the Telegram Confirm --------------------------------


def test_a_failing_write_rolls_everything_back(
    live: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _hashes(live.profile)

    def broken_hook(profile_dir: Path, answers: dict[str, Any]) -> None:
        (profile_dir / "claims.json").write_text("garbage", "utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(profile_edit, "CONFIRM_HOOKS", [broken_hook])
    resp = _post(live, {"section": "background", "values": BACKGROUND})
    assert resp.status_code == 500
    assert resp.json()["rolledBack"] is True
    assert "disk full" in resp.json()["detail"]
    assert str(live.profile) not in resp.text
    assert _hashes(live.profile) == before


# --- rule: never interleaved with a /setup Confirm or another POST ------------------


def test_a_post_waits_for_a_running_confirm(live: SimpleNamespace) -> None:
    before = _hashes(live.profile)
    done: list[httpx.Response] = []
    with commit.WRITE_LOCK:  # what a /setup Confirm holds while it writes
        worker = threading.Thread(
            target=lambda: done.append(
                _post(live, {"section": "level", "values": {"level": "junior"}})
            )
        )
        worker.start()
        time.sleep(0.3)
        assert not done
        assert _hashes(live.profile) == before
    worker.join(timeout=10)
    assert done and done[0].status_code == 200
    assert _prefs(live.profile)["search"]["level"] == "junior"


def test_the_telegram_confirm_takes_the_same_lock(live: SimpleNamespace) -> None:
    from cva.onboarding import flow
    from cva.onboarding.prefill import current_answers

    answers = {**current_answers(live.profile), "level": "mid"}
    finished = threading.Event()

    def confirm() -> None:
        flow._commit_all(live.profile, answers, [], {}, "en", "level")
        finished.set()

    with commit.WRITE_LOCK:
        worker = threading.Thread(target=confirm)
        worker.start()
        assert not finished.wait(0.3)
    worker.join(timeout=10)
    assert finished.is_set()


# --- rule: setup mode answers 503 like the other setup-mode endpoints ---------------


@pytest.fixture()
def setup_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> Iterator[str]:
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=OWNER,
        CVA_BRIDGE_SECRET=SECRET,
        bridge_port=0,
    )
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot_data={})
    handle = bridge.register_setup(app, settings)
    assert handle is not None
    yield f"http://127.0.0.1:{handle.port}/profile"
    handle.stop()


def test_setup_mode_profile_is_503(setup_bridge: str) -> None:
    assert httpx.get(setup_bridge).status_code == 401
    resp = httpx.get(setup_bridge, headers=_auth())
    assert resp.status_code == 503
    assert resp.json() == {"error": bridge.SETUP_ERROR}
    resp = httpx.post(
        setup_bridge,
        json={"section": "level", "values": {"level": "mid"}},
        headers=_auth(),
    )
    assert resp.status_code == 503
    assert httpx.post(setup_bridge, json={}).status_code == 401


def test_without_a_profile_dir_profile_is_503(
    legacy_profile: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    from tests.service_stub import service_stub

    monkeypatch.chdir(tmp_path)
    settings = clean_settings(
        owner_telegram_id=OWNER, CVA_BRIDGE_SECRET=SECRET, bridge_port=0
    )
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot=None, bot_data={})
    handle = bridge.register(app, settings, service_stub(65))
    assert handle is not None
    try:
        resp = httpx.get(f"http://127.0.0.1:{handle.port}/profile", headers=_auth())
        assert resp.status_code == 503
        assert resp.json() == {"error": bridge.NO_PROFILE_DIR_ERROR}
    finally:
        handle.stop()


# --- rule: without [families] the built-ins in effect are shown and kept (fix) -----


def test_unedited_positions_without_families_keep_the_effective_registry(
    live: SimpleNamespace,
) -> None:
    from cva.profile_prefs import load_preferences

    assert "families" not in _prefs(live.profile)  # the demo declares none
    registry = load_preferences(live.profile / "preferences.toml").families
    prefs_bytes = (live.profile / "preferences.toml").read_bytes()
    positions = _get(live).json()["sections"]["positions"]
    assert positions["families"] == ["ai", "backend"]  # the built-ins in effect
    resp = _post(live, {"section": "positions", "values": positions})
    assert resp.status_code == 200, resp.text
    assert load_preferences(live.profile / "preferences.toml").families == registry
    assert live.service.loaded_preferences.families == registry
    assert (live.profile / "preferences.toml").read_bytes() == prefs_bytes
