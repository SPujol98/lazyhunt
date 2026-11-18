"""CV font selection: ``Settings.cv_font`` picks the family the engine embeds.

Carlito (SIL OFL 1.1, metric-compatible with Calibri) is the code default so
a public clone renders without Microsoft's proprietary Calibri files; the
owner keeps Calibri through ``cv_font = "calibri"``.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from cva.claimsdb import load_claims
from cva.config import Settings
from cva.cvgen import CvVariant, load_master
from cva.cvgen.engine import render
from cva.cvgen.fonts import font_files
from cva.cvgen.plan import neutral_plan
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS

_CALIBRI = Path(__file__).resolve().parents[1] / "cvgen" / "fonts" / "calibri.ttf"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
        **overrides,  # type: ignore[arg-type]
    )


def _embedded_fonts(pdf: Path) -> set[str]:
    fonts: set[str] = set()
    for page in PdfReader(str(pdf)).pages:
        resources = page["/Resources"]
        for key in resources["/Font"]:
            base = str(resources["/Font"][key].get_object().get("/BaseFont", ""))
            fonts.add(base.split("+")[-1])
    return fonts


def _render_demo(tmp_path: Path, font: str) -> Path:
    db = load_claims(DEMO_CLAIMS)
    master = load_master(CvVariant.AI_ES, DEMO_MASTERS, db)
    out = tmp_path / "cv.pdf"
    result = render(
        master,
        neutral_plan(CvVariant.AI_ES),
        out=out,
        db=db,
        font=font,  # type: ignore[arg-type]
    )
    assert result.violations == []
    assert result.pages == 1
    return result.pdf_path


def test_default_settings_render_carlito(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.cv_font == "carlito"

    files = font_files(settings.cv_font)
    assert set(files) == {"", "B", "I", "BI"}
    for path in files.values():
        assert path.is_file(), path
        assert path.name.startswith("Carlito-"), path
    assert files["BI"].name == "Carlito-BoldItalic.ttf"

    fonts = _embedded_fonts(_render_demo(tmp_path, settings.cv_font))
    assert fonts and all(font.startswith("Carlito") for font in fonts), fonts
    assert not any("Calibri" in font for font in fonts), fonts


@pytest.mark.skipif(
    not _CALIBRI.is_file(), reason="proprietary Calibri files are not in this clone"
)
def test_calibri_setting_renders_calibri(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cv_font="calibri")
    assert settings.cv_font == "calibri"

    files = font_files(settings.cv_font)
    assert {style: path.name for style, path in files.items()} == {
        "": "calibri.ttf",
        "B": "calibrib.ttf",
        "I": "calibrii.ttf",
        "BI": "calibriz.ttf",
    }

    fonts = _embedded_fonts(_render_demo(tmp_path, settings.cv_font))
    assert fonts and all("Calibri" in font for font in fonts), fonts
    assert not any("Carlito" in font for font in fonts), fonts


def test_invalid_cv_font_is_a_startup_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="cv_font"):
        _settings(tmp_path, cv_font="arial")
