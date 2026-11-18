"""Published-salary extractor (salary-estimate T1): pure text -> PublishedSalary."""

import pytest

from cva.match.salary import PublishedSalary, extract_published_salary


def _ok(text: str) -> PublishedSalary:
    found = extract_published_salary(text)
    assert found is not None, text
    return found


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("Salario: 28.000 - 34.000 € brutos/año", 28000, 34000),
        ("Ofrecemos entre 28.000 y 34.000 euros anuales", 28000, 34000),
        ("Salary range €45K - €55K", 45000, 55000),
        ("Rango salarial 45k-55k", 45000, 55000),
        ("Retribución: 30.000€ brutos anuales", 30000, 30000),
        ("55K €/yr - 75K €/yr", 55000, 75000),
        ("Pay: 45.5k - 52k EUR per year", 45500, 52000),
        ("De 28.000 a 34.000 € bruts/any", 28000, 34000),
        ("28,000 – 34,000 € gross per year", 28000, 34000),
    ],
)
def test_annual_formats(text: str, low: int, high: int) -> None:
    found = _ok(text)
    assert (found.min_eur, found.max_eur) == (low, high)
    assert found.period == "annual"
    assert found.snippet


def test_single_value_has_equal_bounds() -> None:
    found = _ok("Sueldo 30.000€")
    assert found.min_eur == found.max_eur == 30000


def test_monthly_defaults_to_12_payments_and_says_so() -> None:
    found = _ok("Salario: 2.000 - 2.500 € brutos al mes")
    assert found.period == "monthly"
    assert (found.min_eur, found.max_eur) == (24000, 30000)
    assert "x12" in found.note


def test_monthly_14_pagas_only_when_explicit() -> None:
    found = _ok("2.000 - 2.500 € brutos/mes en 14 pagas")
    assert (found.min_eur, found.max_eur) == (28000, 35000)
    assert "x14" in found.note


@pytest.mark.parametrize(
    "text",
    [
        "Más de 10 años de experiencia",
        "Founded in 2019, team of 150 people",
        "Crecimiento del 10 % anual",
        "Más de 10k usuarios activos",
        "Ronda de inversión de 5.000.000 €",
        "Bonus de 3.000 € anual",
        "Salary $120,000 - $150,000",
        "Hourly rate 25 - 30 €/h",
        "Sueldo neto 2.000 €",
        "",
        "Python, SQL, 3 años de experiencia, 2-5 años",
    ],
)
def test_rejects_non_salaries(text: str) -> None:
    assert extract_published_salary(text) is None


def test_ignores_similar_jobs_section() -> None:
    text = (
        "Backend Engineer\nDescripción del puesto\nBuscamos perfil Python.\n\n"
        "Más empleos\nData Engineer 60.000 - 70.000 € brutos/año\n"
    )
    assert extract_published_salary(text) is None


def test_prefers_first_salary_in_description_over_similar_jobs() -> None:
    text = (
        "Descripción\nSalario 28.000 - 34.000 € brutos/año\n\n"
        "Empleos similares\nOtro puesto 60.000 - 70.000 € brutos/año\n"
    )
    found = _ok(text)
    assert (found.min_eur, found.max_eur) == (28000, 34000)


def test_first_salary_wins() -> None:
    found = _ok("Salario 28.000 - 34.000 € brutos/año. Bonus aparte. Antes 40.000 - 50.000 €")
    assert found.min_eur == 28000


def test_dotnet_stack_near_salary_is_not_net_pay() -> None:
    text = "Stack C#/.NET y Python. Salario 40.000-50.000 € brutos anuales."
    found = extract_published_salary(text)
    assert found is not None and (found.min_eur, found.max_eur) == (40000, 50000)
