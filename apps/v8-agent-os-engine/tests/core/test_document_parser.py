from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core import document_parser as document_parser_module
from core.document_parser import DocumentParser


class _TabulateProbe:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def tabulate(self, rows, **options):
        self.calls.append({"rows": rows, "options": options})
        return "rendered-table"


def test_csv_parser_uses_builtin_reader_and_primary_tabulate_path(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "sample.csv"
    source.write_text("name,value\nalpha,7\n", encoding="utf-8")
    table = _TabulateProbe()
    monkeypatch.setattr(document_parser_module, "tabulate", table)

    result = DocumentParser._parse_csv(source)

    assert result == "rendered-table"
    assert table.calls == [{
        "rows": [["alpha", "7"]],
        "options": {
            "headers": ["name", "value"],
            "tablefmt": "pipe",
            "disable_numparse": True,
        },
    }]


def test_xlsx_parser_reads_values_without_pandas(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "sample.xlsx"
    source.write_bytes(b"xlsx-placeholder")
    table = _TabulateProbe()
    closed: list[bool] = []

    class _Worksheet:
        def iter_rows(self, *, values_only):
            assert values_only is True
            return iter((("name", "value"), ("alpha", 7)))

    workbook = SimpleNamespace(active=_Worksheet(), close=lambda: closed.append(True))

    def load_workbook(path, *, read_only, data_only):
        assert path == source
        assert read_only is True
        assert data_only is True
        return workbook

    monkeypatch.setattr(document_parser_module, "openpyxl", SimpleNamespace(load_workbook=load_workbook))
    monkeypatch.setattr(document_parser_module, "tabulate", table)

    result = DocumentParser._parse_excel(source)

    assert result == "rendered-table"
    assert closed == [True]
    assert table.calls[0]["rows"] == [["alpha", 7]]
    assert table.calls[0]["options"]["headers"] == ["name", "value"]


def test_xls_parser_reads_first_sheet_and_releases_resources(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "sample.xls"
    source.write_bytes(b"xls-placeholder")
    table = _TabulateProbe()
    released: list[bool] = []
    worksheet = SimpleNamespace(
        nrows=2,
        row_values=lambda index: [["name", "value"], ["alpha", 7]][index],
    )
    workbook = SimpleNamespace(
        sheet_by_index=lambda index: worksheet if index == 0 else None,
        release_resources=lambda: released.append(True),
    )

    def open_workbook(path, *, on_demand):
        assert path == source
        assert on_demand is True
        return workbook

    monkeypatch.setattr(document_parser_module, "xlrd", SimpleNamespace(open_workbook=open_workbook))
    monkeypatch.setattr(document_parser_module, "tabulate", table)

    result = DocumentParser._parse_excel(source)

    assert result == "rendered-table"
    assert released == [True]
    assert table.calls[0]["rows"] == [["alpha", 7]]
    assert table.calls[0]["options"]["headers"] == ["name", "value"]


def test_docx_parser_preserves_paragraph_and_table_order(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "daily-report.docx"
    source.write_bytes(b"docx-placeholder")

    def paragraph(text: str, style: str = "Normal"):
        return SimpleNamespace(text=text, style=SimpleNamespace(name=style))

    def cell(*values: str):
        return SimpleNamespace(paragraphs=[paragraph(value) for value in values])

    table = SimpleNamespace(rows=[
        SimpleNamespace(cells=[cell("Category"), cell("Work item")]),
        SimpleNamespace(cells=[cell("Operations"), cell("Conference support", "Network maintenance")]),
    ])
    document = SimpleNamespace(iter_inner_content=lambda: iter([
        paragraph("Daily report", "Heading 1"),
        paragraph("Date: 2026.08.19"),
        table,
        paragraph("End of report"),
    ]))
    monkeypatch.setattr(document_parser_module, "Document", lambda path: document)

    result = DocumentParser._parse_docx(source)

    assert "# Daily report" in result
    assert "| Category | Work item |" in result
    assert "| Operations | Conference support<br>Network maintenance |" in result
    assert result.index("Date: 2026.08.19") < result.index("| Category")
    assert result.index("| Operations") < result.index("End of report")
