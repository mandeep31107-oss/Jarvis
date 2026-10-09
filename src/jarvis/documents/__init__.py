"""Document generation (spec section 16).

Excel, Word, PDF, CSV and Markdown writers implemented on the standard library
only, so Jarvis can produce real deliverables on a machine with no third-party
packages installed. When ``openpyxl`` / ``python-pptx`` are present the
DocumentAgent prefers them for richer output, but nothing here *requires* them.

Every writer validates its own output before returning: a workbook that cannot
be re-read is a bug, not a deliverable.
"""

from jarvis.documents.csv_doc import write_csv
from jarvis.documents.docx import DocxDocument
from jarvis.documents.markdown_doc import MarkdownDocument
from jarvis.documents.pdf import PdfDocument
from jarvis.documents.xlsx import XlsxWorkbook, read_xlsx

__all__ = [
    "DocxDocument",
    "MarkdownDocument",
    "PdfDocument",
    "XlsxWorkbook",
    "read_xlsx",
    "write_csv",
]
