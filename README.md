# PDF Editor

Free, open-source (AGPL-3.0) PDF editor for Windows, bilingual French/English.

Goals: fill PDF forms, fill flat forms/scans with free text and ✓ ✗ ● stamps, sign with an
image of a handwritten signature. Milestone 1 (current) is a fast, crisp PDF viewer with
page rotation, undo/redo and saving. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Windows 10/11
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```powershell
uv venv --python 3.12
uv sync --extra dev
```

## Run

```powershell
uv run pdfeditor                 # empty window
uv run pdfeditor path\to\file.pdf
uv run pdfeditor --lang fr file.pdf
```

## Develop

```powershell
uv run pytest
uv run ruff check .
uv run ruff format .
```

## License

GNU Affero General Public License v3.0 only — see [LICENSE](LICENSE).
