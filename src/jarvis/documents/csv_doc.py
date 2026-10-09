"""CSV writer with validation."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def write_csv(
    path: str | Path,
    rows: Sequence[Sequence[Any]],
    *,
    headers: Sequence[str] | None = None,
    delimiter: str = ",",
) -> Path:
    """Write rows to CSV, optionally with a header row. Returns the path written."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        if headers:
            writer.writerow(list(headers))
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    if not dest.is_file():
        raise OSError(f"failed to write CSV to {dest}")
    return dest


def read_csv(path: str | Path, *, delimiter: str = ",") -> list[list[str]]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.reader(fh, delimiter=delimiter)]
