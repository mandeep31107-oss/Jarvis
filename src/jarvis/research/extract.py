"""Text extraction from fetched HTML (Phase 2).

Standard library only: :class:`html.parser.HTMLParser` plus a small amount of
heuristics. This is deliberately not a full readability implementation - it
extracts visible text, the title, the language and the canonical URL, which is
what the knowledge gates need. Anything it cannot determine is left empty
rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

#: Elements whose contents are never user-visible prose.
SKIP_ELEMENTS = frozenset(
    {"script", "style", "noscript", "template", "svg", "iframe", "head", "nav", "footer"}
)

#: Block-level elements that should end a line.
BLOCK_ELEMENTS = frozenset(
    {
        "p", "div", "section", "article", "li", "ul", "ol", "table", "tr",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "br", "hr",
        "header", "aside", "figure", "figcaption", "main", "dd", "dt",
    }
)

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass
class Extracted:
    """What could be read out of a document."""

    text: str = ""
    title: str = ""
    language: str = ""
    canonical_url: str = ""
    description: str = ""
    publisher: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    #: Rough signal of how much real prose was found. Used to decide whether a
    #: page is worth ingesting at all - a page that is 90% navigation chrome is
    #: not a source.
    word_count: int = 0

    @property
    def usable(self) -> bool:
        """Enough real text to summarise. Below this, say so instead of trying."""
        return self.word_count >= 40

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "language": self.language,
            "canonical_url": self.canonical_url,
            "publisher": self.publisher,
            "word_count": self.word_count,
            "usable": self.usable,
            "headings": self.headings[:20],
            "links": len(self.links),
        }


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self.headings: list[str] = []
        self._heading_buf: list[str] = []
        self._in_heading = False
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_ELEMENTS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_buf = []
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.links.append(href)
        elif tag in BLOCK_ELEMENTS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = False
            text = _WHITESPACE.sub(" ", "".join(self._heading_buf)).strip()
            if text:
                self.headings.append(text)
            self._chunks.append("\n")
        elif tag in BLOCK_ELEMENTS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        # <title> lives inside <head>, which is otherwise skipped wholesale.
        # Checking it first is what keeps the document title available.
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        if self._in_heading:
            self._heading_buf.append(data)
        self._chunks.append(data)


def _clean(text: str) -> str:
    text = unescape(text)
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", text).strip()


def extract_meta(html: str) -> dict[str, str]:
    """Pull the handful of meta tags that matter, without a DOM library."""
    meta: dict[str, str] = {}
    for match in re.finditer(
        r"<meta\b[^>]*>", html[:20000], flags=re.IGNORECASE | re.DOTALL
    ):
        tag = match.group(0)
        attrs: dict[str, str] = {}
        for key, dq, sq in re.findall(
            r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", tag
        ):
            attrs[key.lower()] = (dq or sq).strip()
        name = (attrs.get("name") or attrs.get("property") or "").lower()
        content = attrs.get("content", "")
        if not name or not content:
            continue
        if name in ("og:site_name", "application-name"):
            meta.setdefault("publisher", content)
        elif name in ("og:locale", "dc.language", "og:locale:alternate"):
            meta.setdefault("language", content.split("_")[0].split("-")[0].lower())
        elif name in ("description", "og:description"):
            meta.setdefault("description", content)
        elif name in ("og:url", "canonical"):
            meta.setdefault("canonical_url", content)

    lang = re.search(r"<html\b[^>]*\blang\s*=\s*[\"']([^\"']+)", html[:5000], re.IGNORECASE)
    if lang:
        meta.setdefault("language", lang.group(1).split("-")[0].lower())
    canonical = re.search(
        r"<link\b[^>]*\brel\s*=\s*[\"']canonical[\"'][^>]*\bhref\s*=\s*[\"']([^\"']+)",
        html[:20000],
        re.IGNORECASE,
    )
    if canonical:
        meta.setdefault("canonical_url", canonical.group(1))
    return meta


def extract(body: str, *, content_type: str = "text/html") -> Extracted:
    """Turn a fetched body into :class:`Extracted`.

    Non-HTML content types are treated as plain text: no tags to strip, and
    inventing structure for them would be worse than admitting there is none.
    """
    if not body:
        return Extracted()

    if "html" not in content_type:
        text = _clean(body)
        return Extracted(
            text=text,
            word_count=len(text.split()),
            title=text.splitlines()[0][:200] if text else "",
        )

    parser = _TextExtractor()
    try:
        parser.feed(body)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup should not lose the text
        pass

    text = _clean("".join(parser._chunks))  # noqa: SLF001 - same module family
    meta = extract_meta(body)
    return Extracted(
        text=text,
        title=_WHITESPACE.sub(" ", parser.title).strip()[:300],
        language=meta.get("language", ""),
        canonical_url=meta.get("canonical_url", ""),
        description=meta.get("description", "")[:500],
        publisher=meta.get("publisher", ""),
        headings=parser.headings[:40],
        links=list(dict.fromkeys(parser.links))[:100],
        word_count=len(text.split()),
    )


def first_sentences(text: str, *, limit: int = 3, max_chars: int = 600) -> str:
    """A short, faithful excerpt. No paraphrasing, no added interpretation."""
    text = _WHITESPACE.sub(" ", text).strip()
    out: list[str] = []
    total = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        out.append(sentence)
        total += len(sentence)
        if len(out) >= limit or total >= max_chars:
            break
    excerpt = " ".join(out)
    return excerpt if len(excerpt) <= max_chars else excerpt[:max_chars].rsplit(" ", 1)[0] + " …"


__all__ = ["BLOCK_ELEMENTS", "Extracted", "SKIP_ELEMENTS", "extract", "extract_meta", "first_sentences"]
