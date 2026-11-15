from __future__ import annotations

from pathlib import Path

from evaluator.manual import manual_asset, manual_pages, render_manual_page


def test_packaged_manual_matches_canonical_sources() -> None:
    root = Path(__file__).parents[2]
    canonical = root / "docs" / "manual"
    packaged = root / "src" / "evaluator" / "manual"
    canonical_files = sorted(
        path.relative_to(canonical) for path in canonical.rglob("*") if path.is_file()
    )
    packaged_files = sorted(
        path.relative_to(packaged) for path in packaged.rglob("*") if path.is_file()
    )

    assert packaged_files == canonical_files
    for relative in canonical_files:
        assert (packaged / relative).read_bytes() == (canonical / relative).read_bytes()


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
        try:
            render_manual_page(page)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError(f"unsafe page accepted: {page}")
