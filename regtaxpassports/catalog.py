"""Генерация каталога показателей docs/catalog/*.md из собранной базы.

    python -m regtaxpassports.catalog

Для каждой темы раздела: где она лежит в разные годы, какие графы и какие
показатели (коды строк) в ней есть. Файлы перезаписываются целиком — правки
вносите в этот генератор, а не в результат.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import duckdb

from . import config

OUT = config.ROOT / "docs" / "catalog"
HEADER = "<!-- Файл сгенерирован: python -m regtaxpassports.catalog. Не редактируйте вручную. -->\n\n"


def year_ranges(years) -> str:
    ys = sorted(set(years))
    if not ys:
        return ""
    parts, start, prev = [], ys[0], ys[0]
    for y in ys[1:] + [None]:
        if y is not None and y == prev + 1:
            prev = y
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        if y is not None:
            start = prev = y
    return ", ".join(parts)


def relative_years(text: str, year: int) -> str:
    """«2024 год», «01.01.2025» в паспорте 2024 года -> «{Y} год», «01.01.{Y+1}»."""
    def repl(m):
        d = int(m.group(0)) - year
        return "{Y}" if d == 0 else f"{{Y{d:+d}}}"
    return re.sub(r"(?<!\d)(19|20)\d{2}(?!\d)", repl, text or "")


def _md(text) -> str:
    return "" if text is None else str(text).replace("|", "\\|").replace("\n", " ")


def _table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(_md(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def theme_page(con, theme: dict) -> str:
    code = theme["theme_code"]
    lines = [HEADER, f"# {theme['name']}\n\n"]
    lines.append(f"Код темы: `{code}`. Номер раздела в действующем шаблоне: {theme['theme_no']}.\n\n")
    if theme["portal_name"]:
        lines.append(f"На портале: «{theme['portal_name']}».")
        if theme["portal_hint"]:
            lines.append(f" Пояснение портала: «{theme['portal_hint']}».")
        lines.append("\n\n")
    if theme["default_unit"]:
        lines.append(f"Если единица не указана в файле, значения раздела приняты в «{theme['default_unit']}» "
                     "(`unit_source = 'theme_default'`, см. docs/source-format.md).\n\n")

    years = con.execute("""
        SELECT year, sheet_name, mode(title) AS title, count(*) AS passports
        FROM sheet WHERE theme_code = ? GROUP BY ALL ORDER BY year
    """, [code]).fetchall()
    stats = {r[0]: r[1:] for r in con.execute("""
        SELECT year, count(DISTINCT row_code), max(col_no) - 2, count(*)
        FROM value JOIN sheet USING (year, territory_code, sheet_name)
        WHERE theme_code = ? GROUP BY 1
    """, [code]).fetchall()}
    lines.append("## Где находится раздел\n\n")
    lines.append(_table(["Год", "Лист", "Заголовок", "Паспортов", "Показателей", "Граф значений", "Значений"],
                        [[y, s, t, n, *stats.get(y, ("", "", ""))] for y, s, t, n in years]))

    # графы: группируем годы с одинаковым набором шапок
    cols = defaultdict(list)
    for year, col_no, header, unit, variants in con.execute("""
        SELECT year, col_no, header, unit, header_variants FROM v_column
        WHERE theme_code = ? ORDER BY year, col_no
    """, [code]).fetchall():
        cols[year].append((col_no, relative_years(header, year), unit, variants))
    groups: dict[tuple, list[int]] = defaultdict(list)
    for year, cs in cols.items():
        groups[tuple((c, h, u) for c, h, u, _ in cs)].append(year)
    lines.append("\n## Графы\n\nГрафа 1 — наименование показателя, графа 2 — код строки. "
                 "Шапка приведена у большинства территорий; уровни объединённых ячеек разделены « → ». "
                 "`{Y}` — год паспорта, `{Y-1}`, `{Y+1}` — соседние годы.\n\n")
    for sig, ys in sorted(groups.items(), key=lambda kv: kv[1][0]):
        varying = {c for y in ys for c, _, _, v in cols[y] if v > 1}
        lines.append(f"### {year_ranges(ys)}\n\n")
        lines.append(_table(["Графа", "Шапка", "Единица"],
                            [[c, h + (" ¹" if c in varying else ""), u] for c, h, u in sig]))
        if varying:
            lines.append("\n¹ У части территорий шапка графы отличается (обычно у паспортов РФ и федеральных округов).\n")
        lines.append("\n")

    # показатели: последний год темы + годы присутствия по нормализованному наименованию
    ind = con.execute("""
        SELECT year, row_code, label, label_key, unit, level, is_hidden
        FROM v_indicator WHERE theme_code = ?
    """, [code]).fetchall()
    present = defaultdict(set)
    codes = defaultdict(set)
    for year, row_code, _, key, *_ in ind:
        present[key].add(year)
        codes[key].add(row_code)
    last = max(y for y, *_ in ind) if ind else None

    def sort_key(code_):
        # служебные строки с отрицательным кодом — в конце
        try:
            v = float(code_)
        except (TypeError, ValueError):
            return (2, 0.0)
        return (1, -v) if v < 0 else (0, v)

    lines.append(f"## Показатели {last} года\n\n"
                 "«Годы» — в каких паспортах есть показатель с тем же нормализованным наименованием "
                 "(`label_key`); коды строк в разные годы могут отличаться.\n\n")
    rows = []
    for year, row_code, label, key, unit, level, hidden in sorted((r for r in ind if r[0] == last), key=lambda r: sort_key(r[1])):
        other = sorted(codes[key] - {row_code}, key=sort_key)
        rows.append([row_code, ("&nbsp;" * 2 * (level or 0)) + label + (" (скрытая строка)" if hidden else ""),
                     unit, year_ranges(present[key]), ", ".join(other)])
    lines.append(_table(["Код", "Наименование", "Единица", "Годы", "Другие коды"], rows))

    gone = defaultdict(list)
    for year, row_code, label, key, unit, *_ in ind:
        if last not in present[key]:
            gone[key].append((year, row_code, label, unit))
    if gone:
        lines.append(f"\n## Показатели, которых нет в {last} году\n\n")
        rows = []
        for key, items in sorted(gone.items(), key=lambda kv: (min(i[0] for i in kv[1]), sort_key(kv[1][-1][1]))):
            y, row_code, label, unit = max(items)
            rows.append([year_ranges(i[0] for i in items), ", ".join(sorted({i[1] for i in items}, key=sort_key)), label, unit])
        lines.append(_table(["Годы", "Коды", "Наименование (последнее)", "Единица"], rows))
    return "".join(lines)


def map_page(con) -> str:
    lines = [HEADER, "# Показатели карты портала\n\n",
             "Портал выводит на карте субъектов набор ключевых показателей (методы `Indicators` и "
             "`IndicatorData`). Идентификатор показателя стабилен между годами, поэтому эти ряды "
             "удобны для сравнения. Таблица `map_indicator_source` указывает ячейку паспорта, "
             "с которой совпадают значения API; доля совпадений ниже 1 означает, что портал "
             "считает показатель иначе (например, суммой нескольких строк).\n\n"]
    rows = con.execute("""
        SELECT i.indicator_id, mode(i.name), mode(th.theme_code),
               list(DISTINCT i.year ORDER BY i.year),
               mode(i.scale), mode(i.unit_value),
               string_agg(DISTINCT s.sheet_name || ':' || s.row_code || ':' || s.col_no, '; '
                          ORDER BY s.sheet_name || ':' || s.row_code || ':' || s.col_no)
                   FILTER (WHERE s.match_share >= 0.95),
               list(DISTINCT i.year ORDER BY i.year) FILTER (WHERE coalesce(s.match_share, 0) < 0.95)
        FROM map_indicator i
        JOIN theme th ON th.theme_no = i.theme_no
        LEFT JOIN map_indicator_source s USING (year, indicator_id)
        GROUP BY i.indicator_id ORDER BY i.indicator_id
    """).fetchall()
    lines.append(_table(["id", "Показатель", "Тема", "Годы", "Множитель", "Единица",
                         "Ячейка паспорта (лист:код:графа)", "Годы без точного совпадения"],
                        [[i, n, t, year_ranges(ys), sc, u, src, year_ranges(bad or [])]
                         for i, n, t, ys, sc, u, src, bad in rows]))
    return "".join(lines)


def build_catalog(out: Path = OUT) -> list[Path]:
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    out.mkdir(parents=True, exist_ok=True)
    themes = [dict(zip([d[0] for d in con.description], r))
              for r in con.execute("SELECT * FROM theme WHERE theme_no > 0 ORDER BY theme_no").fetchall()]
    written = []
    index = [HEADER, "# Каталог показателей налоговых паспортов\n\n",
             "Страница на каждую тему раздела. Темы пронумерованы по действующему шаблону паспорта; "
             "в ранних годах тот же раздел может стоять на другом листе.\n\n"]
    rows = []
    for th in themes:
        years = [r[0] for r in con.execute("SELECT DISTINCT year FROM sheet WHERE theme_code = ? ORDER BY 1",
                                           [th["theme_code"]]).fetchall()]
        path = out / f"{th['theme_no']:02d}-{th['theme_code']}.md"
        path.write_text(theme_page(con, th), encoding="utf-8")
        written.append(path)
        rows.append([th["theme_no"], f"[{th['name']}]({path.name})", f"`{th['theme_code']}`", year_ranges(years)])
    index.append(_table(["№", "Тема", "Код", "Годы"], rows))
    index.append("\nКлючевые показатели карты портала: [map-indicators.md](map-indicators.md).\n")
    (out / "map-indicators.md").write_text(map_page(con), encoding="utf-8")
    (out / "README.md").write_text("".join(index), encoding="utf-8")
    con.close()
    return written + [out / "map-indicators.md", out / "README.md"]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    for p in build_catalog():
        print(p.relative_to(config.ROOT))


if __name__ == "__main__":
    main()
