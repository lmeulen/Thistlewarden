"""Verify release artifacts contain one complete canonical user-manual tree."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

_WHEEL_MARKER = PurePosixPath("share/thistlewarden/manual")
_SDIST_MARKER = PurePosixPath("docs/manual")
_DUPLICATE_MARKER = PurePosixPath("evaluator/manual")


def canonical_files(root: Path) -> dict[PurePosixPath, bytes]:
    manual = root / "docs" / "manual"
    return {
        PurePosixPath(path.relative_to(manual).as_posix()): path.read_bytes()
        for path in manual.rglob("*")
        if path.is_file()
    }


def _relative_after(path: PurePosixPath, marker: PurePosixPath) -> PurePosixPath | None:
    parts = path.parts
    marker_parts = marker.parts
    for index in range(len(parts) - len(marker_parts) + 1):
        if parts[index : index + len(marker_parts)] == marker_parts:
            relative = parts[index + len(marker_parts) :]
            return PurePosixPath(*relative) if relative else None
    return None


def _manual_entries(
    entries: Mapping[PurePosixPath, bytes], marker: PurePosixPath
) -> dict[PurePosixPath, bytes]:
    return {
        relative: content
        for path, content in entries.items()
        if (relative := _relative_after(path, marker)) is not None
    }


def verify_entries(
    entries: Mapping[PurePosixPath, bytes], expected: Mapping[PurePosixPath, bytes], *, wheel: bool
) -> None:
    marker = _WHEEL_MARKER if wheel else _SDIST_MARKER
    actual = _manual_entries(entries, marker)
    if actual != expected:
        missing = sorted(str(path) for path in expected.keys() - actual.keys())
        extra = sorted(str(path) for path in actual.keys() - expected.keys())
        changed = sorted(
            str(path) for path in expected.keys() & actual.keys() if expected[path] != actual[path]
        )
        raise ValueError(
            f"manual artifact mismatch: missing={missing}, extra={extra}, changed={changed}"
        )
    duplicates = _manual_entries(entries, _DUPLICATE_MARKER)
    if duplicates:
        raise ValueError("artifact contains duplicate evaluator/manual content")


def verify_wheel(path: Path, expected: Mapping[PurePosixPath, bytes]) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = {
            PurePosixPath(name): archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    verify_entries(entries, expected, wheel=True)


def verify_sdist(path: Path, expected: Mapping[PurePosixPath, bytes]) -> None:
    with tarfile.open(path, "r:gz") as archive:
        entries = {
            PurePosixPath(member.name): content
            for member in archive.getmembers()
            if member.isfile()
            and (extracted := archive.extractfile(member)) is not None
            and (content := extracted.read()) is not None
        }
    verify_entries(entries, expected, wheel=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    arguments = parser.parse_args()
    expected = canonical_files(arguments.root)
    verify_wheel(arguments.wheel, expected)
    verify_sdist(arguments.sdist, expected)


if __name__ == "__main__":
    main()
