"""profile-extraction E1: every owner data file resolves through one resolver.

With ``profile_dir`` unset each item resolves to TODAY's location (golden
literals below, independent of the module under test); with it set, each item
comes from ``<profile_dir>/<name>``.
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from cva.config import Settings
from cva.match.persona_defaults import OWNER_JUDGE_PERSONA
from cva.profile_paths import ProfilePaths, resolve_profile_paths
from cva.profile_persona import render_judge_persona_files
from cva.service import JobService
from cva.suggest import suggested_searches_payload
from tests.demo import DEMO_CLAIMS, DEMO_DIR, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
        **overrides,  # type: ignore[arg-type]
    )


# --- golden: unset profile_dir == today's constants -------------------------


def test_unset_profile_dir_resolves_to_todays_paths() -> None:
    paths = resolve_profile_paths(None)

    assert paths.claims == _ROOT / "cva" / "claimsdb" / "claims.json"
    assert paths.synonyms == _ROOT / "cva" / "match" / "synonyms.json"
    assert paths.voice == _ROOT / "data" / "cover_letter_voice.md"
    assert paths.salary_table == _ROOT / "data" / "salary_reference.toml"
    assert paths.honest_gaps == _ROOT / "docs" / "honest_gaps.md"
    assert paths.masters_dir == Path("masters")
    assert paths.base_pdfs_dir == _ROOT / "cv_base_pdfs"
    assert paths.eval_labels == _ROOT / "eval" / "labels.json"
    assert paths.letter_base == _ROOT / "data" / "cover_letter_base.toml"


def test_no_argument_equals_none() -> None:
    assert resolve_profile_paths() == resolve_profile_paths(None)


# --- profile_dir overrides each item ----------------------------------------


def test_profile_dir_overrides_every_item(tmp_path: Path) -> None:
    paths = resolve_profile_paths(tmp_path)

    assert paths == ProfilePaths(
        claims=tmp_path / "claims.json",
        synonyms=tmp_path / "synonyms.json",
        voice=tmp_path / "voice.md",
        salary_table=tmp_path / "salary_reference.toml",
        honest_gaps=tmp_path / "honest_gaps.md",
        masters_dir=tmp_path / "masters",
        base_pdfs_dir=tmp_path / "cv_base_pdfs",
        profile_toml=tmp_path / "profile.toml",
        preferences=tmp_path / "preferences.toml",
        judge_dir=tmp_path / "judge",
        eval_labels=tmp_path / "eval" / "labels.json",
        letter_base=tmp_path / "cover_letter_base.toml",
    )


# --- Settings ---------------------------------------------------------------


def test_profile_dir_setting_defaults_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CVA_PROFILE_DIR", raising=False)

    assert _settings(tmp_path).profile_dir is None


def test_profile_dir_setting_from_toml_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CVA_PROFILE_DIR", raising=False)
    toml = tmp_path / "config.toml"
    toml.write_text('profile_dir = "/srv/profile"\n', encoding="utf-8")
    base = {
        "CVA_TELEGRAM_TOKEN": "123456:TEST",
        "CVA_LLM_API_KEY": "sk-test",
    }

    from_toml = Settings(_env_file=None, toml_file=str(toml), **base)  # type: ignore[arg-type]
    assert from_toml.profile_dir == Path("/srv/profile")

    monkeypatch.setenv("CVA_PROFILE_DIR", "/env/profile")
    from_env = Settings(_env_file=None, toml_file=str(toml), **base)  # type: ignore[arg-type]
    assert from_env.profile_dir == Path("/env/profile")


def test_blank_profile_dir_means_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVA_PROFILE_DIR", "")

    assert _settings(tmp_path).profile_dir is None


# --- loaders route through the resolver -------------------------------------


def _profile(tmp_path: Path) -> Path:
    """A profile dir with distinguishable copies of every loaded item."""
    profile = tmp_path / "profile"
    profile.mkdir()
    shutil.copyfile(DEMO_CLAIMS, profile / "claims.json")
    shutil.copyfile(DEMO_SYNONYMS, profile / "synonyms.json")
    (profile / "voice.md").write_text("<!-- c -->Tono propio.", encoding="utf-8")
    (profile / "salary_reference.toml").write_text(
        (DEMO_DIR / "salary_reference.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # The judge persona files are required in a configured profile (E4).
    (profile / "judge").mkdir()
    for name, text in render_judge_persona_files(OWNER_JUDGE_PERSONA).items():
        (profile / "judge" / name).write_text(text, encoding="utf-8")
    return profile


def test_from_settings_demo_profile_wires_the_demo_files(tmp_path: Path) -> None:
    service = JobService.from_settings(
        _settings(tmp_path, profile_dir=DEMO_DIR), db_path=tmp_path / "d.db"
    )

    assert service._masters_dir == DEMO_DIR / "masters"
    assert service._base_pdfs_dir == DEMO_DIR / "cv_base_pdfs"
    assert len(service._db.claims) > 0
    assert service._salary_table  # the demo table loaded
    assert service._voice_sample() != ""


def test_from_settings_profile_dir_overrides_every_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CVA_PROFILE_DIR", raising=False)
    profile = _profile(tmp_path)
    claims = json.loads((profile / "claims.json").read_text(encoding="utf-8"))
    (profile / "claims.json").write_text(json.dumps(claims[:3]), encoding="utf-8")
    (profile / "synonyms.json").write_text("{}", encoding="utf-8")
    (profile / "salary_reference.toml").write_text("", encoding="utf-8")

    service = JobService.from_settings(
        _settings(tmp_path, profile_dir=profile), db_path=tmp_path / "p.db"
    )

    assert len(service._db.claims) == 3
    assert service._salary_table == []
    assert service._voice_sample() == "Tono propio."
    assert service._masters_dir == profile / "masters"
    assert service._base_pdfs_dir == profile / "cv_base_pdfs"
    assert service.judge_persona == OWNER_JUDGE_PERSONA  # read from judge/*.md


def test_explicit_dirs_still_beat_the_resolver(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "e.db")
    from cva.claimsdb import load_claims
    from cva.match.extraction import ExtractionCache
    from cva.match.scorer import load_synonyms
    from cva.tracker import OfferRepository

    db = load_claims(DEMO_CLAIMS)
    service = JobService(
        repo=OfferRepository(conn),
        llm=None,  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        masters_dir=tmp_path / "m",
        base_pdfs_dir=tmp_path / "b",
    )

    assert service._masters_dir == tmp_path / "m"
    assert service._base_pdfs_dir == tmp_path / "b"


def test_suggested_searches_use_the_given_claims_file(tmp_path: Path) -> None:
    default = suggested_searches_payload()
    assert default == suggested_searches_payload(resolve_profile_paths(DEMO_DIR).claims)

    empty = tmp_path / "claims.json"
    empty.write_text("[]", encoding="utf-8")
    other = suggested_searches_payload(empty)

    assert other != default


def test_unknown_identity_claim_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """profile-extraction review: a typo in profile.toml identity_claim_id must
    not turn into an empty owner name in the letters; it fails at startup."""
    from cva.profile_identity import ProfileError

    monkeypatch.delenv("CVA_PROFILE_DIR", raising=False)
    profile = _profile(tmp_path)
    (profile / "profile.toml").write_text(
        '[identity]\nidentity_claim_id = "no-such-claim"\n', encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="no-such-claim"):
        JobService.from_settings(
            _settings(tmp_path, profile_dir=profile), db_path=tmp_path / "p.db"
        )
