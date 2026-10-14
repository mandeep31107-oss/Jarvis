"""Minimal dependency-free PDF writer.

Supports the two things the app needs: full-page raster images (plans, report
pages drawn with Pillow) and simple vector/text pages (schedules, budgets) using
the built-in Helvetica fonts. The output is a valid PDF 1.4 file with a correct
xref table -- verified by opening it in viewers and by the tests.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PIL import Image

A4 = (595.28, 841.89)          # portrait points
A4_LANDSCAPE = (841.89, 595.28)

EscapedText = str


def _escape(text: str) -> str:
    return (text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))


@dataclass
class PdfPage:
    width: float
    height: float
    content: bytes
    resources: bytes
    images: List[Tuple[str, bytes, int, int]] = field(default_factory=list)


class Pdf:
    """Accumulates pages and writes them out."""

    def __init__(self, title: str = "AI Home Designer export", author: str = "AI Home Designer") -> None:
        self.title = title
        self.author = author
        self.pages: List[PdfPage] = []

    # -- page builders ------------------------------------------------------
    def add_image_page(self, image: Image.Image, page_size: Tuple[float, float] = A4,
                       fit: str = "contain", background: str = "#ffffff") -> None:
        page_w, page_h = page_size
        buffer = io.BytesIO()
        rgb = image.convert("RGB")
        rgb.save(buffer, format="JPEG", quality=88, optimize=True)
        payload = buffer.getvalue()
        content = f"q {page_w:.2f} 0 0 {page_h:.2f} 0 0 cm /Im0 Do Q".encode()
        self.pages.append(PdfPage(width=page_w, height=page_h, content=content,
                                  resources=b"<< /XObject << /Im0 3 0 R >> >>",
                                  images=[("Im0", payload, rgb.width, rgb.height)]))

    def add_png_page(self, png_bytes: bytes, page_size: Tuple[float, float] = A4) -> None:
        self.add_image_page(Image.open(io.BytesIO(png_bytes)), page_size)

    def add_text_page(self, blocks: List[Tuple[str, str, float, float]],
                      page_size: Tuple[float, float] = A4,
                      margin: float = 48.0) -> None:
        """blocks: list of (text, style, x_fraction, y_fraction)."""
        page_w, page_h = page_size
        parts: List[str] = []
        y = page_h - margin
        for text, style, _x, _y in blocks:
            size, bold, color, leading = STYLE.get(style, (10, False, (0.15, 0.18, 0.22), 14))
            font = "/F2" if bold else "/F1"
            r, g, b = color
            for line in _wrap(text, size, page_w - 2 * margin):
                y -= leading
                parts.append(f"BT {font} {size} Tf {r:.3f} {g:.3f} {b:.3f} rg "
                             f"{margin:.2f} {y:.2f} Td ({_escape(line)}) Tj ET")
            y -= 4
        content = "\n".join(parts).encode("latin-1", "replace")
        self.pages.append(PdfPage(width=page_w, height=page_h, content=content,
                                  resources=b"<< /Font << /F1 3 0 R /F2 4 0 R >> >>"))

    # -- writer --------------------------------------------------------------
    def save(self) -> bytes:
        objects: List[bytes] = []

        def add(payload: bytes) -> int:
            objects.append(payload)
            return len(objects)

        catalog_id = add(b"")   # placeholder, index 1
        pages_id = add(b"")     # 2
        font_regular_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                              b"/Encoding /WinAnsiEncoding >>")
        font_bold_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                           b"/Encoding /WinAnsiEncoding >>")

        page_ids: List[int] = []
        for page in self.pages:
            xobject_refs = []
            image_ids: List[Tuple[str, int]] = []
            for name, payload, width, height in page.images:
                img_id = add(b"<< /Type /XObject /Subtype /Image /Width " + str(width).encode() +
                             b" /Height " + str(height).encode() +
                             b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                             b"/Length " + str(len(payload)).encode() + b" >>\nstream\n" +
                             payload + b"\nendstream")
                image_ids.append((name, img_id))
            resources = page.resources
            if image_ids:
                entries = b" ".join(f"/{name} {img_id} 0 R".encode() for name, img_id in image_ids)
                resources = (b"<< /XObject << " + entries + b" >> /ProcSet [/PDF /ImageC] >>")
            elif b"/Font" in resources:
                resources = (b"<< /Font << /F1 " + str(font_regular_id).encode() + b" 0 R /F2 " +
                             str(font_bold_id).encode() + b" 0 R >> /ProcSet [/PDF /Text] >>")
            res_id = add(resources)
            content_id = add(b"<< /Length " + str(len(page.content)).encode() + b" >>\nstream\n" +
                             page.content + b"\nendstream")
            page_id = add(b"<< /Type /Page /Parent " + str(pages_id).encode() + b" 0 R "
                          b"/MediaBox [0 0 " + f"{page.width:.2f} {page.height:.2f}".encode() +
                          b"] /Resources " + str(res_id).encode() + b" 0 R /Contents " +
                          str(content_id).encode() + b" 0 R >>")
            page_ids.append(page_id)

        kids = b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
        objects[pages_id - 1] = (b"<< /Type /Pages /Kids [" + kids + b"] /Count " +
                                 str(len(page_ids)).encode() + b" >>")
        objects[catalog_id - 1] = (b"<< /Type /Catalog /Pages " + str(pages_id).encode() +
                                   b" 0 R >>")

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: List[int] = []
        for index, payload in enumerate(objects, start=1):
            offsets.append(out.tell())
            out.write(str(index).encode() + b" 0 obj\n" + payload + b"\nendobj\n")
        xref_start = out.tell()
        out.write(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n")
        out.write(b"0000000000 65535 f \n")
        for offset in offsets:
            out.write(f"{offset:010d} 00000 n \n".encode())
        out.write(b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root " +
                  str(catalog_id).encode() + b" 0 R /Info << /Title (" +
                  _escape(self.title).encode("latin-1", "replace") + b") /Producer (AI Home "
                  b"Designer) >> >>\nstartxref\n" + str(xref_start).encode() + b"\n%%EOF\n")
        return out.getvalue()


STYLE = {
    "title": (22, True, (0.11, 0.16, 0.22), 28),
    "h1": (15, True, (0.15, 0.24, 0.34), 22),
    "h2": (12, True, (0.2, 0.28, 0.38), 18),
    "body": (10, False, (0.18, 0.22, 0.27), 14),
    "small": (8.5, False, (0.35, 0.4, 0.46), 12),
    "warn": (9.5, False, (0.55, 0.32, 0.1), 13.5),
    "critical": (9.5, True, (0.62, 0.2, 0.16), 13.5),
}


def _wrap(text: str, size: float, max_width: float) -> List[str]:
    approx = max_width / (size * 0.5)
    lines: List[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > approx and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines or [""]
