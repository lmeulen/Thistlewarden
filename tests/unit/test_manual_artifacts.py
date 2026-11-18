from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from scripts.verify_manual_artifacts import verify_entries


def test_artifact_manual_verifier_accepts_one_complete_tree() -> None:
    expected = {PurePosixPath("README.md"): b"manual", PurePosixPath("images/a.png"): b"png"}
    wheel_entries = {
        PurePosixPath("thistlewarden-1.data/data/share/thistlewarden/manual/README.md"): b"manual",
        PurePosixPath("thistlewarden-1.data/data/share/thistlewarden/manual/images/a.png"): b"png",
    }
    sdist_entries = {
        PurePosixPath("thistlewarden-1/docs/manual/README.md"): b"manual",
        PurePosixPath("thistlewarden-1/docs/manual/images/a.png"): b"png",
    }

    verify_entries(wheel_entries, expected, wheel=True)
    verify_entries(sdist_entries, expected, wheel=False)


def test_artifact_manual_verifier_rejects_missing_changed_or_duplicate_content() -> None:
    expected = {PurePosixPath("README.md"): b"manual"}
    with pytest.raises(ValueError, match="missing=.*README.md"):
        verify_entries({}, expected, wheel=True)
    with pytest.raises(ValueError, match="changed=.*README.md"):
        verify_entries(
            {
                PurePosixPath(
                    "thistlewarden-1.data/data/share/thistlewarden/manual/README.md"
                ): b"changed"
            },
            expected,
            wheel=True,
        )
    with pytest.raises(ValueError, match="duplicate evaluator/manual"):
        verify_entries(
            {
                PurePosixPath(
                    "thistlewarden-1.data/data/share/thistlewarden/manual/README.md"
                ): b"manual",
                PurePosixPath("evaluator/manual/README.md"): b"manual",
            },
            expected,
            wheel=True,
        )
