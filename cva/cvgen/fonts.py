"""CV font families: which TTF files the render engine embeds.

``carlito`` (default) is Carlito, SIL OFL 1.1 and metric-compatible with
Calibri, shipped under ``cvgen/fonts/carlito/`` with its ``OFL.txt``.
``calibri`` is Microsoft's proprietary Calibri, read from
``cvgen/fonts/calibri*.ttf`` on machines that have it (never redistributed).
``Settings.cv_font`` selects one; this module is the only file list.
"""

from pathlib import Path
from typing import Final, Literal

from cva.cvgen.master import CvgenError

CvFont = Literal["carlito", "calibri"]
DEFAULT_CV_FONT: Final[CvFont] = "carlito"

FONT_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "cvgen" / "fonts"

# fpdf2 style ("" regular, "B", "I", "BI") -> file relative to FONT_DIR.
_FONT_FILES: Final[dict[CvFont, dict[str, str]]] = {
    "carlito": {
        "": "carlito/Carlito-Regular.ttf",
        "B": "carlito/Carlito-Bold.ttf",
        "I": "carlito/Carlito-Italic.ttf",
        "BI": "carlito/Carlito-BoldItalic.ttf",
    },
    "calibri": {
        "": "calibri.ttf",
        "B": "calibrib.ttf",
        "I": "calibrii.ttf",
        "BI": "calibriz.ttf",
    },
}


def font_files(font: CvFont = DEFAULT_CV_FONT) -> dict[str, Path]:
    """The four style files of ``font``; ``CvgenError`` when one is missing."""
    try:
        names = _FONT_FILES[font]
    except KeyError:
        raise CvgenError(
            f"unknown cv_font {font!r} (known: {', '.join(_FONT_FILES)})"
        ) from None
    files = {style: FONT_DIR / name for style, name in names.items()}
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise CvgenError(
            f"cv_font={font!r} needs font files that are not present: "
            f"{', '.join(missing)}"
        )
    return files
