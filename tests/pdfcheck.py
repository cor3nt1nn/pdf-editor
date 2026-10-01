"""Independent strict parse of the PDF files we write (pypdf, ``strict=True``)."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any, NamedTuple

#: pypdf (strict) warns about every incremental update whose first xref subsection does not
#: start at object 0 - valid PDF (ISO 32000-1 7.5.4, 7.5.6); M1 files have always had it.
INCREMENTAL_NOTE = "Xref table not zero-indexed"


class StrictRead(NamedTuple):
    reader: Any  # pypdf.PdfReader
    #: Number of image stamps (/Stamp with /IT /StampImage) whose images were decoded.
    stamps: int
    #: ``reader.get_fields()`` (None without an AcroForm).
    fields: dict[str, Any] | None


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def strict_read(path: str | Path, password: str | None = None) -> StrictRead:
    """Parse ``path`` with ``pypdf.PdfReader(strict=True)``: decrypt with ``password``,
    read every object of every xref section, decode every image stamp's appearance and
    image (and 8-bit /SMask) and read the form fields. Fails (AssertionError, or the
    exception pypdf raises) on any pypdf warning except :data:`INCREMENTAL_NOTE`."""
    import pypdf

    logger = logging.getLogger("pypdf")
    handler = _Records()
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            reader = pypdf.PdfReader(str(path), strict=True)
            if reader.is_encrypted:
                assert reader.decrypt(password or ""), "wrong password"
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
            fields = reader.get_fields()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    problems = [
        r.getMessage() for r in handler.records if not r.getMessage().startswith(INCREMENTAL_NOTE)
    ]
    assert not problems, problems
    return StrictRead(reader, stamps, fields)
