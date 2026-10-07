"""Unit tests verifying setup and runmin launcher scripts."""

from pathlib import Path


def test_no_runpy_references():
    workspace = Path(__file__).resolve().parent.parent
    files_to_check = [
        workspace / "setup",
        workspace / "setup.bat",
        workspace / "setup.sh",
        workspace / "runmin",
        workspace / "runmin.bat",
        workspace / "runmin.sh",
    ]

    for filepath in files_to_check:
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            assert "runpy" not in content.lower(), f"Found reference to 'runpy' in {filepath.name}"
