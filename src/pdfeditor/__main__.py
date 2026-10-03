from __future__ import annotations

import sys

# The OCR worker process (M8) never starts Qt: dispatch it before importing the app.
if __name__ == "__main__" and sys.argv[1:2] == ["--ocr-worker"]:
    from pdfeditor.core.ocr_worker import main as ocr_worker_main

    sys.exit(ocr_worker_main())

from pdfeditor.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
