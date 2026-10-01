from __future__ import annotations

from importlib.metadata import version

import pytest

import pdfeditor
from pdfeditor.app import parse_args


def test_package_version_matches_metadata() -> None:
    assert pdfeditor.__version__ == version("pdfeditor")


def test_version_flag_prints_version(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"pdfeditor {pdfeditor.__version__}"


def test_hidden_flags_parse_but_stay_out_of_help(capsys) -> None:
    args = parse_args(["--self-check", "out", "--quit-after", "250", "a.pdf"])
    assert (args.self_check, args.quit_after, args.file) == ("out", 250, "a.pdf")
    with pytest.raises(SystemExit):
        parse_args(["--help"])
    help_text = capsys.readouterr().out
    assert "--lang" in help_text
    assert "--self-check" not in help_text and "--quit-after" not in help_text
