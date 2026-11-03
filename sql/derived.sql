-- Производные таблицы: строятся из загруженных данных после schema.sql.

-- Сопоставление показателей карты портала с ячейками паспортов.
-- Для каждого показателя (год, indicator_id) ищется графа и код строки в разделе
-- той же темы (theme_no = mapId), значения которых совпадают со значениями API
-- у наибольшего числа территорий. Совпадением считается равенство с точностью
-- 1e-6 (относительно), в том числе с множителем 1000 (млн ↔ млрд).
-- Показатели, которые портал считает суммой нескольких строк, получают
-- match_share < 1 и не годятся для прямого сопоставления.
CREATE TABLE map_indicator_source AS
WITH m AS (
    SELECT mv.year, mv.indicator_id, mi.theme_no, mv.territory_code, mv.value
    FROM map_indicator_value mv
    JOIN map_indicator mi USING (year, indicator_id)
    WHERE mv.value IS NOT NULL
), c AS (
    SELECT v.year, th.theme_no, th.theme_code, v.sheet_name, v.row_code, v.col_no, v.territory_code, v.value
    FROM value v
    JOIN sheet s USING (year, territory_code, sheet_name)
    JOIN theme th USING (theme_code)
    WHERE v.value IS NOT NULL AND v.row_code IS NOT NULL
), j AS (
    SELECT m.year, m.indicator_id, c.theme_code, c.sheet_name, c.row_code, c.col_no,
           count(*) AS territories,
           count(*) FILTER (WHERE abs(m.value - c.value) <= 1e-6 * greatest(abs(m.value), 1)) AS same,
           count(*) FILTER (WHERE abs(m.value - c.value / 1000) <= 1e-6 * greatest(abs(m.value), 1)) AS div1000,
           count(*) FILTER (WHERE abs(m.value - c.value * 1000) <= 1e-6 * greatest(abs(m.value), 1)) AS mul1000
    FROM m
    JOIN c ON m.year = c.year AND m.theme_no = c.theme_no AND m.territory_code = c.territory_code
    GROUP BY ALL
), best AS (
    SELECT *,
           greatest(same, div1000, mul1000) AS hits,
           row_number() OVER (PARTITION BY year, indicator_id
                              ORDER BY greatest(same, div1000, mul1000) DESC, col_no, row_code) AS rn
    FROM j
)
SELECT year, indicator_id, theme_code, sheet_name, row_code, col_no,
       CASE WHEN same >= greatest(div1000, mul1000) THEN 1
            WHEN div1000 >= mul1000 THEN 0.001 ELSE 1000 END AS factor,  -- value_api = value * factor
       hits AS matched_territories,
       territories,
       round(hits / territories, 4) AS match_share
FROM best
WHERE rn = 1;
