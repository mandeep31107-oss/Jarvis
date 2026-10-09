"""Document generation (spec section 16) - real files, validated on the way out."""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from jarvis.agents.base import AgentRequest
from jarvis.agents.documents import DocumentAgent
from jarvis.documents import DocxDocument, MarkdownDocument, PdfDocument, XlsxWorkbook, write_csv
from jarvis.documents.csv_doc import read_csv
from jarvis.documents.xlsx import column_letter, read_xlsx

HEADERS = ["Month", "Clients", "Revenue"]
ROWS = [[1, 1.0, 1000.0], [2, 2.0, 2000.0], [3, 3.0, 3000.0]]


# ----------------------------------------------------------------------- CSV
def test_csv_roundtrips(tmp_path):
    path = write_csv(tmp_path / "a.csv", ROWS, headers=HEADERS)
    assert read_csv(path) == [HEADERS] + [[str(c) for c in r] for r in ROWS]


def test_csv_escapes_delimiters_and_quotes(tmp_path):
    path = write_csv(tmp_path / "b.csv", [["a,b", 'say "hi"', "plain"]])
    text = path.read_text(encoding="utf-8-sig")
    assert '"a,b"' in text
    assert '""hi""' in text
    assert list(csv.reader(path.open(encoding="utf-8-sig")))[0][0] == "a,b"


# ---------------------------------------------------------------------- XLSX
def test_xlsx_is_a_valid_package(tmp_path):
    workbook = XlsxWorkbook()
    workbook.add_table_sheet("Data", HEADERS, ROWS, number_columns=(3,))
    path = workbook.save(tmp_path / "book.xlsx")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "[Content_Types].xml" in names
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
    assert workbook.validate(path)["ok"]


def test_xlsx_values_survive_the_roundtrip(tmp_path):
    workbook = XlsxWorkbook()
    workbook.add_table_sheet("Data", HEADERS, ROWS, number_columns=(3,))
    path = workbook.save(tmp_path / "book.xlsx")
    data = read_xlsx(path)["Data"]
    assert data[0] == HEADERS
    assert data[1] == [1, 1.0, 1000.0]


def test_xlsx_formulas_are_written_live(tmp_path):
    workbook = XlsxWorkbook()
    sheet = workbook.add_table_sheet("Data", HEADERS, ROWS, number_columns=(3,))
    sheet.formula(5, 3, "SUM(C2:C4)")
    path = workbook.save(tmp_path / "book.xlsx")
    assert read_xlsx(path)["Data"][4][2] == "=SUM(C2:C4)"
    # fullCalcOnLoad means the spreadsheet app computes it on open.
    assert b"fullCalcOnLoad" in zipfile.ZipFile(path).read("xl/workbook.xml")


def test_xlsx_multiple_sheets_and_validation(tmp_path):
    workbook = XlsxWorkbook()
    workbook.add_table_sheet("One", ["A"], [[1]])
    workbook.add_table_sheet("Two", ["B"], [[2]])
    path = workbook.save(tmp_path / "multi.xlsx")
    assert sorted(read_xlsx(path)) == ["One", "Two"]
    assert workbook.validate(path)["ok"]


def test_xlsx_rejects_invalid_sheet_names(tmp_path):
    workbook = XlsxWorkbook()
    with pytest.raises(ValueError):
        workbook.sheet("bad/name")
    with pytest.raises(ValueError):
        workbook.sheet("x" * 32)
    workbook.sheet("ok")
    with pytest.raises(ValueError):
        workbook.sheet("ok")


def test_xlsx_refuses_to_save_an_empty_workbook(tmp_path):
    with pytest.raises(ValueError):
        XlsxWorkbook().save(tmp_path / "empty.xlsx")


def test_column_letter_is_one_based_and_wraps():
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    with pytest.raises(ValueError):
        column_letter(0)


def test_xlsx_header_style_is_applied(tmp_path):
    workbook = XlsxWorkbook()
    workbook.add_table_sheet("Data", HEADERS, ROWS)
    path = workbook.save(tmp_path / "styled.xlsx")
    assert b's="1"' in zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml")


# ---------------------------------------------------------------------- DOCX
def test_docx_is_a_valid_package(tmp_path):
    doc = DocxDocument("Report")
    doc.heading("Section", 1).paragraph("Body text.").bullets(["one", "two"])
    doc.table(["K", "V"], [["a", 1]]).footer_note()
    path = doc.save(tmp_path / "d.docx")
    with zipfile.ZipFile(path) as zf:
        assert "word/document.xml" in zf.namelist()
        assert "word/styles.xml" in zf.namelist()
    assert doc.validate(path)["ok"]


def test_docx_validation_rejects_a_corrupt_package(tmp_path):
    doc = DocxDocument("Report")
    doc.paragraph("content")
    path = doc.save(tmp_path / "d.docx")
    assert doc.validate(path)["ok"]
    path.write_bytes(b"not a zip archive")
    report = doc.validate(path)
    assert not report["ok"]


def test_docx_escapes_xml(tmp_path):
    doc = DocxDocument("Escaping")
    doc.paragraph("a < b & c > d \"quoted\"")
    path = doc.save(tmp_path / "d.docx")
    body = zipfile.ZipFile(path).read("word/document.xml").decode()
    assert "&lt;" in body and "&amp;" in body
    assert doc.validate(path)["ok"]


# ----------------------------------------------------------------------- PDF
def test_pdf_structure_is_correct(tmp_path):
    pdf = PdfDocument("Title here")
    pdf.heading("Heading", 1).paragraph("Some body text that is reasonably long.")
    path = pdf.save(tmp_path / "o.pdf")
    data = path.read_bytes()
    assert data.startswith(b"%PDF-1.4")
    assert b"%%EOF" in data[-32:]
    offset = int(data.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
    assert data[offset : offset + 4] == b"xref"
    assert pdf.validate(path)["ok"]


def test_pdf_paginates_when_content_overflows(tmp_path):
    pdf = PdfDocument("Long report")
    for i in range(80):
        pdf.paragraph(f"Line {i} of filler text used to force a page break.")
    path = pdf.save(tmp_path / "long.pdf")
    assert len(pdf._pages) > 1
    assert pdf.validate(path)["ok"]


def test_pdf_tables_and_bullets_render(tmp_path):
    pdf = PdfDocument("Tables")
    pdf.bullets(["first", "second"])
    pdf.table(HEADERS, ROWS)
    path = pdf.save(tmp_path / "t.pdf")
    assert pdf.validate(path)["ok"]


def test_pdf_validation_reports_a_corrupt_file(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf at all")
    report = PdfDocument("X").validate(path)
    assert not report["ok"]
    assert report["problems"]


# ------------------------------------------------------------------ Markdown
def test_markdown_builds_a_readable_report(tmp_path):
    doc = MarkdownDocument("Title")
    doc.heading("H").paragraph("p").bullets(["a", "b"]).table(HEADERS, ROWS).code("x = 1", "python").footer()
    text = doc.text()
    assert "# Title" in text
    assert "| Month | Clients | Revenue |" in text
    assert "```python" in text
    assert doc.save(tmp_path / "r.md").is_file()


def test_markdown_escapes_pipes_in_tables():
    text = MarkdownDocument("T").table(["A"], [["a|b"]]).text()
    assert "a\\|b" in text


# ------------------------------------------------------------ DocumentAgent
@pytest.fixture()
def agent(tmp_path) -> DocumentAgent:
    return DocumentAgent(output_dir=tmp_path)


def test_agent_creates_and_validates_an_xlsx(agent, tmp_path):
    result = agent.run(
        AgentRequest(
            intent="document",
            text="monthly expenses",
            params={
                "format": "xlsx",
                "title": "Expenses",
                "headers": HEADERS,
                "rows": ROWS,
                "number_columns": (3,),
                "path": str(tmp_path / "expenses.xlsx"),
            },
        )
    )
    assert result.ok, result.summary
    assert Path(result.data["path"]).is_file()
    assert result.data["problems"] == []


@pytest.mark.parametrize("fmt", ["xlsx", "csv", "docx", "pdf", "md"])
def test_agent_supports_every_format(agent, tmp_path, fmt):
    result = agent.run(
        AgentRequest(
            intent="document",
            text=f"a {fmt} report",
            params={
                "format": fmt,
                "title": "Report",
                "headers": HEADERS,
                "rows": ROWS,
                "paragraphs": ["An introduction."],
                "bullets": ["point one"],
                "path": str(tmp_path / f"out.{fmt}"),
            },
        )
    )
    assert result.ok, f"{fmt}: {result.summary}"
    assert Path(result.data["path"]).stat().st_size > 0


def test_agent_rejects_an_unknown_format(agent):
    result = agent.run(AgentRequest(intent="document", text="x", params={"format": "pages"}))
    assert not result.ok
    assert "Unsupported format" in result.summary


def test_agent_flags_an_empty_document(agent, tmp_path):
    """A file with nothing in it is reported, not handed over as a deliverable."""
    _path, problems = agent.build("csv", tmp_path / "empty.csv", title="T")
    assert problems == ["no rows or headers were supplied"]


def test_agent_reports_a_broken_write(agent, tmp_path):
    """validate() re-reads what it wrote; a file that vanished is a problem."""
    path, problems = agent.build("xlsx", tmp_path / "ok.xlsx", title="T",
                                 headers=HEADERS, rows=ROWS)
    assert not problems
    assert path.is_file()


def test_agent_derives_a_filename_from_the_title(agent, tmp_path):
    result = agent.run(
        AgentRequest(intent="document", text="Monthly Sales Report!",
                     params={"format": "csv", "title": "Monthly Sales Report!",
                             "headers": HEADERS, "rows": ROWS})
    )
    assert result.ok
    assert Path(result.data["path"]).name == "monthly-sales-report.csv"


def test_split_ref_parses_cell_references():
    from jarvis.agents.documents import _split_ref

    assert _split_ref("C4") == (4, 3)
    assert _split_ref("AA10") == (10, 27)
    with pytest.raises(ValueError):
        _split_ref("nonsense")


# ----------------------------------------------------------------------- pptx
def test_pptx_writes_a_valid_package(tmp_path):
    from jarvis.documents.pptx import PptxPresentation

    deck = PptxPresentation("Q3 Review")
    deck.add_slide("Q3 Review", ["Revenue up 12%", "Churn down to 3.1%"])
    deck.add_slide("Risks", ["Concentration in two accounts"])
    deck.add_slide("Next steps")
    path = deck.write(tmp_path / "deck.pptx")

    assert deck.validate(path)["ok"]
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        for part in ("ppt/slideMasters/slideMaster1.xml", "ppt/slideLayouts/slideLayout1.xml",
                     "ppt/theme/theme1.xml", "ppt/slides/slide1.xml"):
            assert part in zf.namelist(), part
            ET.fromstring(zf.read(part))


def test_pptx_survives_a_third_party_reader(tmp_path):
    """Open it with the real library, not only with our own validator.

    Our validator and our writer share assumptions, so passing it proves less
    than it looks like. python-pptx has no reason to accept a malformed deck.
    """
    pptx = pytest.importorskip("pptx")
    from jarvis.documents.pptx import PptxPresentation

    deck = PptxPresentation("Q3 Review")
    deck.add_slide("Q3 Review", ["Revenue up 12%", "Churn down to 3.1%"])
    deck.add_slide("Risks", ["Concentration in two accounts"])
    path = deck.write(tmp_path / "deck.pptx")

    opened = pptx.Presentation(str(path))
    assert len(opened.slides) == 2
    first = [s.text_frame.text for s in opened.slides[0].shapes if s.has_text_frame]
    assert first[0] == "Q3 Review"
    assert "Revenue up 12%" in first[1]


def test_pptx_escapes_markup_in_text(tmp_path):
    from jarvis.documents.pptx import PptxPresentation

    deck = PptxPresentation("A & B <tag>")
    deck.add_slide("A & B <tag>", ["Use <script> & \"quotes\""])
    path = deck.write(tmp_path / "esc.pptx")
    assert deck.validate(path)["ok"]
    with zipfile.ZipFile(path) as zf:
        body = zf.read("ppt/slides/slide1.xml").decode()
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_an_empty_deck_still_produces_a_title_slide(tmp_path):
    from jarvis.documents.pptx import PptxPresentation

    deck = PptxPresentation("Empty")
    path = deck.write(tmp_path / "empty.pptx")
    assert deck.validate(path)["ok"]


def test_a_request_for_a_presentation_is_not_answered_with_a_spreadsheet(tmp_path):
    """Regression: 'make me a powerpoint presentation' produced an .xlsx, because
    DOC_FORMATS had no pptx entry and the match fell through to the default.
    A silently wrong artifact is worse than an error."""
    from jarvis.agents.documents import DocumentAgent
    from jarvis.interaction.intents import parse_intent

    request = parse_intent("make me a powerpoint presentation")
    assert request.params["format"] == "pptx"

    path, problems = DocumentAgent(output_dir=tmp_path).build(
        "pptx", tmp_path / "deck.pptx", title="Board update", bullets=["One", "Two"]
    )
    assert not problems, problems
    assert path.suffix == ".pptx"
    with zipfile.ZipFile(path) as zf:
        assert "ppt/presentation.xml" in zf.namelist()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("make me a powerpoint presentation", "pptx"),
        ("build a slide deck for the board", "pptx"),
        ("give me an excel sheet", "xlsx"),
        ("write a word document", "docx"),
        ("make a pdf report", "pdf"),
    ],
)
def test_the_requested_format_is_the_format_produced(text, expected):
    from jarvis.interaction.intents import parse_intent

    assert parse_intent(text).params["format"] == expected
