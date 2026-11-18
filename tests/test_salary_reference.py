"""Salary reference table + lookup + card line (salary-estimate T2).

Tests use a fixture table written to tmp_path, never the real data file.
"""

from pathlib import Path

import pytest

from cva.match.salary_reference import (
    ReferenceRange,
    infer_city,
    infer_level,
    load_reference_table,
    salary_line,
)
from cva.notifier import (
    borderline_message,
    breakdown_message,
    cv_keyboard,
    cv_ready_message,
    judge_breakdown_message,
    sent_keyboard,
    with_salary_line,
)
from tests.demo import DEMO_DIR

FIXTURE = """
[[range]]
branch = "AI"
level = "junior"
city = "barcelona"
low = 28000
median = 31000
high = 34000
ask = 32000
sources = ["Fixture Survey 2026 (n=120)", "Other"]
confidence = "medium"
as_of = "2026-10"

[[range]]
branch = "BACKEND"
level = "mid"
city = "spain_remote"
low = 36000
median = 41500
high = 48000
ask = 43000
sources = ["Fixture Survey 2026"]
confidence = "low"
as_of = "2026-10"
"""


@pytest.fixture()
def table(tmp_path: Path) -> list[ReferenceRange]:
    path = tmp_path / "ref.toml"
    path.write_text(FIXTURE, encoding="utf-8")
    return load_reference_table(path)


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_reference_table(tmp_path / "nope.toml") == []


def test_placeholder_rows_are_never_loaded(tmp_path: Path) -> None:
    path = tmp_path / "ref.toml"
    path.write_text(FIXTURE + "\n[[range]]\nbranch=\"AI\"\nlevel=\"mid\"\ncity=\"madrid\"\n"
        "low=0\nmedian=0\nhigh=0\nask=0\nsources=[\"x\"]\nconfidence=\"low\"\n"
        "as_of=\"0000-00\"\nplaceholder=true\n", encoding="utf-8")
    assert len(load_reference_table(path)) == 2


def test_demo_table_rows_are_well_formed() -> None:
    rows = load_reference_table(DEMO_DIR / "salary_reference.toml")
    assert rows
    assert all(0 < r.low <= r.median <= r.high and r.sources for r in rows)


def test_load_parses_rows(table: list[ReferenceRange]) -> None:
    assert len(table) == 2
    assert table[0].branch == "AI" and table[0].ask == 32000


@pytest.mark.parametrize(
    ("title", "text", "level"),
    [
        ("Junior AI Engineer", "", "junior"),
        ("AI Engineer", "Buscamos 0-2 años de experiencia", "junior"),
        ("AI Engineer", "", "junior"),
        ("AI Engineer (Trainee)", "", "junior"),
        ("Becario IA", "", "junior"),
        ("AI Engineer", "Requisitos: 2-5 años de experiencia en Python", "mid"),
        ("AI Engineer", "3+ years of experience", "mid"),
        ("Mid-level Backend Developer", "", "mid"),
        ("Senior AI Engineer", "", None),
        ("Tech Lead Backend", "", None),
        ("Staff Engineer", "", None),
        ("Principal ML Engineer", "", None),
        ("AI Engineer", "Más de 5 años de experiencia", None),
    ],
)
def test_infer_level(title: str, text: str, level: str | None) -> None:
    assert infer_level(title, text) == level


@pytest.mark.parametrize(
    ("text", "city", "detected"),
    [
        ("Ubicación: Barcelona, Cataluña", "barcelona", True),
        ("Oficina en Madrid (híbrido)", "madrid", True),
        ("Trabajo 100% remoto desde España", "spain_remote", True),
        ("Fully remote position", "spain_remote", True),
        ("Madrid o Barcelona, modelo híbrido", "madrid", True),
        ("Sin ubicación indicada", "spain_remote", False),
    ],
)
def test_infer_city(text: str, city: str, detected: bool) -> None:
    assert infer_city("AI Engineer", text) == (city, detected)


def test_reference_line(table: list[ReferenceRange]) -> None:
    line = salary_line(
        title="Junior AI Engineer", text="Barcelona hybrid", perfil="ai", table=table
    )
    assert line == (
        "💶 Referencia: 28–34k (mediana 31k) · AI junior · Barcelona · "
        "fuente: Fixture Survey 2026 (n=120), 2026-10 · pide ~32k"
    )


def test_reference_line_null_profile_is_ai(table: list[ReferenceRange]) -> None:
    line = salary_line(title="Engineer", text="Barcelona", perfil=None, table=table)
    assert "AI junior" in line


def test_reference_line_flags_undetected_city_and_low_confidence(
    table: list[ReferenceRange],
) -> None:
    line = salary_line(
        title="Backend Engineer", text="2-5 años de experiencia en Python", perfil="backend", table=table
    )
    assert "Backend mid" in line
    assert "ubicación no detectada" in line
    assert "mediana 41,5k" in line
    assert "confianza baja" in line


def test_senior_shows_no_reference(table: list[ReferenceRange]) -> None:
    assert (
        salary_line(title="Senior AI Engineer", text="Barcelona", perfil="ai", table=table)
        == ""
    )


def test_no_matching_row_no_line(table: list[ReferenceRange]) -> None:
    assert salary_line(title="AI Engineer", text="Madrid", perfil="ai", table=table) == ""
    assert salary_line(title="AI Engineer", text="Madrid", perfil="ai", table=[]) == ""


def test_published_salary_wins_over_reference(table: list[ReferenceRange]) -> None:
    line = salary_line(
        title="Junior AI Engineer",
        text="Barcelona. Salario: 28.000 - 34.000 € brutos/año",
        perfil="ai",
        table=table,
    )
    assert line == "💶 Salario publicado: 28.000–34.000 € brutos/año"


def test_published_monthly_states_conversion(table: list[ReferenceRange]) -> None:
    line = salary_line(
        title="AI Engineer", text="2.000 - 2.500 € brutos al mes", perfil="ai", table=table
    )
    assert line.startswith("💶 Salario publicado: 24.000–30.000 € brutos/año")
    assert "mensual x12" in line


def test_with_salary_line_appends_and_respects_limit() -> None:
    assert with_salary_line("card", "") == "card"
    assert with_salary_line("card", "💶 X") == "card\n\n💶 X"
    big = "a" * 4090
    assert with_salary_line(big, "💶 Salario publicado: 1 €") == big


def test_cards_carry_salary_line(table: list[ReferenceRange]) -> None:
    from cva.match import MatchResult

    line = "💶 Salario publicado: 30.000 € brutos/año"
    result = MatchResult(score=70, threshold=65, breakdown=[], stack_wall=None)
    assert breakdown_message(result, 1, salary_line=line).endswith(line)
    assert borderline_message(
        score=60, threshold=65, title="t", company="c", url="u", salary_line=line
    ).endswith(line)
    assert judge_breakdown_message(result, None, 1, salary_line=line).endswith(line)
    assert cv_ready_message(Path("x.pdf"), salary_line=line).endswith(line)
    # no line -> byte-identical to the old card
    assert breakdown_message(result, 1) == breakdown_message(result, 1, salary_line="")


def test_salary_button_on_cards() -> None:
    for markup in (cv_keyboard(7), sent_keyboard(7)):
        data = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "sal:7" in data


@pytest.mark.parametrize(
    "text",
    [
        "Buscamos desarrollador Python, 1 y 3 años de experiencia.",
        "Entre 1 y 3 años de experiencia desarrollando software.",
        "Between 1 and 3 years of experience with Python.",
    ],
)
def test_year_ranges_with_y_and_take_the_lower_bound(text: str) -> None:
    """'1 y 3 años' / 'between 1 and 3 years' is a 1-3 range → junior."""
    assert infer_level("Desarrollador Python", text) == "junior"
