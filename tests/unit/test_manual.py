from __future__ import annotations

from pathlib import Path

import pytest

from evaluator import manual
from evaluator.manual import manual_asset, manual_pages, render_manual_page

ROOT = Path(__file__).parents[2]
CANONICAL = ROOT / "docs" / "manual"


def test_source_checkout_serves_every_canonical_manual_file() -> None:
    markdown_pages = sorted(CANONICAL.glob("*.md"))
    images = sorted(CANONICAL.joinpath("images").glob("*.png"))

    assert manual_pages() == sorted(
        path.stem for path in markdown_pages if path.name != "README.md"
    )
    assert "Thistlewarden User Manual" in str(render_manual_page("index"))
    for path in markdown_pages:
        page = "index" if path.name == "README.md" else path.stem
        assert str(render_manual_page(page))
    for path in images:
        assert manual_asset(path.name) == path.read_bytes()


def test_manual_renderer_rewrites_local_links_and_escapes_html() -> None:
    content = str(render_manual_page("dashboard"))

    assert 'href="/manual/#manual-top"' in content
    assert 'src="/manual/assets/dashboard.png"' in content
    assert '<span id="page-top"></span>' in content
    assert "&lt;a id=" not in content
    assert "<script" not in content


def test_manual_lookup_rejects_unknown_and_unsafe_paths() -> None:
    assert "dashboard" in manual_pages()
    assert manual_asset("dashboard.png").startswith(b"\x89PNG")
    for page in ("missing", "../README", "https://example.com"):
        with pytest.raises(FileNotFoundError):
            render_manual_page(page)
    for asset in ("missing.png", "../dashboard.png", "dashboard.svg"):
        with pytest.raises(FileNotFoundError):
            manual_asset(asset)


def test_missing_manual_content_fails_closed_without_path_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manual, "_SOURCE_MANUAL_ROOT", Path("/unavailable/manual"))
    monkeypatch.setattr(manual, "files", lambda _distribution: None)

    assert manual_pages() == []
    with pytest.raises(FileNotFoundError) as page_error:
        render_manual_page("index")
    with pytest.raises(FileNotFoundError) as asset_error:
        manual_asset("dashboard.png")
    assert str(page_error.value) == "index"
    assert str(asset_error.value) == "dashboard.png"
