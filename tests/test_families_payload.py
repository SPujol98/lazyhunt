"""The bridge serves the role families when a built-in family carries its own
title filter (role-families T6, verifier advisory 3)."""

from pathlib import Path

from cva.bridge import families_payload
from cva.profile_prefs import load_preferences


def _prefs(tmp_path: Path, text: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "preferences.toml"
    path.write_text(text, encoding="utf-8")
    return load_preferences(path)


def test_builtin_family_with_its_own_title_filter_is_served(tmp_path: Path) -> None:
    prefs = _prefs(
        tmp_path,
        '[families.ai]\nlabel = "AI"\ndefault = true\n'
        '[families.ai.title_filter]\ninclude_groups = [["Data"]]\n'
        '[families.backend]\nlabel = "Backend"\n',
    )
    served = families_payload(prefs.families, prefs.title_filters)
    assert served is not None
    ai = next(f for f in served if f["id"] == "ai")
    assert ai["titleFilter"]["includeGroups"] == [["Data"]]


def test_title_filters_table_alone_still_serves_no_families(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path, '[title_filters.backend]\ninclude_groups = [["Python"]]\n')
    assert families_payload(prefs.families, prefs.title_filters) is None


def test_default_profile_serves_no_families(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path, "")
    assert families_payload(prefs.families, prefs.title_filters) is None
