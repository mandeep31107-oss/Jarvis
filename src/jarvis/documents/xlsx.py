"""A real .xlsx writer and reader on the standard library.

Produces a valid OOXML SpreadsheetML package: content types, relationships,
workbook, styles and one XML sheet per worksheet. Number formats, a styled
header row, frozen panes, an autofilter, column widths and live formulas are
all supported.

``read_xlsx`` exists so the DocumentAgent can validate its own output (spec
section 16, step 5) rather than trusting that a file it wrote is well formed.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Style indices (must match cellXfs in _styles_xml)
S_DEFAULT = 0
S_HEADER = 1
S_NUMBER = 2
S_PERCENT = 3
S_BOLD = 4
S_WRAP = 5

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheets}
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="0.0%"/></numFmts>
<fonts count="3">
<font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right><top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def column_letter(index: int) -> str:
    """1-based column number -> Excel letters (1 -> A, 27 -> AA)."""
    if index < 1:
        raise ValueError("column index is 1-based")
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


@dataclass
class _Cell:
    row: int
    col: int
    value: Any
    style: int = S_DEFAULT
    formula: str | None = None

    @property
    def ref(self) -> str:
        return f"{column_letter(self.col)}{self.row}"


@dataclass
class Worksheet:
    name: str
    cells: dict[tuple[int, int], _Cell] = field(default_factory=dict)
    widths: dict[int, float] = field(default_factory=dict)
    freeze_header: bool = False
    autofilter: bool = False
    _max_row: int = 0
    _max_col: int = 0

    def set(self, row: int, col: int, value: Any, style: int = S_DEFAULT) -> Worksheet:
        if row < 1 or col < 1:
            raise ValueError("row and col are 1-based")
        self.cells[(row, col)] = _Cell(row=row, col=col, value=value, style=style)
        self._max_row = max(self._max_row, row)
        self._max_col = max(self._max_col, col)
        return self

    def formula(self, row: int, col: int, expression: str, style: int = S_NUMBER) -> Worksheet:
        cell = _Cell(row=row, col=col, value=None, style=style, formula=expression.lstrip("="))
        self.cells[(row, col)] = cell
        self._max_row = max(self._max_row, row)
        self._max_col = max(self._max_col, col)
        return self

    def width(self, col: int, characters: float) -> Worksheet:
        self.widths[col] = characters
        return self

    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
        *,
        start_row: int = 1,
        number_columns: Sequence[int] = (),
        percent_columns: Sequence[int] = (),
        autofit: bool = True,
    ) -> Worksheet:
        """Write a header row plus data rows, styling numbers sensibly."""
        for c, header in enumerate(headers, start=1):
            self.set(start_row, c, header, S_HEADER)
        numbers = {c for c in number_columns}
        percents = {c for c in percent_columns}
        for r, row in enumerate(rows, start=start_row + 1):
            for c, value in enumerate(row, start=1):
                if c in percents and isinstance(value, (int, float)):
                    self.set(r, c, value, S_PERCENT)
                elif c in numbers and isinstance(value, (int, float)):
                    self.set(r, c, value, S_NUMBER)
                elif isinstance(value, str) and len(value) > 40:
                    self.set(r, c, value, S_WRAP)
                else:
                    self.set(r, c, value)
        if autofit:
            for c in range(1, len(headers) + 1):
                longest = len(str(headers[c - 1]))
                for (_row_no, col_no), cell in self.cells.items():
                    if col_no == c and cell.value is not None:
                        longest = max(longest, len(str(cell.value)))
                self.width(c, min(52.0, max(10.0, longest + 2)))
        self.freeze_header = start_row == 1
        self.autofilter = True
        return self

    # --- serialisation -------------------------------------------------------
    def to_xml(self, sheet_id: int) -> str:
        cols = ""
        if self.widths:
            parts = [
                f'<col min="{c}" max="{c}" width="{w}" customWidth="1"/>'
                for c, w in sorted(self.widths.items())
            ]
            cols = f"<cols>{''.join(parts)}</cols>"

        views = ""
        if self.freeze_header:
            views = (
                '<sheetViews><sheetView workbookViewId="0">'
                '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                "<selection pane=\"bottomLeft\" activeCell=\"A2\" sqref=\"A2\"/>"
                "</sheetView></sheetViews>"
            )

        auto = ""
        if self.autofilter and self._max_row > 1 and self._max_col:
            auto = f'<autoFilter ref="A1:{column_letter(self._max_col)}{self._max_row}"/>'

        rows_xml: list[str] = []
        for row in sorted({r for r, _ in self.cells}):
            cells_xml = []
            for col in sorted(c for r, c in self.cells if r == row):
                cell = self.cells[(row, col)]
                cells_xml.append(_cell_xml(cell))
            rows_xml.append(f'<row r="{row}">{"".join(cells_xml)}</row>')

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{views}<sheetFormatPr defaultRowHeight=\"15\"/>{cols}"
            f'<sheetData>{"".join(rows_xml)}</sheetData>{auto}'
            "</worksheet>"
        )


def _cell_xml(cell: _Cell) -> str:
    style = f' s="{cell.style}"' if cell.style else ""
    ref = cell.ref
    if cell.formula:
        # No cached value: the workbook sets fullCalcOnLoad so Excel/LibreOffice
        # computes it on open instead of showing a stale zero.
        return f'<c r="{ref}"{style}><f>{_escape(cell.formula)}</f></c>'
    value = cell.value
    if value is None:
        return f'<c r="{ref}"{style}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{style} t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value!r}</v></c>'
    text = _escape(str(value))
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


class XlsxWorkbook:
    """Build and save a workbook."""

    def __init__(self) -> None:
        self.sheets: list[Worksheet] = []

    def sheet(self, name: str) -> Worksheet:
        if not name.strip():
            raise ValueError("sheet name must not be empty")
        if len(name) > 31:
            raise ValueError("Excel sheet names are limited to 31 characters")
        if any(ch in name for ch in "[]:*?/\\"):
            raise ValueError(f"invalid characters in sheet name: {name!r}")
        if any(s.name == name for s in self.sheets):
            raise ValueError(f"duplicate sheet name: {name!r}")
        ws = Worksheet(name=name)
        self.sheets.append(ws)
        return ws

    def add_table_sheet(
        self,
        name: str,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
        **kwargs: Any,
    ) -> Worksheet:
        ws = self.sheet(name)
        ws.table(headers, rows, **kwargs)
        return ws

    def save(self, path: str | Path) -> Path:
        if not self.sheets:
            raise ValueError("workbook has no sheets")
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        sheet_overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(self.sheets) + 1)
        )
        workbook_sheets = "".join(
            f'<sheet name="{_escape(ws.name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, ws in enumerate(self.sheets, start=1)
        )
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets>"
            '<calcPr calcId="191029" fullCalcOnLoad="1"/>'
            "</workbook>"
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(self.sheets) + 1)
        )
        styles_id = len(self.sheets) + 1
        rels += (
            f'<Relationship Id="rId{styles_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        workbook_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>"
        )

        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES.format(sheets=sheet_overrides))
            zf.writestr("_rels/.rels", _ROOT_RELS)
            zf.writestr("xl/workbook.xml", workbook)
            zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            zf.writestr("xl/styles.xml", _STYLES)
            for i, ws in enumerate(self.sheets, start=1):
                zf.writestr(f"xl/worksheets/sheet{i}.xml", ws.to_xml(i))
        return dest

    # --- self-validation -----------------------------------------------------
    def validate(self, path: str | Path) -> dict[str, Any]:
        """Re-open the file we just wrote and confirm it is readable and consistent."""
        problems: list[str] = []
        data = read_xlsx(path)
        if sorted(data) != sorted(ws.name for ws in self.sheets):
            problems.append(f"sheet mismatch: wrote {[s.name for s in self.sheets]}, read {sorted(data)}")
        for ws in self.sheets:
            rows = data.get(ws.name, [])
            expected_rows = ws._max_row
            if len(rows) != expected_rows:
                problems.append(f"{ws.name}: expected {expected_rows} rows, read {len(rows)}")
            for row in rows:
                for value in row:
                    if isinstance(value, float) and value != value:  # NaN
                        problems.append(f"{ws.name}: NaN value written")
        for ws in self.sheets:
            for cell in ws.cells.values():
                if cell.formula:
                    for ref in re.findall(r"([A-Z]{1,3}\d{1,7})", cell.formula):
                        col = re.match(r"[A-Z]+", ref).group(0)
                        row = int(ref[len(col):])
                        if row > max(ws._max_row, 1) * 100:
                            problems.append(f"{ws.name}: formula range far outside the data: {ref}")
        return {"ok": not problems, "problems": problems, "sheets": sorted(data)}


def read_xlsx(path: str | Path) -> dict[str, list[list[Any]]]:
    """Minimal reader: sheet name -> rows of values. Formulas read back as their
    expression string, because the file holds no cached value."""
    dest = Path(path)
    if not dest.is_file():
        raise FileNotFoundError(dest)
    out: dict[str, list[list[Any]]] = {}
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        for required in ("xl/workbook.xml", "[Content_Types].xml"):
            if required not in names:
                raise ValueError(f"{dest} is not a valid xlsx package (missing {required})")
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        sheet_names = [s.get("name") or "" for s in workbook.iter(f"{NS}sheet")]
        for i, name in enumerate(sheet_names, start=1):
            member = f"xl/worksheets/sheet{i}.xml"
            if member not in names:
                continue
            root = ET.fromstring(zf.read(member))
            rows: list[list[Any]] = []
            for row_el in root.iter(f"{NS}row"):
                values: dict[int, Any] = {}
                for c in row_el.iter(f"{NS}c"):
                    ref = c.get("r") or ""
                    col = _col_from_ref(ref)
                    ctype = c.get("t")
                    if ctype == "inlineStr":
                        t = c.find(f"{NS}is/{NS}t")
                        values[col] = t.text if t is not None and t.text is not None else ""
                    elif ctype == "b":
                        v = c.find(f"{NS}v")
                        values[col] = (v is not None and v.text == "1")
                    else:
                        f = c.find(f"{NS}f")
                        v = c.find(f"{NS}v")
                        if f is not None and f.text:
                            values[col] = "=" + f.text
                        elif v is not None and v.text not in (None, ""):
                            text = v.text
                            try:
                                values[col] = int(text)
                            except ValueError:
                                try:
                                    values[col] = float(text)
                                except ValueError:
                                    values[col] = text
                        else:
                            values[col] = None
                if values:
                    width = max(values)
                    rows.append([values.get(c) for c in range(1, width + 1)])
            out[name] = rows
    return out


def _col_from_ref(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 1
    n = 0
    for ch in letters.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n
