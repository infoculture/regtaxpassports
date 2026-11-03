-- Представления для анализа. Описание: docs/database.md.

-- Все значения с наименованиями: одна строка — одна ячейка паспорта.
CREATE VIEW v_value AS
SELECT
    v.year,
    v.territory_code,
    t.name            AS territory,
    t.kind            AS territory_kind,
    v.sheet_name,
    s.theme_code,
    th.name           AS theme,
    v.row_code,
    r.label,
    r.label_key,
    r.parent_code,
    r.level,
    r.heading,
    r.is_hidden,
    v.col_no,
    c.header          AS column_header,
    v.value,
    v.value_text,
    v.unit,
    v.unit_source,
    v.row_no
FROM value v
JOIN territory t    USING (territory_code)
JOIN sheet s        USING (year, territory_code, sheet_name)
LEFT JOIN theme th  USING (theme_code)
JOIN sheet_row r    USING (year, territory_code, sheet_name, row_no)
JOIN sheet_column c USING (year, territory_code, sheet_name, col_no);

-- Шаблон года: какие показатели (коды строк) есть в каждой теме и как они названы
-- у большинства территорий.
CREATE VIEW v_indicator AS
SELECT
    r.year,
    s.theme_code,
    r.sheet_name,
    r.row_code,
    mode(r.label)       AS label,
    mode(r.label_key)   AS label_key,
    mode(r.unit)        AS unit,
    mode(r.parent_code) AS parent_code,
    min(r.level)        AS level,
    bool_or(r.is_hidden) AS is_hidden,
    count(*)            AS passports,
    count(DISTINCT r.label) AS label_variants
FROM sheet_row r
JOIN sheet s USING (year, territory_code, sheet_name)
WHERE r.kind = 'indicator'
GROUP BY ALL;

-- Шаблон года: графы разделов.
CREATE VIEW v_column AS
SELECT
    c.year,
    s.theme_code,
    c.sheet_name,
    c.col_no,
    mode(c.header) AS header,
    mode(c.unit)   AS unit,
    count(*)       AS passports,
    count(DISTINCT c.header) AS header_variants
FROM sheet_column c
JOIN sheet s USING (year, territory_code, sheet_name)
GROUP BY ALL;
