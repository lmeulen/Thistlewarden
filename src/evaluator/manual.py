"""Safe rendering and lookup for packaged user-manual Markdown."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import PurePosixPath
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import mistune
from markupsafe import Markup
from mistune import HTMLRenderer

_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>\s*')


class _ManualRenderer(HTMLRenderer):
    def __init__(self) -> None:
        super().__init__(escape=True, allow_harmful_protocols=False)

    @staticmethod
    def _local_url(url: str, *, image: bool = False) -> str:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or url.startswith(("/", "#")):
            return url
        path = PurePosixPath(parsed.path)
        if image and len(path.parts) == 2 and path.parts[0] == "images":
            new_path = f"/manual/assets/{path.name}"
        elif path.suffix.casefold() == ".md" and len(path.parts) == 1:
            new_path = "/manual/" if path.name.casefold() == "readme.md" else f"/manual/{path.stem}"
        else:
            return url
        return urlunsplit(("", "", new_path, parsed.query, parsed.fragment))

    def link(self, text: str, url: str, title: str | None = None) -> str:
        return super().link(text, self._local_url(url), title)

    def image(self, text: str, url: str, title: str | None = None) -> str:
        return super().image(text, self._local_url(url, image=True), title)

    @staticmethod
    def _safe_anchor(html: str) -> str | None:
        match = _ANCHOR.fullmatch(html)
        return f'<span id="{match.group(1)}"></span>' if match else None

    def block_html(self, html: str) -> str:
        return self._safe_anchor(html) or super().block_html(html)

    def inline_html(self, html: str) -> str:
        return self._safe_anchor(html) or super().inline_html(html)


_MARKDOWN = mistune.create_markdown(renderer=_ManualRenderer(), plugins=["table"])
_ROOT = files("evaluator").joinpath("manual")


def manual_pages() -> list[str]:
    """Return stable page slugs included in installed package."""
    return sorted(
        item.name.removesuffix(".md")
        for item in _ROOT.iterdir()
        if item.name != "README.md" and item.name.endswith(".md")
    )


def render_manual_page(page: str) -> Markup:
    """Render allowlisted packaged Markdown with escaped embedded HTML."""
    filename = "README.md" if page == "index" else f"{page}.md"
    if page != "index" and not _SAFE_NAME.fullmatch(page):
        raise FileNotFoundError(page)
    source = _ROOT.joinpath(filename)
    if not source.is_file():
        raise FileNotFoundError(page)
    markdown = source.read_text(encoding="utf-8")
    anchors = "".join(
        f'<span id="{match.group(1)}"></span>' for match in _ANCHOR.finditer(markdown)
    )
    rendered = anchors + cast(str, _MARKDOWN(_ANCHOR.sub("", markdown)))
    return Markup(rendered)  # noqa: S704


def manual_asset(name: str) -> bytes:
    """Read an allowlisted packaged PNG asset."""
    if not _SAFE_NAME.fullmatch(PurePosixPath(name).stem) or PurePosixPath(name).suffix != ".png":
        raise FileNotFoundError(name)
    asset = _ROOT.joinpath("images", name)
    if not asset.is_file():
        raise FileNotFoundError(name)
    return asset.read_bytes()
