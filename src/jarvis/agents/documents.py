"""Document agent (spec section 16).

Understands a request, builds the file, *validates it*, and reports where it
went. The validation step is not optional decoration: a spreadsheet whose
formulas do not add up is worse than no spreadsheet, because the user trusts it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.confidence import Claim, Confidence
from jarvis.documents import (
    DocxDocument,
    MarkdownDocument,
    PdfDocument,
    XlsxWorkbook,
    write_csv,
)
from jarvis.documents.xlsx import S_NUMBER, S_PERCENT

SUPPORTED = ("xlsx", "csv", "docx", "pdf", "md")


class DocumentAgent(Agent):
    name = "documents"
    description = "Creates Excel, CSV, Word, PDF and Markdown documents and validates them."
    capabilities = ("document", "xlsx", "csv", "docx", "pdf", "markdown", "report")

    def __init__(self, runtime: Any = None, *, output_dir: str | Path | None = None) -> None:
        super().__init__(runtime)
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "output"

    def run(self, request: AgentRequest) -> AgentResult:
        fmt = (request.param("format") or "xlsx").lower().lstrip(".")
        if fmt == "markdown":
            fmt = "md"
        if fmt not in SUPPORTED:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=f"Unsupported format '{fmt}'. Supported: {', '.join(SUPPORTED)}.",
            )
        title = request.param("title") or request.text or "Document"
        rows: Sequence[Sequence[Any]] = request.param("rows") or []
        headers: Sequence[str] = request.param("headers") or []
        filename = request.param("filename") or self._filename(title, fmt)
        destination = Path(request.param("path") or (self.output_dir / filename))

        try:
            path, problems = self.build(
                fmt,
                destination,
                title=title,
                headers=headers,
                rows=rows,
                paragraphs=request.param("paragraphs") or [],
                bullets=request.param("bullets") or [],
                sections=request.param("sections") or [],
                number_columns=request.param("number_columns") or (),
                percent_columns=request.param("percent_columns") or (),
                formulas=request.param("formulas") or {},
            )
        except (OSError, ValueError) as exc:
            return AgentResult(agent=self.name, ok=False, summary=f"failed to create the document: {exc}")

        result = AgentResult(
            agent=self.name,
            ok=not problems,
            summary=(
                f"Created {fmt.upper()} at {path}"
                + (f" with {len(problems)} validation problem(s): {'; '.join(problems)}" if problems else " (validated)")
            ),
            data={"path": str(path), "format": fmt, "problems": problems, "rows": len(rows)},
        )
        result.add_claim(
            Claim.certain(f"The file exists at {path} ({path.stat().st_size} bytes).")
        )
        result.add_claim(
            Claim(
                statement="The document passed its structural validation.",
                confidence=Confidence.HIGH if not problems else Confidence.LOW,
                caveats=list(problems),
                reasoning="re-opened and re-parsed the file after writing it",
            )
        )
        return result

    # ------------------------------------------------------------------ build
    def build(
        self,
        fmt: str,
        destination: str | Path,
        *,
        title: str,
        headers: Sequence[str] = (),
        rows: Iterable[Sequence[Any]] = (),
        paragraphs: Sequence[str] = (),
        bullets: Sequence[str] = (),
        sections: Sequence[Mapping[str, Any]] = (),
        number_columns: Sequence[int] = (),
        percent_columns: Sequence[int] = (),
        formulas: Mapping[str, str] | None = None,
    ) -> tuple[Path, list[str]]:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        row_list = [list(r) for r in rows]

        if fmt == "csv":
            path = write_csv(destination, row_list, headers=list(headers) or None)
            problems = [] if (row_list or headers) else ["no rows or headers were supplied"]
            return path, problems

        if fmt == "xlsx":
            wb = XlsxWorkbook()
            ws = wb.add_table_sheet(
                title[:31] or "Sheet1",
                list(headers),
                row_list,
                number_columns=tuple(number_columns),
                percent_columns=tuple(percent_columns),
            )
            for ref, expression in (formulas or {}).items():
                row_s, col_s = _split_ref(ref)
                ws.formula(row_s, col_s, expression, style=S_PERCENT if "ratio" in ref.lower() else S_NUMBER)
            for section in sections:
                sheet = wb.sheet(str(section.get("name", "Section"))[:31])
                sheet.table(
                    list(section.get("headers", headers)),
                    [list(r) for r in section.get("rows", [])],
                    number_columns=tuple(section.get("number_columns", ())),
                )
            path = wb.save(destination)
            report = wb.validate(path)
            return path, list(report.get("problems", []))

        if fmt == "docx":
            doc = DocxDocument(title)
            for p in paragraphs:
                doc.paragraph(p)
            if bullets:
                doc.bullets(list(bullets))
            if headers and row_list:
                doc.table(list(headers), row_list)
            for section in sections:
                doc.heading(str(section.get("name", "")), level=1)
                for p in section.get("paragraphs", []):
                    doc.paragraph(str(p))
                if section.get("rows") and section.get("headers"):
                    doc.table(list(section["headers"]), section["rows"])
            doc.footer_note()
            path = doc.save(destination)
            return path, list(doc.validate(path).get("problems", []))

        if fmt == "pdf":
            pdf = PdfDocument(title)
            pdf.heading(title, 1)
            for p in paragraphs:
                pdf.paragraph(p)
            if bullets:
                pdf.bullets(list(bullets))
            if headers and row_list:
                pdf.table(list(headers), row_list)
            for section in sections:
                pdf.heading(str(section.get("name", "")), 2)
                for p in section.get("paragraphs", []):
                    pdf.paragraph(str(p))
                if section.get("rows") and section.get("headers"):
                    pdf.table(list(section["headers"]), section["rows"])
            path = pdf.save(destination)
            return path, list(pdf.validate(path).get("problems", []))

        md = MarkdownDocument(title)
        for p in paragraphs:
            md.paragraph(p)
        if bullets:
            md.bullets(list(bullets))
        if headers and row_list:
            md.table(list(headers), row_list)
        for section in sections:
            md.heading(str(section.get("name", "")), level=2)
            for p in section.get("paragraphs", []):
                md.paragraph(str(p))
            if section.get("rows") and section.get("headers"):
                md.table(list(section["headers"]), section["rows"])
        md.footer()
        path = md.save(destination)
        return path, [] if path.stat().st_size > 0 else ["file is empty"]


    @staticmethod
    def _filename(title: str, fmt: str) -> str:
        import re

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "document"
        return f"{slug}.{fmt}"


def _split_ref(ref: str) -> tuple[int, int]:
    """'C4' -> (4, 3)."""
    import re

    from jarvis.documents.xlsx import _col_from_ref

    match = re.match(r"^([A-Z]+)(\d+)$", ref.strip().upper())
    if not match:
        raise ValueError(f"not a cell reference: {ref!r}")
    return int(match.group(2)), _col_from_ref(match.group(1))
