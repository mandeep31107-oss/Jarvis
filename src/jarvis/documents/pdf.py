"""A minimal but valid PDF writer on the standard library.

Produces a PDF 1.4 file with correct cross-reference offsets, three Type-1 base
fonts (Helvetica, Helvetica-Bold, Courier), automatic page breaks, wrapped
paragraphs, bullet lists and bordered tables.

Text layout uses an average-glyph-width approximation, which is accurate enough
for wrapping and is deliberately conservative (it wraps slightly early rather
than running text off the page).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

A4 = (595.28, 841.89)
LETTER = (612.0, 792.0)

_FONTS = {"helv": "/F1", "helv-bold": "/F2", "cour": "/F3"}
#: Average advance width as a fraction of font size, per font.
_AVG_WIDTH = {"helv": 0.50, "helv-bold": 0.53, "cour": 0.60}


@dataclass
class _Op:
    """One drawing operation, resolved to PDF operators at render time."""

    kind: str  # text | line | rect
    x: float = 0.0
    y: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    text: str = ""
    size: float = 11.0
    font: str = "helv"
    width: float = 0.5
    fill: tuple[float, float, float] | None = None
    #: Stroke/fill colour for text runs (None = black).
    color: tuple[float, float, float] | None = None


@dataclass
class _Page:
    ops: list[_Op] = field(default_factory=list)


#: Unicode -> WinAnsiEncoding code point for the punctuation a report actually uses.
_WINANSI = {
    "\u2022": "\x95",  # bullet
    "\u2013": "\x96",  # en dash
    "\u2014": "\x97",  # em dash
    "\u2018": "\x91",
    "\u2019": "\x92",
    "\u201c": "\x93",
    "\u201d": "\x94",
    "\u2026": "\x85",  # ellipsis
    "\u20ac": "\x80",  # euro
    "\u00a0": " ",
}


def _esc(text: str) -> str:
    """Escape PDF string syntax and map common Unicode punctuation onto WinAnsi."""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "(":
            out.append("\\(")
        elif ch == ")":
            out.append("\\)")
        elif ch in _WINANSI:
            out.append(_WINANSI[ch])
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append("?")  # not in WinAnsi - degrade visibly rather than corrupt
    return "".join(out)


class PdfDocument:
    """Cursor-based PDF builder."""

    def __init__(
        self,
        title: str = "Document",
        *,
        page_size: tuple[float, float] = A4,
        margin: float = 56.0,
        author: str = "Jarvis",
    ) -> None:
        self.title = title
        self.author = author
        self.page_size = page_size
        self.margin = margin
        self._pages: list[_Page] = [_Page()]
        self._y = page_size[1] - margin
        self._x = margin

    # --- layout cursor -------------------------------------------------------
    @property
    def usable_width(self) -> float:
        return self.page_size[0] - 2 * self.margin

    def _ensure_space(self, needed: float) -> None:
        if self._y - needed < self.margin:
            self.new_page()

    def new_page(self) -> PdfDocument:
        self._pages.append(_Page())
        self._y = self.page_size[1] - self.margin
        return self

    def _emit(self, op: _Op) -> None:
        self._pages[-1].ops.append(op)

    # --- content -------------------------------------------------------------
    def heading(self, text: str, level: int = 1) -> PdfDocument:
        size = {1: 18.0, 2: 14.0, 3: 12.0}.get(level, 12.0)
        self._ensure_space(size * 2.4)
        self._y -= size * 0.9
        self._wrap_text(text, size=size, font="helv-bold", leading=size * 1.25)
        self._y -= size * 0.35
        return self

    def paragraph(self, text: str, *, size: float = 10.5, indent: float = 0.0) -> PdfDocument:
        self._wrap_text(text, size=size, font="helv", leading=size * 1.42, indent=indent)
        self._y -= size * 0.45
        return self

    def bullets(self, items: Iterable[str], *, size: float = 10.5) -> PdfDocument:
        for item in items:
            self._ensure_space(size * 2)
            self._emit(_Op(kind="text", x=self._x, y=self._y, text="\u2022", size=size))
            self._wrap_text(item, size=size, leading=size * 1.4, indent=14.0)
            self._y -= size * 0.25
        self._y -= size * 0.3
        return self

    def spacer(self, points: float = 12.0) -> PdfDocument:
        self._ensure_space(points)
        self._y -= points
        return self

    def rule(self, *, width: float = 0.6) -> PdfDocument:
        self._ensure_space(6)
        self._emit(_Op(kind="line", x=self._x, y=self._y, x2=self._x + self.usable_width, y2=self._y, width=width))
        self._y -= 8
        return self

    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[object]],
        *,
        size: float = 9.0,
        col_widths: Sequence[float] | None = None,
        row_height: float | None = None,
    ) -> PdfDocument:
        """A bordered table that splits across pages."""
        n = max(1, len(headers))
        widths = list(col_widths) if col_widths else [self.usable_width / n] * n
        scale = self.usable_width / sum(widths)
        widths = [w * scale for w in widths]
        height = row_height or size * 1.9

        def draw_row(values: Sequence[str], *, header: bool) -> None:
            self._ensure_space(height + 2)
            top = self._y
            x = self._x
            for i, value in enumerate(values[:n]):
                cell_width = widths[i]
                if header:
                    self._emit(
                        _Op(kind="rect", x=x, y=top - height, x2=x + cell_width, y2=top,
                            fill=(0.122, 0.220, 0.392))
                    )
                font = "helv-bold" if header else "helv"
                text = _clip(str(value), cell_width - 6, size, font)
                # White text over the dark header fill, black elsewhere.
                self._emit(_Op(kind="text", x=x + 3, y=top - height * 0.68, text=text,
                               size=size, font=font,
                               color=(1.0, 1.0, 1.0) if header else None))
                self._emit(_Op(kind="line", x=x, y=top - height, x2=x + cell_width,
                               y2=top - height, width=0.4))
                self._emit(_Op(kind="line", x=x + cell_width, y=top, x2=x + cell_width,
                               y2=top - height, width=0.4))
                x += cell_width
            self._emit(_Op(kind="line", x=self._x, y=top, x2=self._x + sum(widths), y2=top, width=0.4))
            self._y = top - height

        draw_row([str(h) for h in headers], header=True)
        for row in rows:
            values = [str(v) for v in row]
            values += [""] * (n - len(values))
            draw_row(values[:n], header=False)
        self._y -= 6
        return self

    def _wrap_text(
        self,
        text: str,
        *,
        size: float,
        leading: float,
        font: str = "helv",
        indent: float = 0.0,
    ) -> None:
        available = self.usable_width - indent
        for paragraph in str(text).split("\n"):
            for line in _wrap(paragraph, available, size, font):
                self._ensure_space(leading)
                self._emit(
                    _Op(kind="text", x=self._x + indent, y=self._y, text=line, size=size, font=font)
                )
                self._y -= leading

    # --- rendering -----------------------------------------------------------
    def _content_stream(self, page: _Page) -> bytes:
        out: list[str] = ["BT", "/F1 11 Tf", "ET"]
        pending_text: list[str] = []

        def flush() -> None:
            if pending_text:
                out.extend(pending_text)
                pending_text.clear()

        for op in page.ops:
            if op.kind == "text":
                font = _FONTS.get(op.font, "/F1")
                if op.color is not None:
                    r, g, b = op.color
                    pending_text.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
                else:
                    pending_text.append("0 0 0 rg")
                pending_text.append("BT")
                pending_text.append(f"{font} {op.size:.2f} Tf")
                pending_text.append(f"1 0 0 1 {op.x:.2f} {op.y:.2f} Tm")
                pending_text.append(f"({_esc(op.text)}) Tj")
                pending_text.append("ET")
            else:
                flush()
                if op.kind == "rect" and op.fill is not None:
                    r, g, b = op.fill
                    out.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
                    out.append(
                        f"{op.x:.2f} {op.y:.2f} {op.x2 - op.x:.2f} {op.y2 - op.y:.2f} re f"
                    )
                    out.append("0 0 0 rg")
                elif op.kind == "line":
                    out.append("0.55 0.55 0.55 RG")
                    out.append(f"{op.width:.2f} w")
                    out.append(f"{op.x:.2f} {op.y:.2f} m {op.x2:.2f} {op.y2:.2f} l S")
                    out.append("0 0 0 RG")
        flush()
        return "\n".join(out).encode("latin-1", "replace")

    def save(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        width, height = self.page_size

        contents = [self._content_stream(p) for p in self._pages]
        page_ids = [6 + 2 * i for i in range(len(self._pages))]
        content_ids = [7 + 2 * i for i in range(len(self._pages))]
        info_id = 6 + 2 * len(self._pages)

        objects: dict[int, bytes] = {}
        objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects[2] = (
            f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")
        )
        objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        objects[5] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
        for i, (pid, cid) in enumerate(zip(page_ids, content_ids, strict=True)):
            objects[pid] = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] "
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
                f"/Contents {cid} 0 R >>"
            ).encode("latin-1")
            objects[cid] = contents[i]
        objects[info_id] = (
            f"<< /Title ({_esc(self.title)}) /Producer (Jarvis) /Author ({_esc(self.author)}) >>"
        ).encode("latin-1")

        buf = bytearray()
        buf += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        offsets: dict[int, int] = {}
        for obj_id in sorted(objects):
            offsets[obj_id] = len(buf)
            body = objects[obj_id]
            if obj_id in content_ids:
                buf += f"{obj_id} 0 obj\n<< /Length {len(body)} >>\nstream\n".encode("latin-1")
                buf += body
                buf += b"\nendstream\nendobj\n"
            else:
                buf += f"{obj_id} 0 obj\n".encode("latin-1")
                buf += body
                buf += b"\nendobj\n"

        xref_pos = len(buf)
        count = max(offsets) + 1
        buf += f"xref\n0 {count}\n".encode("latin-1")
        buf += b"0000000000 65535 f \n"
        for obj_id in range(1, count):
            buf += f"{offsets.get(obj_id, 0):010d} 00000 n \n".encode("latin-1")
        buf += (
            f"trailer\n<< /Size {count} /Root 1 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")

        dest.write_bytes(bytes(buf))
        return dest

    # --- self-validation -----------------------------------------------------
    def validate(self, path: str | Path) -> dict[str, object]:
        """Structural checks, plus a real parse when pypdf is installed."""
        data = Path(path).read_bytes()
        problems: list[str] = []
        if not data.startswith(b"%PDF-"):
            problems.append("file does not start with a PDF header")
        if b"%%EOF" not in data[-32:]:
            problems.append("file does not end with %%EOF")
        if b"startxref" not in data:
            problems.append("no startxref marker")
        else:
            try:
                marker = data.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip()
                offset = int(marker.split()[0])
                if data[offset : offset + 4] != b"xref":
                    problems.append(f"startxref points to {data[offset:offset + 4]!r}, not 'xref'")
            except (ValueError, IndexError) as exc:
                problems.append(f"startxref is not parseable: {exc}")
        declared_pages = data.count(b"/Type /Page ") + data.count(b"/Type /Page\n")
        if declared_pages and declared_pages != len(self._pages):
            problems.append(f"expected {len(self._pages)} page objects, found {declared_pages}")
        try:  # optional, but a real parse is the strongest check available
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            if len(reader.pages) != len(self._pages):
                problems.append(
                    f"pypdf read {len(reader.pages)} pages, expected {len(self._pages)}"
                )
            meta_title = (reader.metadata or {}).get("/Title") if reader.metadata else None
            if self.title and self.title.isascii() and meta_title != self.title:
                problems.append(f"metadata title is {meta_title!r}, expected {self.title!r}")
            text = "".join((p.extract_text() or "") for p in reader.pages)
            sample = self._first_text()
            if sample and sample not in text:
                problems.append(f"emitted text {sample!r} was not recoverable from the PDF")
            if not text.strip():
                problems.append("no text at all was extractable from the PDF")
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001 - any parse failure is a real failure
            problems.append(f"pypdf could not parse the file: {exc}")
        return {"ok": not problems, "problems": problems, "pages": len(self._pages)}

    def _first_text(self) -> str:
        """The first ASCII-only text run we emitted, used to prove content survived.

        Non-ASCII runs are skipped because they are re-encoded to WinAnsi on the
        way out, so a byte-for-byte comparison against the input would be wrong.
        """
        for page in self._pages:
            for op in page.ops:
                text = op.text.strip()
                if op.kind == "text" and text and text.isascii():
                    return text
        return ""


def _text_width(text: str, size: float, font: str) -> float:
    return len(text) * size * _AVG_WIDTH.get(font, 0.5)


def _clip(text: str, max_width: float, size: float, font: str) -> str:
    if _text_width(text, size, font) <= max_width:
        return text
    per_char = size * _AVG_WIDTH.get(font, 0.5)
    limit = max(1, int(max_width / per_char))
    return text[: max(0, limit - 1)] + "\u2026" if limit > 1 else ""


def _wrap(text: str, available: float, size: float, font: str) -> list[str]:
    """Greedy word wrap using the average-glyph-width model."""
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if _text_width(candidate, size, font) <= available or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines
