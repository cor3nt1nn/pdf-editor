"""M4 review 5: the files we write parse strictly with an independent reader (pypdf):
incremental and full saves, plain and AES-256, with signatures (and objects freed by the
orphan cleanup)."""

from __future__ import annotations

import fixtures
import pymupdf
import pytest
from fixtures import A4, PASSWORD
from pdfcheck import strict_read
from PySide6.QtCore import QRectF

from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.commands import AddAnnotCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.signature import ImageData

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("cryptography")  # AES-256 decryption

RECT = QRectF(100, 100, 200, 80)


def _spec(rect: QRectF = RECT) -> AnnotSpec:
    data = ImageData(*fixtures.sig_asym_samples())
    return AnnotSpec(0, AnnotKind.SIGNATURE, "", 11.0, (0.0, 0.0, 0.0), rect, image=data)


def _make(kind: str, path):
    if kind == "signed":
        return fixtures.make_signed_pdf(path)
    pdf = pymupdf.open()
    pdf.new_page(width=A4[0], height=A4[1])
    if kind == "aes256":
        pdf.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=PASSWORD,
            owner_pw="owner-" + PASSWORD,
            permissions=-1,
        )
    else:
        pdf.save(path)
    pdf.close()
    return path


def _strict_read(path) -> int:
    """Strict pypdf parse of ``path`` (see :func:`pdfcheck.strict_read`); returns the
    number of image stamps."""
    return strict_read(path, PASSWORD).stamps


@pytest.mark.parametrize("kind", ["blank", "signed", "aes256"])
def test_saved_files_parse_strictly(qapp, tmp_path, kind) -> None:
    path = _make(kind, tmp_path / f"{kind}.pdf")
    doc = PdfDocument.open(path, password=PASSWORD if kind == "aes256" else None)
    try:
        before = _strict_read(path)
        doc.add_annot(_spec())
        doc.add_annot(_spec(RECT.translated(0, 200)))
        undone = AddAnnotCommand(doc, _spec(RECT.translated(0, 400)))
        undone.apply_now()
        undone.undo()  # freed by the orphan cleanup: free entries in the update
        doc.save()
        assert doc.can_save_incrementally()  # the incremental save was used
        assert _strict_read(path) == before + 2
        moved = doc.annots(0)[-1]
        doc.update_annot(0, moved.name, rect=moved.rect.translated(5, 5))
        doc.save()  # a second incremental update on top
        assert _strict_read(path) == before + 2
        doc.save(force_full=True)
        assert _strict_read(path) == before + 2
    finally:
        doc.close()
