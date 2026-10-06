"""Spreadsheets: Excel workbooks (one table per sheet) and CSV files."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .base import Context, ExtractedDoc, md_table

MAX_TABLE_ROWS = 500

def extract_xlsx(path: Path, ctx: Context) -> ExtractedDoc:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v) for v in row][:30])
                if len(rows) > MAX_TABLE_ROWS:
                    break
            parts.append(f"## Sheet: {ws.title}")
            parts.append(md_table(rows) or "_(empty)_")
    finally:
        wb.close()
    return ExtractedDoc(title=path.stem, markdown="\n\n".join(parts))


def extract_csv(path: Path, ctx: Context) -> ExtractedDoc:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        rows.append(row[:30])
        if len(rows) > MAX_TABLE_ROWS:
            break
    return ExtractedDoc(title=path.stem, markdown=md_table(rows))
