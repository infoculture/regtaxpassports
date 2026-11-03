-- База данных налоговых паспортов субъектов РФ (DuckDB).
-- Описание таблиц и полей: docs/database.md.
--
-- Ключ паспорта: (year, territory_code).
--   year            — отчётный год паспорта (параметр year портала); данные на 01.01 следующего года
--   territory_code  — код территории портала: 0 — РФ, 101..109 — федеральные округа, 1..95 — субъекты

-- Справочники ----------------------------------------------------------------

CREATE TABLE theme (
    theme_code        VARCHAR PRIMARY KEY,  -- канонический код темы раздела
    theme_no          INTEGER NOT NULL,     -- номер раздела в действующем шаблоне (= mapId портала; 12 — прогноз; 0 — руководители)
    name              VARCHAR NOT NULL,     -- краткое название темы
    portal_name       VARCHAR,              -- наименование раздела в справочнике Maps портала
    portal_start_year INTEGER,
    portal_end_year   INTEGER,
    portal_hint       VARCHAR,              -- пояснение портала (например, о приостановке публикации)
    default_unit      VARCHAR               -- единица по умолчанию, если в паспорте она не указана
);

CREATE TABLE territory (
    territory_code        INTEGER PRIMARY KEY,
    kind                  VARCHAR NOT NULL,  -- country | federal_district | region
    name                  VARCHAR NOT NULL,  -- наименование на портале (совпадает с меткой в имени файла)
    federal_district_code INTEGER,           -- для субъектов: код федерального округа
    portal_id             INTEGER NOT NULL,  -- id в справочнике Regions / FederalRegions
    portal_year           INTEGER,           -- поле year справочника: с какого года территория есть на портале
    passport_from_year    INTEGER            -- поле yearForPassport: с какого года публикуются паспорта
);

-- Источник -------------------------------------------------------------------

CREATE TABLE export (
    year        INTEGER PRIMARY KEY,
    as_of_date  DATE,              -- дата среза («ПО СОСТОЯНИЮ НА») по листам ТИТУЛ
    files       INTEGER NOT NULL,  -- число XLS в архиве
    zip_bytes   BIGINT NOT NULL,
    zip_sha256  VARCHAR NOT NULL,
    fetched_at  TIMESTAMP,
    source_url  VARCHAR NOT NULL
);

CREATE TABLE passport (
    year                   INTEGER NOT NULL,
    territory_code         INTEGER NOT NULL,
    file_name              VARCHAR NOT NULL,  -- имя XLS внутри архива
    file_bytes             INTEGER NOT NULL,
    file_sha256            VARCHAR NOT NULL,
    as_of_date             DATE,
    doc_title              VARCHAR,           -- заголовок титульного листа
    admin_center           VARCHAR,           -- административный центр (из ТИТУЛ)
    federal_district_label VARCHAR,           -- федеральный округ (из ТИТУЛ, для субъектов)
    title_code             VARCHAR,           -- «КОД УПРАВЛЕНИЯ ФНС РОССИИ» / «КОД ФЕДЕРАЛЬНОГО ОКРУГА»
    PRIMARY KEY (year, territory_code)
);

-- Содержание паспортов -------------------------------------------------------

CREATE TABLE sheet (
    year           INTEGER NOT NULL,
    territory_code INTEGER NOT NULL,
    sheet_name     VARCHAR NOT NULL,   -- имя листа: «Раздел1» ... «Раздел12»
    sheet_no       INTEGER,            -- номер из имени листа (в разные годы означает разное!)
    theme_code     VARCHAR,            -- что в разделе на самом деле (см. theme)
    title          VARCHAR,            -- заголовок раздела в листе
    toc_title      VARCHAR,            -- заголовок раздела в листе «Содержание»
    preamble       VARCHAR[],          -- надписи между заголовком и таблицей («ПЕРИОД: ...», «Справочно: ...»)
    section_unit   VARCHAR,            -- единица, указанная надписью над таблицей
    header_row_no  INTEGER,            -- строка Excel (с 1), где начинается шапка
    PRIMARY KEY (year, territory_code, sheet_name)
);

CREATE TABLE sheet_column (
    year           INTEGER NOT NULL,
    territory_code INTEGER NOT NULL,
    sheet_name     VARCHAR NOT NULL,
    col_no         INTEGER NOT NULL,   -- номер графы (= номер столбца Excel с 1; графа 1 — наименование, 2 — код строки)
    header         VARCHAR,            -- уровни шапки через « → »
    header_levels  VARCHAR[],
    unit           VARCHAR,            -- единица из шапки графы
    PRIMARY KEY (year, territory_code, sheet_name, col_no)
);

CREATE TABLE sheet_row (
    year           INTEGER NOT NULL,
    territory_code INTEGER NOT NULL,
    sheet_name     VARCHAR NOT NULL,
    row_no         INTEGER NOT NULL,   -- номер строки Excel (с 1)
    row_code       VARCHAR,            -- «Код строки» (графа 2); NULL у заголовков групп и «в том числе:»
    kind           VARCHAR NOT NULL,   -- indicator | heading | qualifier
    label          VARCHAR NOT NULL,   -- наименование показателя как в файле
    label_key      VARCHAR,            -- нормализованное наименование для сопоставления между годами
    level          INTEGER NOT NULL,   -- уровень вложенности (отступ ячейки)
    parent_code    VARCHAR,            -- код строки родительского показателя («в том числе», «из них»)
    heading        VARCHAR,            -- заголовок группы строк (например, «НАЛОГ НА ПРИБЫЛЬ ОРГАНИЗАЦИЙ»)
    unit           VARCHAR,            -- единица из наименования
    is_hidden      BOOLEAN NOT NULL,   -- строка скрыта в Excel
    PRIMARY KEY (year, territory_code, sheet_name, row_no)
);

CREATE TABLE value (
    year           INTEGER NOT NULL,
    territory_code INTEGER NOT NULL,
    sheet_name     VARCHAR NOT NULL,
    row_no         INTEGER NOT NULL,
    col_no         INTEGER NOT NULL,
    row_code       VARCHAR,            -- копия sheet_row.row_code для удобства
    value          DOUBLE,             -- числовое значение; NULL, если в ячейке текст
    value_text     VARCHAR,            -- текст ячейки, если это не число («X», «-»)
    unit           VARCHAR,            -- итоговая единица значения
    unit_source    VARCHAR,            -- откуда взята единица: column | row | section | theme_default
    PRIMARY KEY (year, territory_code, sheet_name, row_no, col_no)
);

-- Показатели карты портала (JSON API) ----------------------------------------

CREATE TABLE map_indicator (
    year            INTEGER NOT NULL,
    indicator_id    INTEGER NOT NULL,
    theme_no        INTEGER NOT NULL,  -- mapId раздела
    name            VARCHAR NOT NULL,
    scale           VARCHAR,           -- множитель: млн, млрд, тыс
    unit_value      VARCHAR,           -- единица значения: ₽, %, чел., ед.
    unit_difference VARCHAR,           -- единица изменения к прошлому году
    start_year      INTEGER,
    end_year        INTEGER,
    PRIMARY KEY (year, indicator_id)
);

CREATE TABLE map_indicator_value (
    year           INTEGER NOT NULL,
    indicator_id   INTEGER NOT NULL,
    territory_code INTEGER NOT NULL,
    value          DOUBLE,
    difference     DOUBLE,             -- изменение к прошлому году (обычно в %)
    heat_map_type  INTEGER,            -- класс заливки на карте портала
    PRIMARY KEY (year, indicator_id, territory_code)
);

-- Пересмотры --------------------------------------------------------------------
-- Ранее скачанные архивы хранятся в data/raw/snapshots/<дата>/<год>.zip. При сборке
-- файлы снимка, чей sha256 отличается от текущего, сравниваются с текущими по ячейкам.

CREATE TABLE passport_revision (
    snapshot        VARCHAR NOT NULL,  -- имя каталога снимка (дата загрузки)
    year            INTEGER NOT NULL,
    territory_code  INTEGER NOT NULL,
    old_file_sha256 VARCHAR NOT NULL,
    new_file_sha256 VARCHAR,           -- NULL, если файла больше нет в текущем архиве
    changed_cells   INTEGER,
    PRIMARY KEY (snapshot, year, territory_code)
);

CREATE TABLE value_revision (
    snapshot       VARCHAR NOT NULL,
    year           INTEGER NOT NULL,
    territory_code INTEGER NOT NULL,
    sheet_name     VARCHAR NOT NULL,
    row_no         INTEGER NOT NULL,
    col_no         INTEGER NOT NULL,
    row_code       VARCHAR,
    old_value      DOUBLE,
    old_value_text VARCHAR,
    new_value      DOUBLE,             -- NULL и new_value_text NULL — ячейка исчезла
    new_value_text VARCHAR,
    PRIMARY KEY (snapshot, year, territory_code, sheet_name, row_no, col_no)
);
