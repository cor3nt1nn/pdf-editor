"""M4 review 5: the files we write parse strictly with an independent reader (pypdf):
incremental and full saves, plain and AES-256, with signatures (and objects freed by the
orphan cleanup)."""

from __future__ import annotations

import logging
import warnings

import fixtures
import pymupdf
import pytest
from fixtures import A4, PASSWORD
from PySide6.QtCore import QRectF

from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.commands import AddAnnotCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.signature import ImageData

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("cryptography")  # AES-256 decryption

RECT = QRectF(100, 100, 200, 80)
#: pypdf (strict) warns about every incremental update whose first xref subsection does not
#: start at object 0 - valid PDF (ISO 32000-1 7.5.4, 7.5.6); M1 files have always had it.
INCREMENTAL_NOTE = "Xref table not zero-indexed"


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


def _strict_read(path, caplog) -> int:
    """Parse ``path`` with ``pypdf.PdfReader(strict=True)``, read every object of every
    xref section and decode every signature image; returns the number of image stamps.
    Fails on any pypdf warning."""
    caplog.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        reader = pypdf.PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            assert reader.decrypt(PASSWORD)
        for generation, objects in reader.xref.items():
            for num in objects:
                reader.get_object(pypdf.generic.IndirectObject(num, generation, reader))
        stamps = 0
        for page in reader.pages:
            for ref in page.get("/Annots") or []:
                annot = ref.get_object()
                if annot.get("/Subtype") != "/Stamp" or annot.get("/IT") != "/StampImage":
                    continue
                form = annot["/AP"]["/N"].get_object()
                form.get_data()
                for xobject in form["/Resources"]["/XObject"].values():
                    image = xobject.get_object()
                    w, h = int(image["/Width"]), int(image["/Height"])
                    smask = image.get("/SMask")
                    if smask is not None and image.get("/Filter") in (None, "/FlateDecode"):
                        smask = smask.get_object()
                        assert len(image.get_data()) >= w * h * 3
                        if smask.get("/BitsPerComponent") == 8:  # ours (MuPDF's: 1 bit)
                            assert len(smask.get_data()) >= w * h
                stamps += 1
    problems = [
        r
        for r in caplog.records
        if r.name.startswith("pypdf")
        and r.levelno >= logging.WARNING
        and not r.getMessage().startswith(INCREMENTAL_NOTE)
    ]
    assert not problems, [r.getMessage() for r in problems]
    return stamps


@pytest.mark.parametrize("kind", ["blank", "signed", "aes256"])
def test_saved_files_parse_strictly(qapp, tmp_path, caplog, kind) -> None:
    caplog.set_level(logging.WARNING)
    path = _make(kind, tmp_path / f"{kind}.pdf")
    doc = PdfDocument.open(path, password=PASSWORD if kind == "aes256" else None)
    try:
        before = _strict_read(path, caplog)
        doc.add_annot(_spec())
        doc.add_annot(_spec(RECT.translated(0, 200)))
        undone = AddAnnotCommand(doc, _spec(RECT.translated(0, 400)))
        undone.apply_now()
        undone.undo()  # freed by the orphan cleanup: free entries in the update
        doc.save()
        assert doc.can_save_incrementally()  # the incremental save was used
        assert _strict_read(path, caplog) == before + 2
        moved = doc.annots(0)[-1]
        doc.update_annot(0, moved.name, rect=moved.rect.translated(5, 5))
        doc.save()  # a second incremental update on top
        assert _strict_read(path, caplog) == before + 2
        doc.save(force_full=True)
        assert _strict_read(path, caplog) == before + 2
    finally:
        doc.close()
