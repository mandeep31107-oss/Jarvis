"""A real .docx writer on the standard library.

WordprocessingML package with document part, styles and relationships. Supports
headings, paragraphs, bullet lists, tables and a page footer line.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from jarvis.util.clock import now_iso

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/>
</w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="240"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="52"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="260" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="2E5496"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="220" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="404040"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>
<w:pPr><w:ind w:left="420"/><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>
<w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:color="BFBFBF"/><w:left w:val="single" w:sz="4" w:color="BFBFBF"/>
<w:bottom w:val="single" w:sz="4" w:color="BFBFBF"/><w:right w:val="single" w:sz="4" w:color="BFBFBF"/>
<w:insideH w:val="single" w:sz="4" w:color="BFBFBF"/><w:insideV w:val="single" w:sz="4" w:color="BFBFBF"/>
</w:tblBorders></w:tblPr></w:style>
</w:styles>"""


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _para(text: str, style: str | None = None, *, bold: bool = False) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    # Preserve whitespace and line breaks inside the run.
    body = _esc(text).replace("\n", '</w:t><w:br/><w:t xml:space="preserve">')
    return (
        f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">{body}</w:t></w:r></w:p>"
    )


def _cell(text: object, *, header: bool = False) -> str:
    """One table cell, written as explicit XML rather than patched-together strings."""
    shading = '<w:shd w:val="clear" w:color="auto" w:fill="1F3864"/>' if header else ""
    run_props = '<w:rPr><w:b/><w:color w:val="FFFFFF"/></w:rPr>' if header else ""
    return (
        f'<w:tc><w:tcPr>{shading}</w:tcPr>'
        '<w:p><w:pPr><w:spacing w:after="40"/></w:pPr>'
        f'<w:r>{run_props}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'
        "</w:p></w:tc>"
    )


class DocxDocument:
    """Build and save a Word document."""

    def __init__(self, title: str = "Document") -> None:
        self.title = title
        self._body: list[str] = [_para(title, "Title")]

    def heading(self, text: str, level: int = 1) -> DocxDocument:
        level = max(1, min(3, level))
        self._body.append(_para(text, f"Heading{level}"))
        return self

    def paragraph(self, text: str, *, bold: bool = False) -> DocxDocument:
        self._body.append(_para(text, bold=bold))
        return self

    def bullets(self, items: Iterable[str]) -> DocxDocument:
        for item in items:
            self._body.append(_para(f"\u2022  {item}", "ListBullet"))
        return self

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> DocxDocument:
        headers = [str(h) for h in headers]
        width = int(9000 / max(1, len(headers)))
        grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in headers)
        out = [
            '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>',
            '<w:tblW w:w="0" w:type="auto"/></w:tblPr>',
            f"<w:tblGrid>{grid}</w:tblGrid>",
        ]
        head_cells = "".join(_cell(h, header=True) for h in headers)
        out.append(f"<w:tr><w:trPr><w:tblHeader/></w:trPr>{head_cells}</w:tr>")
        for row in rows:
            values = [str(v) for v in row]
            values += [""] * (len(headers) - len(values))
            body_cells = "".join(_cell(v) for v in values[: len(headers)])
            out.append(f"<w:tr>{body_cells}</w:tr>")
        out.append("</w:tbl>")
        # A trailing empty paragraph keeps Word happy after a table.
        out.append(_para(""))
        self._body.append("".join(out))
        return self

    def page_break(self) -> DocxDocument:
        self._body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        return self

    def footer_note(self, text: str | None = None) -> DocxDocument:
        note = text or f"Generated by Jarvis at {now_iso()} UTC."
        self._body.append(_para(note))
        return self

    def save(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{"".join(self._body)}'
            "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
            "<w:pgMar w:top=\"1134\" w:right=\"1134\" w:bottom=\"1134\" w:left=\"1134\" "
            "w:header=\"708\" w:footer=\"708\" w:gutter=\"0\"/></w:sectPr>"
            "</w:body></w:document>"
        )
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
            zf.writestr("_rels/.rels", _ROOT_RELS)
            zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
            zf.writestr("word/styles.xml", _STYLES)
            zf.writestr("word/document.xml", document)
        return dest

    def validate(self, path: str | Path) -> dict[str, object]:
        """Re-open and confirm the package parses and the text survived."""
        from xml.etree import ElementTree as ET

        problems: list[str] = []
        try:
            archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            return {"ok": False, "problems": [f"not a readable docx package: {exc}"]}
        with archive as zf:
            names = zf.namelist()
            for required in ("[Content_Types].xml", "word/document.xml", "word/styles.xml"):
                if required not in names:
                    problems.append(f"missing part {required}")
            if "word/document.xml" in names:
                try:
                    root = ET.fromstring(zf.read("word/document.xml"))
                except ET.ParseError as exc:
                    problems.append(f"document.xml does not parse: {exc}")
                else:
                    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                    text = "".join(t.text or "" for t in root.iter(f"{ns}t"))
                    if self.title not in text:
                        problems.append("title text not found in the rendered document")
        return {"ok": not problems, "problems": problems}
