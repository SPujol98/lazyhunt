"""profile-extraction E2: identity from profile.toml + manifest-driven base CVs.

Golden: with no profile.toml every generated/delivered file name and the
identity claim contract equal what the hard-coded constants produced before.
"""

import asyncio
import dataclasses
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from cva.claimsdb import load_claims
from cva.cvgen import validators
from cva.cvgen.base_pdfs import deliverable_variants
from cva.cvgen.engine import generic_output_path, output_path, tailored_output_path
from cva.cvgen.master import CvgenError, CvVariant
from cva.match.cover_letter import owner_name
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.profile_identity import (
    DEFAULT_IDENTITY,
    Identity,
    ProfileError,
    load_identity,
)
from cva.profile_paths import resolve_profile_paths
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_BASE_PDFS, DEMO_CLAIMS, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent
CLAIMS = load_claims(DEMO_CLAIMS)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "profile.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- identity loading -------------------------------------------------------


def test_missing_profile_toml_gives_defaults(tmp_path: Path) -> None:
    assert load_identity(tmp_path / "profile.toml") == DEFAULT_IDENTITY


def test_identity_table_overrides_each_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[identity]\nname = "Ana Demo"\nfile_slug = "Ana_Demo"\n'
        'identity_claim_id = "me-contact"\n',
    )

    assert load_identity(path) == Identity(
        name="Ana Demo", file_slug="Ana_Demo", identity_claim_id="me-contact"
    )


def test_partial_identity_keeps_other_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, '[identity]\nfile_slug = "Ana_Demo"\n')

    got = load_identity(path)

    assert got.file_slug == "Ana_Demo"
    assert got.identity_claim_id == "identity-contact"
    assert got.name is None


@pytest.mark.parametrize(
    "body",
    [
        '[identity]\nfile_slug = "Ana Demo/../x"\n',
        '[identity]\nfile_slug = ""\n',
        "[identity]\nfile_slug = 3\n",
        '[identity]\nidentity_claim_id = ""\n',
        '[identity]\nname = ["a"]\n',
        "identity = 3\n",
        "not = [valid",
    ],
)
def test_invalid_profile_toml_fails_fast(tmp_path: Path, body: str) -> None:
    with pytest.raises(ProfileError):
        load_identity(_write(tmp_path, body))


def test_default_contact_claim_ids_equal_the_old_constant() -> None:
    assert DEFAULT_IDENTITY.contact_claim_ids == frozenset(
        {
            "identity-contact",
            "identity-phone",
            "identity-contact_en",
            "identity-phone_en",
        }
    )


def test_contact_claim_ids_follow_a_custom_claim_id() -> None:
    custom = Identity(name=None, file_slug="X", identity_claim_id="me-contact")

    assert custom.contact_claim_ids == frozenset(
        {"me-contact", "me-contact_en", "me-phone", "me-phone_en"}
    )


# --- golden: generated file names unchanged ---------------------------------


def test_owner_slug_param_changes_every_output_name(tmp_path: Path) -> None:
    assert (
        generic_output_path(CvVariant.AI_EN, 7, "Acme", tmp_path, owner_slug="Ana_Demo")
        == tmp_path / "7_Acme" / "Ana_Demo_AI_Engineer.pdf"
    )
    assert output_path("AI Engineer", "Acme", tmp_path, owner_slug="Ana_Demo") == (
        tmp_path / "Ana_Demo_AI_Engineer_Acme.pdf"
    )
    assert tailored_output_path("Dev", tmp_path, owner_slug="Ana_Demo") == (
        tmp_path / "Ana_Demo_Dev.pdf"
    )


# --- owner_name -------------------------------------------------------------


def test_owner_name_default_matches_old_behaviour() -> None:
    assert owner_name(CLAIMS) == "Alex Rivera"
    assert owner_name(CLAIMS, DEFAULT_IDENTITY) == "Alex Rivera"


def test_owner_name_override_beats_the_claim() -> None:
    identity = Identity(
        name="Ana Demo", file_slug="A", identity_claim_id="identity-contact"
    )

    assert owner_name(CLAIMS, identity) == "Ana Demo"


def test_owner_name_reads_the_configured_claim_id() -> None:
    identity = Identity(
        name=None, file_slug="A", identity_claim_id="identity-contact_en"
    )

    assert owner_name(CLAIMS, identity) == (
        CLAIMS.by_id["identity-contact_en"].text.split("·", 1)[0].strip()
    )
    missing = Identity(name=None, file_slug="A", identity_claim_id="nope")
    assert owner_name(CLAIMS, missing) == ""


# --- check_contact reads the identity ids -----------------------------------


def _leaf(claim_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(claim_id=claim_id, text=text)


def test_check_contact_checks_only_the_identity_claim_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validators, "extract_text", lambda _p: "nothing here")
    master = SimpleNamespace(
        header=SimpleNamespace(
            name=_leaf("me-contact", "Ana Demo"),
            contact=[_leaf("other-claim", "Elsewhere")],
            tagline=None,
        )
    )

    # Default identity: "me-contact" is not an identity claim -> nothing checked.
    assert validators.check_contact("x.pdf", master) == []  # type: ignore[arg-type]
    # Configured identity: its tokens must survive into the render.
    custom = Identity(name=None, file_slug="A", identity_claim_id="me-contact")
    problems = validators.check_contact("x.pdf", master, custom)  # type: ignore[arg-type]
    assert problems and "ana" in problems[0] and "demo" in problems[0]
    assert "elsewhere" not in problems[0]


# --- service uses the profile identity --------------------------------------


def _service(tmp_path: Path, **kwargs: object) -> tuple[JobService, OfferRepository]:
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    svc = JobService(
        repo=repo,
        llm=None,  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        output_dir=tmp_path / "adaptadas",
        **kwargs,  # type: ignore[arg-type]
    )
    return svc, repo


def _offer(repo: OfferRepository) -> int:
    return repo.upsert_offer(
        OfferInput(
            title="Backend Dev",
            company="Acme",
            url="https://www.linkedin.com/jobs/view/4459804598/",
            text="We are hiring a backend developer. Python and REST APIs are required.",
            source="bridge-inline",
            profile="backend",
        )
    )


def test_service_default_delivery_name_is_unchanged(tmp_path: Path) -> None:
    # Legacy (unset profile_dir) identity, delivered from the demo base PDFs.
    svc, repo = _service(
        tmp_path,
        profile_paths=resolve_profile_paths(None),
        base_pdfs_dir=DEMO_BASE_PDFS,
    )

    pdf = asyncio.run(svc.generate_cv(_offer(repo)))

    assert pdf.name == f"{DEFAULT_IDENTITY.file_slug}_Backend_Developer.pdf"


def test_service_uses_the_profile_file_slug(tmp_path: Path) -> None:
    profile_toml = _write(tmp_path, '[identity]\nfile_slug = "Ana_Demo"\n')
    paths = dataclasses.replace(resolve_profile_paths(), profile_toml=profile_toml)
    svc, repo = _service(tmp_path, profile_paths=paths, base_pdfs_dir=DEMO_BASE_PDFS)

    pdf = asyncio.run(svc.generate_cv(_offer(repo)))

    assert pdf.name == "Ana_Demo_Backend_Developer.pdf"


# --- manifest-driven base CVs -----------------------------------------------

_BASE = DEMO_BASE_PDFS


def test_real_manifest_gives_exactly_the_five_deliverable_variants() -> None:
    assert deliverable_variants(_BASE) == frozenset(
        {
            CvVariant.AI_ES,
            CvVariant.AI_EN,
            CvVariant.AI_CA,
            CvVariant.BACKEND_ES,
            CvVariant.BACKEND_EN,
        }
    )
    assert deliverable_variants() == deliverable_variants(_BASE)


def test_manifest_file_names_are_the_current_ones() -> None:
    manifest = json.loads((_BASE / "manifest.json").read_text(encoding="utf-8"))
    assert {k: v["file"] for k, v in manifest["variants"].items()} == {
        "AI_ES": "CV_AI_Engineer_Alex_Rivera.pdf",
        "AI_EN": "CV_AI_Engineer_Alex_Rivera_EN.pdf",
        "AI_CA": "CV_AI_Engineer_Alex_Rivera_CA.pdf",
        "BACKEND_ES": "CV_Backend_Developer_Python_Alex_Rivera.pdf",
        "BACKEND_EN": "CV_Backend_Developer_Python_Alex_Rivera_EN.pdf",
    }


def test_deliverable_variants_follow_a_custom_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"variants": {"AI_EN": {"file": "a.pdf", "sha256": "0"}}}),
        encoding="utf-8",
    )

    assert deliverable_variants(tmp_path) == frozenset({CvVariant.AI_EN})


def test_deliverable_variants_reject_a_missing_or_bad_manifest(tmp_path: Path) -> None:
    with pytest.raises(CvgenError, match="manifiesto"):
        deliverable_variants(tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"variants": {"XX_YY": {"file": "a.pdf", "sha256": "0"}}}),
        encoding="utf-8",
    )
    with pytest.raises(CvgenError, match="XX_YY"):
        deliverable_variants(tmp_path)


def test_copy_of_real_manifest_dir_round_trips(tmp_path: Path) -> None:
    shutil.copyfile(_BASE / "manifest.json", tmp_path / "manifest.json")

    assert deliverable_variants(tmp_path) == deliverable_variants(_BASE)


def test_require_identity_claim_names_the_missing_claim() -> None:
    from cva.claimsdb import load_claims
    from cva.profile_identity import require_identity_claim

    db = load_claims(DEMO_CLAIMS)
    require_identity_claim(DEFAULT_IDENTITY, db)  # exists: no error
    with pytest.raises(ProfileError, match="typo-contact"):
        require_identity_claim(Identity(identity_claim_id="typo-contact"), db)
