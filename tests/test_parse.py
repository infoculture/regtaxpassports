"""Проверки парсера на реальных файлах из data/raw/exports (пропускаются, если архивов нет)."""

from __future__ import annotations

import zipfile

import pytest

from regtaxpassports import config, units
from regtaxpassports.parse import detect_theme, label_key, norm_code, parse_workbook


def _passport(year: int, label: str):
    path = config.EXPORTS / f"{year}.zip"
    if not path.exists():
        pytest.skip(f"нет {path}")
    name = f"НП_{year}_{label}.xls"
    with zipfile.ZipFile(path) as z:
        return parse_workbook(z.read(name), name)


def _sheet(p, theme):
    return next(s for s in p.sheets if s.theme == theme)


def test_norm_code():
    assert norm_code(601.0) == "601"
    assert norm_code("1201.1") == "1201.1"
    assert norm_code(1201.1) == "1201.1"
    assert norm_code("-401") == "-401"
    assert norm_code("-") is None


def test_detect_theme_order():
    assert detect_theme("Раздел 12.Оценка и прогнозирование поступлений отдельных налогов") == "forecast"
    assert detect_theme("Раздел 9. Задолженность по налогам, сборам, страховым взносам, пеням") == "debt"
    assert detect_theme("Раздел 6.Поступление доходов по страховым взносам на обязательное") == "insurance_contributions"
    assert detect_theme("Раздел 11.Показатели налоговой нагрузки по отдельным видам экономической") == "tax_burden_by_activity"


def test_units():
    assert units.from_label("ВАЛОВОЙ РЕГИОНАЛЬНЫЙ ПРОДУКТ (по состоянию на 01.01.2023) (млрд. руб.)") == "млрд. руб."
    assert units.from_label("Объем добычи природного горючего газа (млн.куб.м.)") == "млн. куб. м"
    assert units.from_label("Отношение к прибыли прибыльных организаций, %") == "%"
    assert units.from_header(["Поступило в консолидированный бюджет РФ", "В % к предыдущему периоду"]) == "%"
    assert units.from_preamble(["- ВРП за 2013 год 402.56 млрд. руб."]) is None
    assert units.from_preamble(["Алтайский край млн.руб."]) == "млн. руб."


def test_label_key():
    assert label_key("ЧИСЛЕННОСТЬ НАСЕЛЕНИЯ НА 1 ЯНВАРЯ 2024 ГОДА (тыс.чел.)") == "численность населения"
    assert label_key("ВАЛОВОЙ РЕГИОНАЛЬНЫЙ ПРОДУКТ (по состоянию на 01.01.2023) (млрд. руб.)") == "валовой региональный продукт"


def test_region_2023_structure():
    p = _passport(2023, "Томская область")
    assert p.title["as_of"] == "01.01.2024"
    assert p.title["code"] == "7000"
    themes = [s.theme for s in p.sheets]
    assert "debt" not in themes  # публикация задолженности приостановлена
    s = _sheet(p, "socio_economic")
    grp = next(r for r in s.rows if r.code == "101")
    cell = next(c for c in s.cells if c.row == grp.row and c.col == 2)
    assert cell.value == pytest.approx(809.5944)
    assert not s.warnings


def test_hierarchy_from_indent():
    p = _passport(2023, "Томская область")
    rows = {r.code: r for r in _sheet(p, "revenue_by_activity").rows if r.code}
    assert rows["702"].parent_code == "701"
    assert rows["703"].parent_code == "702"
    assert rows["706"].parent_code == "705"
    assert rows["710"].parent_code == "701"
    rows = {r.code: r for r in _sheet(p, "tax_benefits").rows if r.code}
    assert rows["301"].parent_code is None
    assert rows["302"].parent_code == "301"


def test_rf_numbering_row():
    # в паспорте РФ над строкой нумерации граф есть лишняя пара «1 | 2»
    p = _passport(2023, "РОССИЙСКАЯ ФЕДЕРАЦИЯ")
    s = _sheet(p, "revenue_by_activity")
    assert not s.warnings
    assert s.columns[0].header == "Поступило в консолидированный бюджет, всего"


def test_2014_template():
    p = _passport(2014, "Томская область")
    assert [s.theme for s in p.sheets] == [
        "socio_economic", "tax_base", "tax_benefits", "tax_control", "revenue_by_budget",
        "revenue_by_activity", "debt", "debt_by_activity", "tax_burden", "tax_burden_by_activity", "forecast",
    ]
    assert all(not s.warnings for s in p.sheets)


def test_2018_title_in_second_row():
    p = _passport(2018, "Алтайский край")
    s = next(s for s in p.sheets if s.name == "Раздел1")
    assert s.theme == "socio_economic"
    assert s.title.startswith("Раздел 1.")
    assert not any(t.startswith("Раздел") for t in s.preamble)
