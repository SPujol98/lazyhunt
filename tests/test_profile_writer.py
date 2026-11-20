"""onboarding-v2 T2 (S7): validated, atomic writers for ``preferences.toml`` and
``profile.toml``.

A document (the mapping ``tomllib`` returns) is emitted as TOML, parsed back,
validated by the strict loaders and only then written over the file through a
temporary file + ``os.replace``; the previous file is kept as ``<name>.bak``.
Round-trip: ``load(write(x)) == load(x)`` and the written text parses back to
the very same document.
"""

import random
import tomllib
from pathlib import Path
from typing import Any

import pytest

from cva.profile_identity import Identity, ProfileError, load_identity
from cva.profile_prefs import load_preferences
from cva.profile_writer import (
    read_document,
    toml_document,
    write_preferences,
    write_profile,
)
from tests.demo import DEMO_DIR

_TRICKY = 'He said "hi" \\ path C:\\x\n\ttab é ñ 日本 \x01 \x7f end'


def _doc(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# --- round trip ----------------------------------------------------------------


def test_demo_preferences_round_trip(tmp_path: Path) -> None:
    source = DEMO_DIR / "preferences.toml"
    target = tmp_path / "preferences.toml"

    returned = write_preferences(target, _doc(source))

    assert _doc(target) == _doc(source)
    assert load_preferences(target) == load_preferences(source)
    assert returned == load_preferences(source)


def test_demo_profile_round_trip(tmp_path: Path) -> None:
    source = DEMO_DIR / "profile.toml"
    target = tmp_path / "profile.toml"

    returned = write_profile(target, _doc(source))

    assert _doc(target) == _doc(source)
    assert load_identity(target) == load_identity(source) == returned


_EXAMPLES: list[dict[str, Any]] = [
    {},
    {
        "search": {
            "work_modes": ["remote", "hybrid"],
            "level": "senior",
            "min_salary": 45000,
            "currency": "EUR",
        },
        "location": {"home_region": "catalonia", "city": "Girona", "country": "España"},
    },
    {
        "gates": {"allowed_languages": ["es", "en"], "flag_english_above_c1": False},
        "title_filters": {
            "company_blocklist": ["Acme Staffing", _TRICKY],
            "ai": {
                "enabled": True,
                "include_groups": [["AI", "LLM"], ["Engineer"]],
                "exclude_terms": ["Senior"],
            },
        },
        "cover_letter": {
            "career_change": _TRICKY,
            "branch_emphasis": {"AI": "ai text", "FRONTEND": "front text"},
        },
        "families": {
            "frontend": {
                "label": "Front-end & UI",
                "languages": ["es", "en"],
                "title_terms": ["Frontend", "React"],
                "threshold": 55,
                "search_roles": ["Frontend Developer"],
                "default": True,
                "title_filter": {"include_groups": [["Frontend"]]},
            },
            "ai": {},
        },
    },
]


@pytest.mark.parametrize("doc", _EXAMPLES, ids=["empty", "search", "families"])
def test_generated_examples_round_trip(tmp_path: Path, doc: dict[str, Any]) -> None:
    target = tmp_path / "preferences.toml"
    source = tmp_path / "source.toml"
    source.write_text(toml_document(doc), encoding="utf-8")

    write_preferences(target, doc)

    assert _doc(target) == doc
    assert load_preferences(target) == load_preferences(source)


def test_any_string_survives_the_emitter() -> None:
    """Seeded fuzz over every code point class the emitter escapes."""
    rng = random.Random(20261119)
    alphabet = [chr(c) for c in range(0x80)] + ["é", "ñ", "€", "日", "\u2028"]
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        key = "".join(rng.choice(alphabet[32:]) for _ in range(rng.randint(1, 8)))
        doc = {"t": {"value": text, key: [text, "x"]}}
        assert tomllib.loads(toml_document(doc)) == doc


def test_read_document_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_document(tmp_path / "preferences.toml") == {}
    assert read_document(DEMO_DIR / "preferences.toml") == _doc(
        DEMO_DIR / "preferences.toml"
    )


# --- validation before replacing ------------------------------------------------


def _existing(tmp_path: Path, name: str) -> tuple[Path, bytes]:
    path = tmp_path / name
    path.write_bytes((DEMO_DIR / name).read_bytes())
    return path, path.read_bytes()


@pytest.mark.parametrize(
    "doc",
    [
        {"nope": {"x": 1}},
        {"search": {"level": "lead"}},
        {"search": {"min_salary": -5}},
        {"families": {"Bad-Id": {}}},
        {"location": {"home_region": "atlantis"}},
    ],
)
def test_invalid_preferences_leave_the_file_untouched(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    path, before = _existing(tmp_path, "preferences.toml")

    with pytest.raises(ProfileError):
        write_preferences(path, doc)

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["preferences.toml"]


@pytest.mark.parametrize(
    "doc",
    [
        {"identity": {"file_slug": "Ana Demo"}},
        {"identity": {"name": ""}},
        {"identity": {"nickname": "Ana"}},
        {"identity": "Ana"},
        {"contact": {"email": "a@b.c"}},
    ],
)
def test_invalid_profile_leaves_the_file_untouched(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    path, before = _existing(tmp_path, "profile.toml")

    with pytest.raises(ProfileError):
        write_profile(path, doc)

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["profile.toml"]


@pytest.mark.parametrize(
    "value",
    [1.5, None, [{"a": 1}], [1, 2], [["a", 1]], (lambda: 1)],
    ids=["float", "none", "list-of-tables", "ints", "mixed-group", "callable"],
)
def test_values_outside_the_schema_types_are_refused(
    tmp_path: Path, value: object
) -> None:
    with pytest.raises(ProfileError, match="cannot write"):
        write_preferences(tmp_path / "preferences.toml", {"search": {"x": value}})
    assert not (tmp_path / "preferences.toml").exists()


# --- atomic replace + backup ----------------------------------------------------


def test_the_previous_file_is_kept_as_bak(tmp_path: Path) -> None:
    path, before = _existing(tmp_path, "preferences.toml")
    doc = _doc(path)
    doc["search"] = {"work_modes": ["remote"]}

    write_preferences(path, doc)

    assert (tmp_path / "preferences.toml.bak").read_bytes() == before
    assert load_preferences(path).search.work_modes == ("remote",)
    # Hand comments are not preserved: the file is rebuilt from the document.
    assert "#" not in path.read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "preferences.toml",
        "preferences.toml.bak",
    ]


def test_a_new_file_has_no_bak(tmp_path: Path) -> None:
    path = tmp_path / "profile.toml"
    assert write_profile(path, {"identity": {"name": "Ana Demo"}}) == Identity(
        name="Ana Demo"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["profile.toml"]
