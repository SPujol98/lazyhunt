"""CV mirror tests (auto-triage cv-mirror spec): two-folder best-effort mirror.

Unit layer over ``tmp_path``: copy → ``nuevos/``, move → ``enviados/``,
unlink on discard; every failure is a WARNING + ``False``, never a raise.
The disabled mode (``None``/``""``) must do zero filesystem activity and
log zero warnings. EXDEV fallback and the unwritable-root warn-once are
POSIX-only (``os.replace`` errno semantics + ``chmod``).
"""

import errno
import logging
import os
from pathlib import Path

import pytest

from cva import mirror
from cva.mirror import MirrorPaths, mirror_root, on_discarded, on_generated, on_sent

PDF_BYTES = b"%PDF-1.7 fake pdf payload"

_posix_user = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="chmod-based unwritable root is POSIX-only and meaningless for root",
)


def _make_pdf(folder: Path, name: str = "Alex_Rivera_AI_Engineer.pdf") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / name
    pdf.write_bytes(PDF_BYTES)
    return pdf


@pytest.fixture()
def adaptadas(tmp_path: Path) -> Path:
    return tmp_path / "adaptadas"


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "mirror"


# --- disabled mode: zero fs, zero warnings ----------------------------------


def test_none_mirror_is_total_no_op(
    adaptadas: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: unset cv_mirror_dir → zero filesystem activity, zero warnings."""
    pdf = _make_pdf(adaptadas)
    untouched = tmp_path / "nowhere"
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        assert on_generated(None, pdf) is False
        assert on_sent(None, pdf) is False
        assert on_discarded(None, pdf) is False
        assert on_discarded(None, None) is False
    assert not untouched.exists()
    assert list(tmp_path.iterdir()) == [adaptadas]  # nothing new created
    assert caplog.records == []


def test_empty_string_disables_mirror(
    adaptadas: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: "" normalizes to disabled inside mirror.py."""
    pdf = _make_pdf(adaptadas)
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        assert on_generated("", pdf) is False
    assert list(tmp_path.iterdir()) == [adaptadas]
    assert caplog.records == []


def test_mirror_root_normalizes_blank_to_none() -> None:
    """mirror_root: None/""/whitespace → None; real paths pass through."""
    assert mirror_root(None) is None
    assert mirror_root("") is None
    assert mirror_root("   ") is None
    assert mirror_root("/tmp/cv") == Path("/tmp/cv")


# --- on_generated: copy → nuevos/ --------------------------------------------


def test_generated_copy_creates_dirs_on_demand(adaptadas: Path, root: Path) -> None:
    """Spec: PDF lands in <mirror>/nuevos/ with directories created on demand."""
    pdf = _make_pdf(adaptadas)
    assert on_generated(root, pdf) is True
    mirrored = root / "nuevos" / pdf.parent.name / pdf.name
    assert mirrored.is_file()
    assert mirrored.read_bytes() == PDF_BYTES


def test_generated_copy_leaves_original_and_overwrites_silently(
    adaptadas: Path, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Re-generate same offer → overwrite without warning; original stays."""
    pdf = _make_pdf(adaptadas)
    assert on_generated(root, pdf) is True
    pdf.write_bytes(b"%PDF-1.7 regenerated")
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        assert on_generated(root, pdf) is True
    assert pdf.read_bytes() == b"%PDF-1.7 regenerated"  # adaptadas/ untouched
    assert (root / "nuevos" / pdf.parent.name / pdf.name).read_bytes() == b"%PDF-1.7 regenerated"
    assert caplog.records == []


def test_generated_oserror_warns_and_returns_false(
    adaptadas: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: copy failure → WARNING, no exception, mirror skipped."""
    pdf = _make_pdf(adaptadas)
    (tmp_path / "mirror").write_text("a file blocks the mirror root")  # mkdir fails
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        assert on_generated(tmp_path / "mirror", pdf) is False
    assert caplog.records
    assert pdf.is_file()  # original untouched


# --- on_sent: move → enviados/ -----------------------------------------------


def test_sent_moves_nuevos_to_enviados(adaptadas: Path, root: Path) -> None:
    """Spec: file exists in enviados/ and no longer in nuevos/."""
    pdf = _make_pdf(adaptadas)
    assert on_generated(root, pdf) is True
    assert on_sent(root, pdf) is True
    assert (root / "enviados" / pdf.parent.name / pdf.name).is_file()
    assert not (root / "nuevos" / pdf.parent.name / pdf.name).exists()


def test_sent_exdev_falls_back_to_copy_and_unlink(
    adaptadas: Path,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D6: cross-device mount → os.replace raises EXDEV → copy2 + unlink."""

    def _exdev(src: object, dst: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(mirror.os, "replace", _exdev)
    pdf = _make_pdf(adaptadas)
    assert on_generated(root, pdf) is True
    assert on_sent(root, pdf) is True
    assert (root / "enviados" / pdf.parent.name / pdf.name).read_bytes() == PDF_BYTES
    assert not (root / "nuevos" / pdf.parent.name / pdf.name).exists()


def test_sent_missing_source_warns_and_returns_false(
    adaptadas: Path, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: source moved manually → warning; caller continues normally."""
    pdf = _make_pdf(adaptadas)
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        assert on_sent(root, pdf) is False
    assert caplog.records


# --- on_discarded: unlink from nuevos/ ---------------------------------------


def test_discarded_removes_mirror_copy_only(adaptadas: Path, root: Path) -> None:
    """Spec: gone from nuevos/; the adaptadas/ original stays."""
    pdf = _make_pdf(adaptadas)
    assert on_generated(root, pdf) is True
    assert on_discarded(root, pdf) is True
    assert not (root / "nuevos" / pdf.parent.name / pdf.name).exists()
    assert pdf.is_file()


def test_discarded_absent_is_silent_no_op(
    adaptadas: Path, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: no mirrored file → clean no-op, no warning (vs on_sent)."""
    pdf = _make_pdf(adaptadas)
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        assert on_discarded(root, pdf) is False
        assert on_discarded(root, None) is False
    assert caplog.records == []


# --- unwritable root: warn once per session ----------------------------------


@_posix_user
def test_unwritable_root_warns_once(
    adaptadas: Path, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """D5: the root itself unwritable → ONE warning per root per session."""
    pdf = _make_pdf(adaptadas)
    assert on_generated(root, pdf) is True
    folder = root / "nuevos" / pdf.parent.name
    folder.chmod(0o500)
    (root / "nuevos").chmod(0o500)
    fresh = _make_pdf(adaptadas, "Alex_Rivera_AI_Engineer_oferta_2.pdf")
    try:
        with caplog.at_level(logging.WARNING, logger="cva.mirror"):
            assert on_sent(root, pdf) is False
            assert on_sent(root, pdf) is False
            assert on_generated(root, fresh) is False
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
    finally:
        (root / "nuevos").chmod(0o700)
        folder.chmod(0o700)


@_posix_user
def test_warn_once_is_per_root(
    adaptadas: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A different unwritable root gets its own single warning."""
    pdf = _make_pdf(adaptadas)
    other = tmp_path / "other-mirror"
    other.mkdir()
    other.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="cva.mirror"):
            assert on_generated(other, pdf) is False
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
    finally:
        other.chmod(0o700)


# --- MirrorPaths value object -------------------------------------------------


def test_mirror_paths_folder_pair() -> None:
    """MirrorPaths(root) resolves the nuevos/enviados pair under the root."""
    paths = MirrorPaths(Path("/srv/cv"))
    assert paths.root == Path("/srv/cv")
    assert paths.nuevos == Path("/srv/cv/nuevos")
    assert paths.enviados == Path("/srv/cv/enviados")


# --- collision-free layout: <folder>/<name> ------------------------------------


def test_same_filename_in_different_offer_folders_does_not_collide(
    tmp_path: Path, root: Path
) -> None:
    """Two offers share the generic file name; the mirror keeps both."""
    pdf_a = _make_pdf(tmp_path / "adaptadas" / "1_Acme")
    pdf_b = _make_pdf(tmp_path / "adaptadas" / "2_Globex")
    pdf_b.write_bytes(b"%PDF-1.7 other")
    assert on_generated(root, pdf_a) is True
    assert on_generated(root, pdf_b) is True
    assert (root / "nuevos" / "1_Acme" / pdf_a.name).read_bytes() == PDF_BYTES
    assert (root / "nuevos" / "2_Globex" / pdf_b.name).read_bytes() == b"%PDF-1.7 other"
    assert on_sent(root, pdf_a) is True
    assert (root / "enviados" / "1_Acme" / pdf_a.name).is_file()
    assert (root / "nuevos" / "2_Globex" / pdf_b.name).is_file()
    assert on_discarded(root, pdf_b) is True
    assert not (root / "nuevos" / "2_Globex" / pdf_b.name).exists()
    assert (root / "enviados" / "1_Acme" / pdf_a.name).is_file()
