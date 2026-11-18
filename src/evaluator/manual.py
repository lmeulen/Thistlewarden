"""Safe rendering and lookup for canonical user-manual Markdown."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, PackagePath, files
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import mistune
from markupsafe import Markup
from mistune import HTMLRenderer

_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>\s*')
_DISTRIBUTION = "thistlewarden"
_PACKAGED_MANUAL_SUFFIX = PurePosixPath("share/thistlewarden/manual/README.md")
_SOURCE_MANUAL_ROOT = Path(__file__).resolve().parents[2] / "docs" / "manual"


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


def _manual_root() -> Path | None:
    """Resolve canonical checkout or installed data location without CWD or tree searches."""

    if _SOURCE_MANUAL_ROOT.joinpath("README.md").is_file():
        return _SOURCE_MANUAL_ROOT
    try:
        distribution_files = files(_DISTRIBUTION)
    except (PackageNotFoundError, OSError):
        return None
    if distribution_files is None:
        return None
    indexes = [
        item
        for item in distribution_files
        if isinstance(item, PackagePath)
        and PurePosixPath(str(item)).parts[-4:] == _PACKAGED_MANUAL_SUFFIX.parts
    ]
    if len(indexes) != 1:
        return None
    index = indexes[0]
    try:
        located = Path(index.locate()).resolve()
        return located.parent if located.is_file() else None
    except OSError:
        return None


def manual_pages() -> list[str]:
    """Return stable page slugs from canonical manual content."""

    root = _manual_root()
    if root is None:
        return []
    try:
        return sorted(
            item.name.removesuffix(".md")
            for item in root.iterdir()
            if item.name != "README.md" and item.name.endswith(".md") and item.is_file()
        )
    except OSError:
        return []


def render_manual_page(page: str) -> Markup:
    """Render allowlisted canonical Markdown with escaped embedded HTML."""

    filename = "README.md" if page == "index" else f"{page}.md"
    if page != "index" and not _SAFE_NAME.fullmatch(page):
        raise FileNotFoundError(page)
    root = _manual_root()
    if root is None:
        raise FileNotFoundError(page)
    source = root.joinpath(filename)
    try:
        if not source.is_file():
            raise FileNotFoundError(page)
        markdown = source.read_text(encoding="utf-8")
    except OSError:
        raise FileNotFoundError(page) from None
    anchors = "".join(
        f'<span id="{match.group(1)}"></span>' for match in _ANCHOR.finditer(markdown)
    )
    rendered = anchors + cast(str, _MARKDOWN(_ANCHOR.sub("", markdown)))
    return Markup(rendered)  # noqa: S704


def manual_asset(name: str) -> bytes:
    """Read an allowlisted canonical PNG asset."""

    path = PurePosixPath(name)
    if not _SAFE_NAME.fullmatch(path.stem) or path.suffix != ".png":
        raise FileNotFoundError(name)
    root = _manual_root()
    if root is None:
        raise FileNotFoundError(name)
    asset = root.joinpath("images", name)
    try:
        if not asset.is_file():
            raise FileNotFoundError(name)
        return asset.read_bytes()
    except OSError:
        raise FileNotFoundError(name) from None
