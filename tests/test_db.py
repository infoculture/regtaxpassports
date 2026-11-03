"""Проверки собранной базы (пропускаются, если data/db/regtaxpassports.duckdb нет)."""

from __future__ import annotations

import zipfile

import duckdb
import pytest
import xlrd

from regtaxpassports import config


@pytest.fixture(scope="module")
def con():
    if not config.DB_PATH.exists():
        pytest.skip("база не собрана: python -m regtaxpassports.build_db")
    c = duckdb.connect(str(config.DB_PATH), read_only=True)
    yield c
    c.close()


def test_all_passports_loaded(con):
    rows = con.execute("""
        SELECT e.year, e.files, count(p.territory_code)
        FROM export e LEFT JOIN passport p USING (year) GROUP BY ALL ORDER BY 1
    """).fetchall()
    assert rows and all(files == loaded for _, files, loaded in rows)


def test_every_sheet_has_theme(con):
    assert con.execute("SELECT count(*) FROM sheet WHERE theme_code IS NULL").fetchone()[0] == 0


def test_no_personal_data_sheet(con):
    assert con.execute("SELECT count(*) FROM sheet WHERE sheet_name = 'Руководители'").fetchone()[0] == 0


def test_values_match_source_cells(con):
    """Случайная выборка ячеек из БД совпадает с ячейками исходных XLS."""
    sample = con.execute("""
        SELECT v.year, p.file_name, v.sheet_name, v.row_no, v.col_no, v.value, v.value_text
        FROM value v JOIN passport p USING (year, territory_code)
        USING SAMPLE reservoir(300 ROWS) REPEATABLE (42)
    """).fetchall()
    books: dict[tuple[int, str], xlrd.Book] = {}
    for year, file_name, sheet, row_no, col_no, value, text in sample:
        if (year, file_name) not in books:
            with zipfile.ZipFile(config.EXPORTS / f"{year}.zip") as z:
                books[(year, file_name)] = xlrd.open_workbook(file_contents=z.read(file_name))
        cell = books[(year, file_name)].sheet_by_name(sheet).cell_value(row_no - 1, col_no - 1)
        if value is not None:
            assert float(str(cell).replace(" ", "").replace(",", ".")) == pytest.approx(value)
        else:
            assert str(cell).strip() == text


def test_map_indicators_match_passports(con):
    """Показатели карты портала (JSON API) в основном совпадают с ячейками паспортов."""
    share = con.execute("SELECT avg((match_share >= 0.95)::INT) FROM map_indicator_source").fetchone()[0]
    assert share > 0.9


def test_hierarchy_parent_exists(con):
    orphans = con.execute("""
        SELECT count(*) FROM sheet_row r
        WHERE r.parent_code IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM sheet_row p
            WHERE p.year = r.year AND p.territory_code = r.territory_code
              AND p.sheet_name = r.sheet_name AND p.row_code = r.parent_code)
    """).fetchone()[0]
    assert orphans == 0
