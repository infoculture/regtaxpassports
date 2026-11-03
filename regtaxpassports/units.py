"""Единицы измерения: из наименования показателя, шапки графы или преамбулы раздела.

В паспортах единица указана текстом в разных местах и в разном написании
(«млн. руб.», «млн.руб.», «млн. рублей»). Здесь всё сводится к короткому
каноническому словарю.
"""

from __future__ import annotations

import re

# (шаблон, каноническая единица); порядок важен
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\$\s*/\s*баррел", re.I), "долл. США/барр."),
    (re.compile(r"копеек", re.I), "коп. на 1 руб."),
    (re.compile(r"млрд\.?\s*долл", re.I), "млрд. долл. США"),
    (re.compile(r"млн\.?\s*долл", re.I), "млн. долл. США"),
    (re.compile(r"млрд\.?\s*руб", re.I), "млрд. руб."),
    (re.compile(r"млн\.?\s*руб", re.I), "млн. руб."),
    (re.compile(r"тыс\.?\s*руб", re.I), "тыс. руб."),
    (re.compile(r"млн\.?\s*(м\.?\s*куб|куб\.?\s*м)", re.I), "млн. куб. м"),
    (re.compile(r"тыс\.?\s*тонн", re.I), "тыс. т"),
    (re.compile(r"тыс\.?\s*кв\.?\s*км", re.I), "тыс. кв. км"),
    (re.compile(r"тыс\.?\s*чел", re.I), "тыс. чел."),
    (re.compile(r"(^|\W)(человек|чел\.)", re.I), "чел."),
    (re.compile(r"литр", re.I), "л"),
    (re.compile(r"ед\.?\s*из\s*1000", re.I), "ед. на 1000 налогоплательщиков"),
    (re.compile(r"(^|\W)(единиц|ед\.)", re.I), "ед."),
    (re.compile(r"%"), "%"),
    (re.compile(r"(^|\W)(рубл|руб\.)", re.I), "руб."),
]

_PAREN = re.compile(r"\(([^()]*)\)")
_TAIL = re.compile(r",\s*([^,]{1,25})$")  # «Отношение к прибыли ..., %», «Оборот, млрд.руб.»


def normalize(text: str | None) -> str | None:
    if not text:
        return None
    for pattern, unit in _PATTERNS:
        if pattern.search(text):
            return unit
    return None


def from_label(label: str) -> str | None:
    """Единица из наименования строки: последняя скобка с единицей или хвост «, ед.»."""
    for group in reversed(_PAREN.findall(label)):
        if "состоянию" in group.lower():
            continue
        unit = normalize(group)
        if unit:
            return unit
    m = _TAIL.search(label.rstrip(":. "))
    return normalize(m.group(1)) if m else None


def from_header(levels: list[str]) -> str | None:
    """Единица графы. Процентные графы («В % к ...», «Удельный вес (%)») имеют
    приоритет над денежной единицей из верхнего уровня шапки."""
    units = [normalize(level) for level in levels]
    if "%" in units:
        return "%"
    return next((u for u in reversed(units) if u), None)


def from_preamble(lines: list[str]) -> str | None:
    """Единица раздела, если она вынесена над таблицей отдельной надписью («млн.руб.»)."""
    for line in lines:
        # «Справочно: - ВРП за 2013 год 402.56 млрд. руб.» — это значение, не единица раздела
        if len(line) <= 60 and not re.search(r"\d", line):
            for token in re.findall(r"(?:млрд|млн|тыс)\.?\s*руб[а-я]*\.?", line, re.I):
                return normalize(token)
    return None
