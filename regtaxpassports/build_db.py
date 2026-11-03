"""Сборка базы DuckDB и Parquet-выгрузок из скачанных архивов.

    python -m regtaxpassports.build_db

Читает data/raw/exports/*.zip и data/raw/api/*.json, пишет
data/db/regtaxpassports.duckdb (пересоздаётся целиком) и data/parquet/<таблица>.parquet.
Лист «Руководители» (ФИО, телефоны и адреса должностных лиц) не загружается.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from . import config, units
from .parse import THEMES, label_key, parse_workbook

log = logging.getLogger(__name__)

# Единица по умолчанию для тем, где в шаблоне 2014–2018 годов она указана
# надписью «млн.руб.» над таблицей, а в последующих годах пропущена. Значения
# в этих разделах согласуются с показателями карты портала (scale «млн», ₽).
THEME_DEFAULT_UNIT = {
    "tax_benefits": "млн. руб.",
    "revenue_by_budget": "млн. руб.",
    "revenue_by_activity": "млн. руб.",
    "insurance_contributions": "млн. руб.",
    "debt": "млн. руб.",
    "debt_by_activity": "млн. руб.",
}

TABLES = [
    "theme", "territory", "export", "passport", "sheet", "sheet_column",
    "sheet_row", "value", "map_indicator", "map_indicator_value",
    "passport_revision", "value_revision",
]
DERIVED = ["map_indicator_source"]  # sql/derived.sql


def _date(s: str | None):
    return datetime.strptime(s, "%d.%m.%Y").date() if s else None


def load_territories() -> tuple[list[dict], dict[str, int], dict[tuple[str, int], int]]:
    regions = json.loads((config.API / "Regions.json").read_text(encoding="utf-8"))
    feds = json.loads((config.API / "FederalRegions.json").read_text(encoding="utf-8"))
    fed_by_id = {f["id"]: f for f in feds}
    rows, by_name, by_portal = [], {}, {}
    for f in feds:
        kind = "country" if f["code"] == 0 else "federal_district"
        rows.append(dict(territory_code=f["code"], kind=kind, name=f["label"], federal_district_code=None,
                         portal_id=f["id"], portal_year=f["year"], passport_from_year=f["yearForPassport"]))
        by_portal[("federal", f["id"])] = f["code"]
    for r in regions:
        if r["code"] is None:  # «Все регионы»
            continue
        parent = fed_by_id.get(r["parentId"])
        rows.append(dict(territory_code=r["code"], kind="region", name=r["label"],
                         federal_district_code=parent["code"] if parent else None,
                         portal_id=r["id"], portal_year=r["year"], passport_from_year=r["yearForPassport"]))
        by_portal[("region", r["id"])] = r["code"]
    for row in rows:
        by_name[row["name"]] = row["territory_code"]
    return rows, by_name, by_portal


def load_themes() -> list[dict]:
    maps = {m["id"]: m for m in json.loads((config.API / "Maps.json").read_text(encoding="utf-8"))}
    out = []
    for code, no, _, name in THEMES:
        m = maps.get(no, {})
        out.append(dict(theme_code=code, theme_no=no, name=name, portal_name=m.get("name"),
                        portal_start_year=m.get("startYear"), portal_end_year=m.get("endYear"),
                        portal_hint=m.get("hint"), default_unit=THEME_DEFAULT_UNIT.get(code)))
    return out


def _effective_unit(col_unit, row_unit, section_unit, theme):
    if col_unit == "%":
        return "%", "column"
    if row_unit:
        return row_unit, "row"
    if col_unit:
        return col_unit, "column"
    if section_unit:
        return section_unit, "section"
    if theme in THEME_DEFAULT_UNIT:
        return THEME_DEFAULT_UNIT[theme], "theme_default"
    return None, None


def _parse_member(args: tuple[str, str, bytes]) -> dict:
    """Разбор одного XLS в плоские записи (выполняется в отдельном процессе)."""
    zip_name, file_name, content = args
    p = parse_workbook(content, file_name)
    out = {"file_name": file_name, "file_label": p.file_label, "year": p.year, "title": p.title,
           "file_bytes": len(content), "file_sha256": hashlib.sha256(content).hexdigest(),
           "sheets": [], "columns": [], "rows": [], "values": [], "warnings": []}
    for s in p.sheets:
        section_unit = units.from_preamble(s.preamble)
        out["sheets"].append(dict(
            sheet_name=s.name, sheet_no=int(s.name.removeprefix("Раздел")) if s.name.startswith("Раздел") else None,
            theme_code=s.theme, title=s.title, toc_title=dict(p.contents).get(s.name), preamble=s.preamble,
            section_unit=section_unit, header_row_no=s.header_row + 1 if s.header_row is not None else None))
        col_unit = {}
        for c in s.columns:
            col_no = c.col + 1
            if c.graph_no is not None and c.graph_no != str(col_no):
                out["warnings"].append(f"{file_name} {s.name}: графа {c.graph_no} в столбце {col_no}")
            col_unit[c.col] = units.from_header(c.levels)
            out["columns"].append(dict(sheet_name=s.name, col_no=col_no, header=c.header,
                                       header_levels=c.levels, unit=col_unit[c.col]))
        row_unit = {}
        for r in s.rows:
            row_unit[r.row] = units.from_label(r.label) if r.kind == "indicator" else None
            out["rows"].append(dict(sheet_name=s.name, row_no=r.row + 1, row_code=r.code, kind=r.kind,
                                    label=r.label, label_key=label_key(r.label), level=r.level,
                                    parent_code=r.parent_code, heading=r.heading, unit=row_unit[r.row],
                                    is_hidden=r.is_hidden))
        codes = {r.row: r.code for r in s.rows}
        for cell in s.cells:
            unit, source = _effective_unit(col_unit.get(cell.col), row_unit.get(cell.row), section_unit, s.theme)
            out["values"].append(dict(sheet_name=s.name, row_no=cell.row + 1, col_no=cell.col + 1,
                                      row_code=codes.get(cell.row), value=cell.value, value_text=cell.raw,
                                      unit=unit, unit_source=source))
        out["warnings"].extend(f"{file_name} {s.name}: {w}" for w in s.warnings)
    return out


def _iter_members(zips: list[Path]):
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename):
                if info.filename.lower().endswith(".xls"):
                    yield zp.name, info.filename, z.read(info)


def load_map_indicators(by_portal: dict) -> tuple[list[dict], list[dict]]:
    inds, vals = [], []
    for f in sorted((config.API / "indicators").glob("*/map_*.json")):
        year = int(f.parent.name)
        theme_no = int(f.stem.removeprefix("map_"))
        for i in json.loads(f.read_text(encoding="utf-8")).get("indicators") or []:
            inds.append(dict(year=year, indicator_id=i["id"], theme_no=theme_no, name=i["name"], scale=i["scale"],
                             unit_value=i["unitValue"], unit_difference=i["unitDifference"] or None,
                             start_year=i["startYear"], end_year=i["endYear"]))
    for f in sorted((config.API / "indicator_data").glob("*/*.json")):
        year, ind_id = int(f.parent.name), int(f.stem)
        for it in json.loads(f.read_text(encoding="utf-8")).get("items") or []:
            key = ("region", it["regionId"]) if it["regionId"] is not None else ("federal", it["federalRegionId"])
            vals.append(dict(year=year, indicator_id=ind_id, territory_code=by_portal[key], value=it["value"],
                             difference=it["difference"], heat_map_type=it["heatMapType"]))
    return inds, vals


def compare_snapshots(tables: dict[str, list[dict]], by_name: dict[str, int]) -> None:
    """Сравнить файлы из data/raw/snapshots/<дата>/ с текущими; заполнить *_revision."""
    current = {(p["year"], p["file_name"]): (p["territory_code"], p["file_sha256"]) for p in tables["passport"]}
    unchanged = {sha for _, sha in current.values()}
    values: dict[tuple[int, int], dict] | None = None
    for snap in sorted(d for d in config.SNAPSHOTS.glob("*") if d.is_dir()):
        members = (m for m in _iter_members(sorted(snap.glob("*.zip")))
                   if hashlib.sha256(m[2]).hexdigest() not in unchanged)
        for res in map(_parse_member, members):
            key = (res["year"], by_name[res["file_label"]])
            new_sha = current.get((res["year"], res["file_name"]), (None, None))[1]
            if values is None:  # индекс текущих значений строится только при наличии отличий
                values = {}
                for v in tables["value"]:
                    values.setdefault((v["year"], v["territory_code"]), {})[(v["sheet_name"], v["row_no"], v["col_no"])] = v
            new = values.get(key, {}) if new_sha else {}
            old = {(v["sheet_name"], v["row_no"], v["col_no"]): v for v in res["values"]}
            changed = 0
            for cell in sorted(old.keys() | new.keys()):
                o, n = old.get(cell, {}), new.get(cell, {})
                if (o.get("value"), o.get("value_text")) == (n.get("value"), n.get("value_text")):
                    continue
                changed += 1
                tables["value_revision"].append(dict(
                    snapshot=snap.name, year=key[0], territory_code=key[1], sheet_name=cell[0], row_no=cell[1],
                    col_no=cell[2], row_code=(n or o).get("row_code"), old_value=o.get("value"),
                    old_value_text=o.get("value_text"), new_value=n.get("value"), new_value_text=n.get("value_text")))
            tables["passport_revision"].append(dict(snapshot=snap.name, year=key[0], territory_code=key[1],
                                                    old_file_sha256=res["file_sha256"], new_file_sha256=new_sha,
                                                    changed_cells=changed))
            log.info("снимок %s: %s изменён, ячеек: %d", snap.name, res["file_name"], changed)


def _insert(con, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = [r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()]
    arrow = pa.Table.from_pylist([{c: r.get(c) for c in cols} for r in rows])
    con.register("_src", arrow)
    con.execute(f"INSERT INTO {table} SELECT {', '.join(cols)} FROM _src")
    con.unregister("_src")


def build(workers: int | None = None, export_parquet: bool = True) -> dict:
    manifest = json.loads((config.RAW / "manifest.json").read_text(encoding="utf-8"))
    zips = sorted(config.EXPORTS.glob("*.zip"))
    territories, by_name, by_portal = load_territories()

    tables: dict[str, list[dict]] = {t: [] for t in TABLES}
    tables["theme"] = load_themes()
    tables["territory"] = territories
    warnings: list[str] = []
    as_of = {}

    with ProcessPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(_parse_member, _iter_members(zips), chunksize=4):
            year, code = res["year"], by_name.get(res["file_label"])
            if code is None:
                raise RuntimeError(f"не найдена территория для файла {res['file_name']}")
            key = dict(year=year, territory_code=code)
            t = res["title"]
            tables["passport"].append(dict(**key, file_name=res["file_name"], file_bytes=res["file_bytes"],
                                           file_sha256=res["file_sha256"], as_of_date=_date(t.get("as_of")),
                                           doc_title=t.get("doc_title"), admin_center=t.get("admin_center"),
                                           federal_district_label=t.get("federal_district"), title_code=t.get("code")))
            as_of.setdefault(year, Counter())[t.get("as_of")] += 1
            for name in ("sheets", "columns", "rows", "values"):
                table = {"sheets": "sheet", "columns": "sheet_column", "rows": "sheet_row", "values": "value"}[name]
                tables[table].extend({**key, **r} for r in res[name])
            warnings.extend(res["warnings"])
    log.info("разобрано паспортов: %d, значений: %d", len(tables["passport"]), len(tables["value"]))

    for e in manifest["exports"]:
        y = e["year"]
        tables["export"].append(dict(year=y, as_of_date=_date(as_of[y].most_common(1)[0][0]) if y in as_of else None,
                                     files=e["files_in_zip"], zip_bytes=e["bytes"], zip_sha256=e["sha256"],
                                     fetched_at=datetime.fromisoformat(e["fetched_at"]),
                                     source_url=f"{config.BASE_URL}/ExportAll?year={y}"))
    tables["map_indicator"], tables["map_indicator_value"] = load_map_indicators(by_portal)
    compare_snapshots(tables, by_name)

    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.DB_PATH.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute((config.ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
    for t in TABLES:
        _insert(con, t, tables[t])
    con.execute((config.ROOT / "sql" / "derived.sql").read_text(encoding="utf-8"))
    con.execute((config.ROOT / "sql" / "views.sql").read_text(encoding="utf-8"))
    report = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "tables": {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES + DERIVED},
        "years": [r[0] for r in con.execute("SELECT year FROM export ORDER BY 1").fetchall()],
        "warnings": warnings,
    }
    if export_parquet:
        config.PARQUET.mkdir(parents=True, exist_ok=True)
        for t in TABLES + DERIVED:
            con.execute(f"COPY {t} TO '{config.PARQUET / (t + '.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    con.close()
    tmp.replace(config.DB_PATH)
    (config.DB_DIR / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=None, help="число процессов для разбора XLS")
    ap.add_argument("--no-parquet", action="store_true", help="не выгружать Parquet")
    args = ap.parse_args(argv)
    report = build(args.workers, export_parquet=not args.no_parquet)
    for t, n in report["tables"].items():
        log.info("%-20s %10d", t, n)
    if report["warnings"]:
        log.warning("предупреждений: %d (см. data/db/build_report.json)", len(report["warnings"]))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
