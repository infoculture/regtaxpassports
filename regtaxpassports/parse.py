"""Разбор XLS-файлов налоговых паспортов в табличные записи.

Каждый лист «РазделN» устроен одинаково во всех годах:

    строка 0           заголовок раздела («Раздел 5.Поступления ...»)
    строки 1..h-1      преамбула: территория, «ПЕРИОД: ...», «Справочно: ...»
    строка h           «ПОКАЗАТЕЛЬ» | «Код строки» | шапка граф (могут быть объединённые ячейки)
    строки h+1..n-1    нижние уровни шапки
    строка n           нумерация граф: 1 | 2 | 3 | ...
    строки n+1..       показатели: наименование | код строки | значения по графам

Номер листа не определяет содержание: в разные годы «Раздел8» означает разное.
Содержание раздела определяется по заголовку (см. THEMES).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import xlrd

# Канонические темы разделов. Номер темы совпадает с номером раздела в
# действующем шаблоне и с mapId справочника /api/TaxPassports/Maps
# (12 — прогноз, на карте портала его нет). Порядок проверки важен.
THEMES: list[tuple[str, int, str, str]] = [
    # (код темы, номер, шаблон заголовка, название)
    ("officials", 0, r"руководител", "Руководители федерального округа, администрации субъекта и УФНС"),
    ("socio_economic", 1, r"социально-экономическ", "Социально-экономическая характеристика"),
    ("tax_base", 2, r"налоговой базы", "Основные показатели налоговой базы"),
    ("tax_benefits", 3, r"льготы", "Льготы по отдельным администрируемым доходам"),
    ("tax_control", 4, r"контрольной работы", "Основные показатели контрольной работы"),
    ("forecast", 12, r"оценка и прогноз", "Оценка и прогнозирование поступлений"),
    ("debt_by_activity", 9, r"задолженност.*видам экономическ", "Задолженность по видам экономической деятельности"),
    ("debt", 8, r"задолженност", "Задолженность по налогам и сборам"),
    ("tax_burden_by_activity", 11, r"налоговой нагрузки.*видам экономическ", "Налоговая нагрузка по видам экономической деятельности"),
    ("tax_burden", 10, r"налоговой нагрузки", "Налоговая нагрузка"),
    ("insurance_contributions", 6, r"страховым\s+взносам", "Поступления по страховым взносам"),
    ("revenue_by_activity", 7, r"поступлени.*видов экономической деятельности", "Поступления по видам экономической деятельности"),
    ("revenue_by_budget", 5, r"поступлени", "Поступления по уровням бюджетной системы"),
]

MISSING_MARKERS = {"X", "Х", "x", "х", "-", "–", "—", "*"}

QUALIFIER_RE = re.compile(r"^(в\s+том\s+числе|из\s+н(их|ее|её|его)|из\s+стр\.?\s*\d+|справочно)\b[^А-Яа-яЁё]*", re.I)
UNIT_RE = re.compile(
    r"\((?P<u>(?:млрд|млн|тыс)\.?\s*(?:руб|рублей|долларов|чел|тонн|куб|кв)[^()]*|%|единиц[а-я]*|ед\.?|руб\.?|литр[а-я]*|копеек[^()]*|чел\.?)\)",
    re.I,
)


@dataclass
class Column:
    col: int
    graph_no: str | None
    header: str            # уровни шапки через « → »
    levels: list[str]


@dataclass
class Row:
    row: int
    code: str | None
    label: str
    is_hidden: bool
    kind: str               # indicator | heading | qualifier
    level: int              # отступ наименования (уровень вложенности)
    heading: str | None     # ближайший заголовок группы (строка без кода)
    parent_code: str | None  # код родительского показателя (см. _assign_parents)


@dataclass
class Cell:
    row: int
    col: int
    value: float | None
    raw: str | None         # исходный текст, если значение не число


@dataclass
class Sheet:
    name: str
    title: str
    theme: str | None
    preamble: list[str]
    columns: list[Column]
    rows: list[Row]
    cells: list[Cell]
    header_row: int | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Passport:
    file_name: str
    file_label: str          # территория из имени файла
    year: int
    title: dict[str, str]    # поля листа ТИТУЛ
    contents: list[tuple[str, str]]  # лист «Содержание»: (имя листа, заголовок)
    sheets: list[Sheet]


def norm_text(v) -> str:
    if v is None:
        return ""
    s = str(v).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def norm_code(v) -> str | None:
    """Код строки: 601.0 -> '601', 1201.1 -> '1201.1', '-401' -> '-401'."""
    if v is None or v == "":
        return None
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.4f}".rstrip("0").rstrip(".")
    s = norm_text(v)
    if not s or s in MISSING_MARKERS:
        return None
    try:
        return norm_code(float(s.replace(",", ".")))
    except ValueError:
        return s


def label_key(label: str) -> str:
    """Наименование без дат, лет, единиц и пунктуации — для сопоставления показателей
    между годами, когда коды строк сдвигаются."""
    from . import units

    s = label.lower().replace("ё", "е")
    s = re.sub(r"\(по состоянию на[^)]*\)", " ", s)
    s = re.sub(r"\(([^()]*)\)", lambda m: " " if units.normalize(m.group(1)) else m.group(0), s)
    s = re.sub(r"\b(на\s+)?\d{1,2}\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*\s+(19|20)\d{2}\s*(года?)?", " ", s)
    s = re.sub(r"\b(19|20)\d{2}\b(\s*год[а-я]*)?", " ", s)
    s = re.sub(r"[^0-9a-zа-я%]+", " ", s)
    return s.strip()


def detect_theme(title: str) -> str | None:
    t = title.lower()
    for code, _, pattern, _ in THEMES:
        if re.search(pattern, t):
            return code
    return None


def parse_unit(text: str) -> str | None:
    m = None
    for m in UNIT_RE.finditer(text):
        pass  # берём последнюю скобку: «(по состоянию на 01.01.2023) (млрд. руб.)»
    return norm_text(m.group("u")).lower() if m else None


def _cell_value(sh: xlrd.sheet.Sheet, r: int, c: int):
    cell = sh.cell(r, c)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return "#" + xlrd.error_text_from_code.get(cell.value, "ERR")
    return cell.value


def _to_number(v) -> tuple[float | None, str | None]:
    if v is None:
        return None, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v), None
    s = norm_text(v)
    if not s:
        return None, None
    try:
        return float(s.replace(" ", "").replace(",", ".")), None
    except ValueError:
        return None, s


def _merged_lookup(sh) -> dict[tuple[int, int], tuple[int, int]]:
    out = {}
    for r0, r1, c0, c1 in sh.merged_cells:
        for r in range(r0, r1):
            for c in range(c0, c1):
                out[(r, c)] = (r0, c0)
    return out


def _is_graph_numbering(sh, r: int) -> bool:
    # Проверяем три ячейки: в шапке паспорта РФ встречается лишняя пара «1 | 2».
    expected = ["1", "2", "3"][: min(sh.ncols, 3)]
    return [norm_code(_cell_value(sh, r, c)) for c in range(len(expected))] == expected


def _assign_parents(rows: list[Row]) -> None:
    """Иерархия «итог → в том числе/из них» задана в шаблоне отступом ячейки
    с наименованием (XF indent_level). Родитель — ближайший предшествующий
    показатель с меньшим отступом; строки-квалификаторы и заголовки групп
    родителями не бывают. Служебные строки с отрицательным кодом пропускаются.
    """
    stack: list[tuple[int, str | None]] = []
    for row in rows:
        while stack and stack[-1][0] >= row.level:
            stack.pop()
        if row.kind == "indicator":
            row.parent_code = next((code for _, code in reversed(stack) if code), None)
        is_parent = row.kind == "indicator" and row.code and not row.code.startswith("-")
        stack.append((row.level, row.code if is_parent else None))


def _sheet_title(sh) -> str:
    # Обычно заголовок в A1, в 2018 году — в A2.
    for r in range(min(sh.nrows, 4)):
        t = norm_text(_cell_value(sh, r, 0))
        if t.lower().startswith(("раздел", "руководители")):
            return t
    return norm_text(_cell_value(sh, 0, 0)) if sh.nrows else ""


def parse_section_sheet(wb, sh, contents_title: str | None = None) -> Sheet:
    title = _sheet_title(sh) or (contents_title or "")
    hidden = {r for r, info in sh.rowinfo_map.items() if info.hidden}
    warnings: list[str] = []

    header_row = None
    for r in range(min(sh.nrows, 40)):
        c0 = norm_text(_cell_value(sh, r, 0)).upper()
        c1 = norm_text(_cell_value(sh, r, 1)).lower() if sh.ncols > 1 else ""
        if c0.startswith("ПОКАЗАТЕЛ") or "код строки" in c1:
            header_row = r
            break
    theme = detect_theme(title) or (detect_theme(contents_title) if contents_title else None)
    if header_row is None:
        return Sheet(sh.name, title, theme, [], [], [], [], None, ["не найдена шапка"])

    num_row = None
    for r in range(header_row + 1, min(sh.nrows, header_row + 8)):
        if _is_graph_numbering(sh, r):
            num_row = r
            break
    if num_row is None:
        warnings.append("не найдена строка нумерации граф")
        num_row = header_row

    title_row = next((r for r in range(min(sh.nrows, 4)) if norm_text(_cell_value(sh, r, 0)) == title), 0)
    preamble = []
    for r in range(title_row + 1, header_row):
        texts = []
        for c in range(sh.ncols):
            t = norm_text(_cell_value(sh, r, c))
            if t and t not in MISSING_MARKERS and t not in texts:
                texts.append(t)
        if texts:
            preamble.append(" ".join(texts))

    merged = _merged_lookup(sh)
    columns: list[Column] = []
    for c in range(2, sh.ncols):
        levels: list[str] = []
        for r in range(header_row, num_row if num_row > header_row else header_row + 1):
            src = merged.get((r, c), (r, c))
            t = norm_text(_cell_value(sh, *src))
            if t and (not levels or levels[-1] != t):
                levels.append(t)
        graph = norm_code(_cell_value(sh, num_row, c)) if num_row > header_row else None
        if levels or graph:
            columns.append(Column(c, graph, " → ".join(levels), levels))
    known_cols = {col.col for col in columns}

    rows: list[Row] = []
    cells: list[Cell] = []
    heading: str | None = None
    for r in range(num_row + 1, sh.nrows):
        label = norm_text(_cell_value(sh, r, 0))
        code = norm_code(_cell_value(sh, r, 1)) if sh.ncols > 1 else None
        row_cells = []
        for c in range(2, sh.ncols):
            num, raw = _to_number(_cell_value(sh, r, c))
            if num is None and raw is None:
                continue
            if c not in known_cols:
                warnings.append(f"значение вне шапки: строка {r}, графа {c}")
                continue
            row_cells.append(Cell(r, c, num, raw))
        if code is None and all(c.raw in MISSING_MARKERS for c in row_cells):
            row_cells = []  # «в том числе:» | - | -
        if not label and not code and not row_cells:
            continue
        if code is None and not row_cells:
            kind = "qualifier" if QUALIFIER_RE.match(label) or label.lower().startswith("из стр") else "heading"
            if kind == "heading":
                heading = label
        else:
            kind = "indicator"
            if code is None:
                warnings.append(f"значения без кода строки: строка {r} «{label[:40]}»")
        level = wb.xf_list[sh.cell_xf_index(r, 0)].alignment.indent_level
        rows.append(Row(r, code, label, r in hidden, kind, level, heading if kind == "indicator" else None, None))
        cells.extend(row_cells)
    _assign_parents(rows)
    return Sheet(sh.name, title, theme, preamble, columns, rows, cells, header_row, warnings)


def _unspace(s: str) -> str:
    """«Н А Л О Г О В Ы Й   П А С П О Р Т» -> «НАЛОГОВЫЙ ПАСПОРТ»."""
    words = re.split(r"\s{2,}", s.strip())
    if words and all(len(w) == 1 for w in words[0].split(" ")):
        return " ".join(w.replace(" ", "") for w in words)
    return norm_text(s)


def parse_title(sh) -> dict[str, str]:
    """Лист ТИТУЛ: дата среза, заголовок документа, пары «поле — значение»."""
    out: dict[str, str] = {}
    doc_title: list[str] = []
    for r in range(sh.nrows):
        raw = []
        for c in range(sh.ncols):
            v = _cell_value(sh, r, c)
            if v not in (None, "") and str(v).strip() and v not in raw:
                raw.append(v)
        if not raw:
            continue
        line = norm_text(raw[0])
        m = re.search(r"ПО СОСТОЯНИЮ НА\s+(\d{2}\.\d{2}\.\d{4})", line, re.I)
        if m:
            out["as_of"] = m.group(1)
        elif len(raw) >= 2:
            key, val = line.upper(), raw[1]
            if key.startswith("КОД"):
                out["code"] = norm_code(val) or norm_text(val)
            elif "ЦЕНТР" in key:
                out["admin_center"] = norm_text(val)
            elif key.startswith("СУБЪЕКТ") or key.startswith("ФЕДЕРАЛЬНЫЙ ОКРУГ РОССИЙСКОЙ"):
                out["territory"] = norm_text(val)
            elif "ОКРУГ" in key:
                out["federal_district"] = norm_text(val)
        else:
            text = _unspace(str(raw[0]))
            # шапка ведомства: «ФЕДЕРАЛЬНАЯ НАЛОГОВАЯ СЛУЖБА [РОССИЙСКОЙ ФЕДЕРАЦИИ]»
            if not text.upper().startswith("ФЕДЕРАЛЬНАЯ НАЛОГОВАЯ СЛУЖБА"):
                doc_title.append(text)
    if doc_title:
        out["doc_title"] = " ".join(doc_title)
    return out


def parse_contents(sh) -> list[tuple[str, str]]:
    out = []
    for r in range(sh.nrows):
        name = norm_text(_cell_value(sh, r, 1)) if sh.ncols > 1 else ""
        text = norm_text(_cell_value(sh, r, 2)) if sh.ncols > 2 else ""
        if name and text and name != "Содержание":
            out.append((name, text))
    return out


FILE_RE = re.compile(r"^НП_(?P<year>\d{4})_(?P<label>.+)\.xls$")


def parse_workbook(content: bytes, file_name: str, skip_sheets: set[str] = frozenset({"Руководители"})) -> Passport:
    m = FILE_RE.match(file_name)
    if not m:
        raise ValueError(f"неожиданное имя файла: {file_name}")
    wb = xlrd.open_workbook(file_contents=content, formatting_info=True)
    title: dict[str, str] = {}
    contents: list[tuple[str, str]] = []
    sheets: list[Sheet] = []
    names = wb.sheet_names()
    if "ТИТУЛ" in names:
        title = parse_title(wb.sheet_by_name("ТИТУЛ"))
    if "Содержание" in names:
        contents = parse_contents(wb.sheet_by_name("Содержание"))
    toc = dict(contents)
    for sh in wb.sheets():
        if sh.name in ("ТИТУЛ", "Содержание") or sh.name in skip_sheets:
            continue
        sheets.append(parse_section_sheet(wb, sh, toc.get(sh.name)))
    # В 2017–2022 заголовок раздела «Задолженность ... по видам экономической
    # деятельности» не содержит слов «по видам»; различаем по составу строк.
    for s in sheets:
        if s.theme == "debt" and any("добыча полезных ископаемых" in r.label.lower() for r in s.rows):
            s.theme = "debt_by_activity"
    return Passport(file_name, m.group("label"), int(m.group("year")), title, contents, sheets)


def iter_zip(path: Path) -> Iterator[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.filename.lower().endswith(".xls"):
                yield info.filename, z.read(info)
