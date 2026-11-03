# База данных

База собирается командой `make build` (`python -m regtaxpassports.build_db`) из архивов `data/raw/exports/*.zip` и JSON-ответов `data/raw/api/`. Результат:

- `data/db/regtaxpassports.duckdb` — база DuckDB (около 100 МБ, в git не хранится, пересобирается примерно за 10 секунд);
- `data/parquet/<таблица>.parquet` — те же таблицы в Parquet (zstd, около 9 МБ). Их можно читать без DuckDB: pandas, Polars, R arrow, Spark.

DDL: [sql/schema.sql](../sql/schema.sql) (таблицы), [sql/derived.sql](../sql/derived.sql) (производные таблицы), [sql/views.sql](../sql/views.sql) (представления). Формат исходных файлов описан в [source-format.md](source-format.md).

## Модель

Исходный паспорт — это книга Excel с листами-разделами. Каждый лист — таблица «показатель × графа». База хранит её в нормализованном виде, без потери исходной структуры:

```
territory ─┐
           ├─ passport (year, territory_code) ── sheet (… , sheet_name) ─┬─ sheet_column (… , col_no)
export ────┘                                         │                    ├─ sheet_row    (… , row_no)
                                                     theme                └─ value        (… , row_no, col_no)

map_indicator (year, indicator_id) ── map_indicator_value (… , territory_code)
map_indicator_source: map_indicator → ячейка паспорта (sheet_name, row_code, col_no)
passport_revision / value_revision: изменения относительно прежних загрузок
```

Принятые решения:

- **Ключ паспорта** — `(year, territory_code)`. `territory_code` — код территории портала: `0` — РФ, `101`…`109` — федеральные округа, `1`…`95` — субъекты (код региона: 70 — Томская область, 77 — г. Москва, 91 — Республика Крым).
- **Строки и графы хранятся по каждому паспорту**, а не по шаблону года: наименования и шапки у паспортов РФ, округов и субъектов различаются (см. [source-format.md](source-format.md#различия-между-паспортами-одного-года)). Сводный шаблон года дают представления `v_indicator` и `v_column`.
- **Ячейка адресуется** номером строки и графы Excel (`row_no`, `col_no`, оба с 1), поэтому любое значение можно найти в исходном файле. Код строки (`row_code`) продублирован в `value` для удобства.
- **Тема раздела** (`theme_code`) отделена от имени листа: в разные годы один и тот же лист означает разное.
- **Текст и число разделены**: `value` — число или NULL, `value_text` — исходный текст (`X`, `-`). Пустые ячейки не хранятся.
- **Лист «Руководители» не загружается**: в нём ФИО и контакты должностных лиц.

## Таблицы

### `theme` — темы разделов

| Поле | Тип | Описание |
|---|---|---|
| `theme_code` | VARCHAR, PK | код темы: `socio_economic`, `tax_base`, `tax_benefits`, `tax_control`, `revenue_by_budget`, `insurance_contributions`, `revenue_by_activity`, `debt`, `debt_by_activity`, `tax_burden`, `tax_burden_by_activity`, `forecast`, `officials` |
| `theme_no` | INTEGER | номер раздела в действующем шаблоне (= `mapId` портала); 12 — прогноз, 0 — руководители |
| `name` | VARCHAR | краткое название |
| `portal_name`, `portal_start_year`, `portal_end_year`, `portal_hint` | | данные справочника `Maps` портала |
| `default_unit` | VARCHAR | единица по умолчанию (правило `theme_default`) |

### `territory` — территории

| Поле | Тип | Описание |
|---|---|---|
| `territory_code` | INTEGER, PK | 0 — РФ, 101–109 — ФО, 1–95 — субъекты |
| `kind` | VARCHAR | `country`, `federal_district`, `region` |
| `name` | VARCHAR | наименование на портале; совпадает с меткой в имени файла |
| `federal_district_code` | INTEGER | код ФО для субъекта |
| `portal_id` | INTEGER | `id` в справочнике `Regions` (субъекты) или `FederalRegions` (РФ и ФО) |
| `portal_year` | INTEGER | поле `year` справочника |
| `passport_from_year` | INTEGER | поле `yearForPassport`: с какого года публикуются паспорта (2027 — у новых субъектов) |

99 записей: РФ, 9 округов (включая «ЮГО-ЗАПАДНЫЕ СУБЪЕКТЫ РФ», по которому паспортов нет), 89 субъектов (у 4 новых паспортов нет).

### `export` — загруженные архивы

`year` (PK), `as_of_date`, `files`, `zip_bytes`, `zip_sha256`, `fetched_at`, `source_url`.

### `passport` — паспорта (файлы XLS)

| Поле | Описание |
|---|---|
| `year`, `territory_code` | ключ |
| `file_name`, `file_bytes`, `file_sha256` | файл в архиве |
| `as_of_date` | «ПО СОСТОЯНИЮ НА» с титульного листа |
| `doc_title` | заголовок документа |
| `admin_center`, `federal_district_label` | административный центр и округ с титульного листа |
| `title_code` | «КОД УПРАВЛЕНИЯ ФНС РОССИИ» у субъекта (например, `7000`), «КОД ФЕДЕРАЛЬНОГО ОКРУГА» у округа (1–8) |

### `sheet` — разделы паспорта

| Поле | Описание |
|---|---|
| `year`, `territory_code`, `sheet_name` | ключ; `sheet_name` — «Раздел1» … «Раздел12» |
| `sheet_no` | номер из имени листа |
| `theme_code` | тема раздела |
| `title`, `toc_title` | заголовок в листе и в листе «Содержание» |
| `preamble` | VARCHAR[]: надписи над таблицей |
| `section_unit` | единица, указанная надписью над таблицей |
| `header_row_no` | строка Excel, с которой начинается шапка |

### `sheet_column` — графы

`year`, `territory_code`, `sheet_name`, `col_no` (PK); `header` — уровни шапки через « → »; `header_levels` — те же уровни массивом; `unit` — единица из шапки.

### `sheet_row` — строки

| Поле | Описание |
|---|---|
| `year`, `territory_code`, `sheet_name`, `row_no` | ключ (`row_no` — строка Excel с 1) |
| `row_code` | «Код строки»; NULL у заголовков и связок |
| `kind` | `indicator` — показатель; `heading` — заголовок группы; `qualifier` — «в том числе:», «из них:» |
| `label` | наименование как в файле |
| `label_key` | нормализованное наименование: нижний регистр, без дат «по состоянию на», годов, единиц и пунктуации. Нужно для сопоставления между годами, когда меняется код строки |
| `level` | уровень вложенности (отступ ячейки, 0–6) |
| `parent_code` | код строки родительского показателя |
| `heading` | заголовок группы, под которым стоит показатель |
| `unit` | единица из наименования |
| `is_hidden` | строка скрыта в Excel (служебные строки с отрицательным кодом и некоторые обычные) |

### `value` — значения

| Поле | Описание |
|---|---|
| `year`, `territory_code`, `sheet_name`, `row_no`, `col_no` | ключ: ячейка Excel |
| `row_code` | код строки |
| `value` | DOUBLE: число; NULL, если в ячейке текст |
| `value_text` | исходный текст нечисловой ячейки: `X` — не заполняется, `-` — нет данных |
| `unit` | итоговая единица значения |
| `unit_source` | `column`, `row`, `section`, `theme_default` — откуда взята единица (порядок выбора: [source-format.md](source-format.md#единицы-измерения)) |

### `map_indicator`, `map_indicator_value` — показатели карты портала

Из методов `Indicators` и `IndicatorData`. `map_indicator`: `year`, `indicator_id` (PK), `theme_no` (`mapId`), `name`, `scale` (`млн`, `млрд`, `тыс`), `unit_value` (`₽`, `%`, `чел.`, `ед.`), `unit_difference`, `start_year`, `end_year`. `map_indicator_value`: `year`, `indicator_id`, `territory_code` (PK), `value`, `difference` (изменение к прошлому году), `heat_map_type` (класс заливки карты).

### `map_indicator_source` — сопоставление показателей карты с паспортами

Производная таблица ([sql/derived.sql](../sql/derived.sql)). Для каждого `(year, indicator_id)` указаны тема, лист, код строки и графа, значения которых совпадают со значениями API у наибольшего числа территорий. `factor` — множитель (`value_api = value * factor`). `match_share` — доля совпавших территорий; 1.0 — точное совпадение. Значение ниже 0.95 означает, что портал считает показатель иначе, например суммой строк.

### `passport_revision`, `value_revision` — пересмотры

Строятся при сборке, если в `data/raw/snapshots/<дата>/` лежат прежние загрузки. `passport_revision`: какой файл изменился (`old_file_sha256` → `new_file_sha256`) и сколько ячеек. `value_revision`: каждая изменённая ячейка со старым и новым значением.

## Представления

| Представление | Назначение |
|---|---|
| `v_value` | все значения с названием территории, темой, наименованием строки, шапкой графы и единицей. Основная точка входа для анализа |
| `v_indicator` | шаблон года: показатели темы (`row_code`) с наименованием, единицей и родителем у большинства паспортов; `label_variants` — сколько разных наименований встретилось |
| `v_column` | шаблон года: графы темы; `header_variants` — сколько разных шапок встретилось |

## Примеры запросов

DuckDB CLI: `duckdb data/db/regtaxpassports.duckdb`. Python: `duckdb.connect("data/db/regtaxpassports.duckdb", read_only=True)`. Без базы те же запросы работают по Parquet: `FROM 'data/parquet/value.parquet'`.

ВРП Томской области по годам паспортов (раздел 1, строка 101, графа 3):

```sql
SELECT year, value AS grp, unit
FROM v_value
WHERE theme_code = 'socio_economic' AND row_code = '101' AND col_no = 3
  AND territory_code = 70
ORDER BY year;
```

Пять субъектов с наибольшими поступлениями НДФЛ в консолидированный бюджет за 2024 год:

```sql
SELECT territory, value, unit
FROM v_value
WHERE year = 2024 AND theme_code = 'revenue_by_budget'
  AND row_code = '503' AND col_no = 3 AND territory_kind = 'region'
ORDER BY value DESC
LIMIT 5;
```

Поступления по видам деятельности, непосредственно входящим в итог (РФ, 2024 год):

```sql
SELECT r.row_code, r.label, v.value
FROM sheet_row r
JOIN value v USING (year, territory_code, sheet_name, row_no)
JOIN sheet s USING (year, territory_code, sheet_name)
WHERE s.theme_code = 'revenue_by_activity' AND r.year = 2024 AND r.territory_code = 0
  AND r.parent_code = '701' AND v.col_no = 3
ORDER BY v.value DESC;
```

Показатель, у которого код строки менялся между годами (численность населения: в 2014 году строка 145, затем 151). Сопоставление по `label_key`:

```sql
SELECT year, row_code, label, value
FROM v_value
WHERE theme_code = 'socio_economic' AND territory_code = 0 AND col_no = 3
  AND label_key = 'численность населения'
ORDER BY year;
```

Ключевые показатели карты по федеральным округам (численность населения, 2024 год):

```sql
SELECT t.name, v.value, v.difference
FROM map_indicator_value v
JOIN territory t USING (territory_code)
WHERE v.indicator_id = 9 AND v.year = 2024 AND t.kind = 'federal_district'
ORDER BY v.value DESC;
```

Шаблон раздела за год — какие показатели и графы в нём есть:

```sql
SELECT row_code, label, unit, parent_code, label_variants
FROM v_indicator WHERE year = 2024 AND theme_code = 'tax_burden' ORDER BY row_code;

SELECT col_no, header, unit FROM v_column WHERE year = 2024 AND theme_code = 'tax_burden';
```

Что изменилось при пересмотрах:

```sql
SELECT snapshot, year, territory_code, sheet_name, row_code, col_no, old_value, new_value
FROM value_revision;
```

## Как не ошибиться

- Сравнивая годы, сопоставляйте показатели по теме и `label_key` (или по `indicator_id` карты), а не по имени листа и коду строки.
- Не суммируйте субъекты с округами и РФ (`territory_kind`) и итоги с детализацией (`parent_code`, `level`).
- Проверяйте `unit` и `unit_source`. Внутри одной строки графы бывают в разных единицах: сумма в «млн. руб.», а рядом «В % к предыдущему году».
- Скрытые строки (`is_hidden`) — служебные. Исключайте их, если не нужны.
- `value IS NULL` при `value_text = 'X'` означает «не заполняется», а не ноль.
