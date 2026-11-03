"""Сбор налоговых паспортов и справочников с analytic.nalog.gov.ru.

Что скачивается:
  data/raw/api/{MaxYear,Regions,FederalRegions,Maps}.json  справочники
  data/raw/exports/<год>.zip                               все паспорта за год (ExportAll)
  data/raw/api/indicators/<год>/map_<id>.json              ключевые показатели раздела по РФ
  data/raw/api/indicator_data/<год>/<id>.json              значения показателя по всем территориям
  data/raw/manifest.json                                   размеры, SHA-256, время загрузки

Повторный запуск докачивает только отсутствующие файлы (--force перекачивает).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)


class Client:
    def __init__(self, interval: float = config.INTERVAL):
        self.interval = interval
        self._last = 0.0
        self.http = httpx.Client(
            proxy=config.PROXY,
            timeout=config.TIMEOUT,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json, */*"},
            follow_redirects=True,
        )

    def _wait(self) -> None:
        delay = self._last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        url = f"{config.BASE_URL}/{path}"
        for attempt in range(1, 4):
            self._wait()
            try:
                r = self.http.request(method, url, **kw)
            except httpx.TransportError as e:
                log.warning("%s %s: %s (попытка %d)", method, path, e, attempt)
                time.sleep(5 * attempt)
                continue
            if r.status_code in (403, 429):
                # Не обходим ограничения портала: останавливаемся.
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code}, сбор остановлен")
            if r.status_code >= 500:
                log.warning("%s %s: HTTP %d (попытка %d)", method, path, r.status_code, attempt)
                time.sleep(5 * attempt)
                continue
            return r
        raise RuntimeError(f"{method} {path}: не удалось получить ответ")

    def get_json(self, path: str, **kw):
        r = self.request("GET", path, **kw)
        r.raise_for_status()
        return r.json()["data"]

    def post_json(self, path: str, params: dict):
        # Фронтенд портала (axios) шлёт тело вида {"params": {...}}.
        r = self.request("POST", path, json={"params": params})
        r.raise_for_status()
        return r.json()["data"]


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_references(client: Client) -> int:
    for name in ("MaxYear", "Regions", "FederalRegions", "Maps"):
        data = client.get_json(name)
        _write_json(config.API / f"{name}.json", data)
        log.info("справочник %s сохранён", name)
    return int(json.loads((config.API / "MaxYear.json").read_text()))


def fetch_export(client: Client, year: int, force: bool = False) -> Path | None:
    out = config.EXPORTS / f"{year}.zip"
    if out.exists() and not force:
        return out
    r = client.request("GET", "ExportAll", params={"year": year})
    if r.status_code == 400:
        log.warning("ExportAll year=%d: HTTP 400 (%s)", year, r.text[:200])
        return None
    r.raise_for_status()
    body = r.content
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"{year}.zip: повреждён файл {bad}")
        n = len(z.namelist())
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(out)
    log.info("ExportAll %d: %d файлов, %d байт", year, n, len(body))
    return out


def fetch_map_indicators(client: Client, year: int, maps: list[dict], force: bool = False) -> None:
    """Показатели, которые портал выводит на карте (подмножество паспортов)."""
    ids: dict[int, int] = {}
    for m in maps:
        if m["startYear"] and year < m["startYear"] or m["endYear"] and year > m["endYear"]:
            continue
        out = config.API / "indicators" / str(year) / f"map_{m['id']}.json"
        if out.exists() and not force:
            data = json.loads(out.read_text(encoding="utf-8"))
        else:
            data = client.post_json("Indicators", {"year": year, "federalRegionId": 0, "mapId": m["id"]})
            _write_json(out, data)
        for ind in data.get("indicators") or []:
            ids[ind["id"]] = m["id"]
    for ind_id in sorted(ids):
        out = config.API / "indicator_data" / str(year) / f"{ind_id}.json"
        if out.exists() and not force:
            continue
        _write_json(out, client.get_json(f"IndicatorData/{year}/{ind_id}"))
    log.info("показатели карты %d: %d показателей", year, len(ids))


def write_manifest() -> None:
    files = []
    for p in sorted(config.EXPORTS.glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            infos = sorted(z.infolist(), key=lambda i: i.filename)
        # Портал собирает ZIP на лету с текущей датой файлов, поэтому sha256 архива
        # меняется при каждой загрузке. content_sha256 зависит только от имён и CRC файлов.
        content = hashlib.sha256("\n".join(f"{i.filename}\t{i.CRC:08x}\t{i.file_size}" for i in infos).encode())
        files.append({
            "path": str(p.relative_to(config.ROOT)),
            "year": int(p.stem),
            "bytes": p.stat().st_size,
            "sha256": _sha256(p),
            "content_sha256": content.hexdigest(),
            "files_in_zip": len(infos),
            "unpacked_bytes": sum(i.file_size for i in infos),
            "fetched_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        })
    _write_json(config.RAW / "manifest.json", {"source": config.BASE_URL, "exports": files})


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", help="диапазон лет, например 2014-2024 (по умолчанию от 2014 до MaxYear)")
    ap.add_argument("--force", action="store_true", help="перекачать уже скачанные файлы")
    ap.add_argument("--skip-indicators", action="store_true", help="не скачивать показатели карты")
    args = ap.parse_args(argv)

    client = Client()
    max_year = fetch_references(client)
    if args.years:
        a, _, b = args.years.partition("-")
        years = range(int(a), int(b or a) + 1)
    else:
        years = range(config.FIRST_YEAR, max_year + 1)
    maps = json.loads((config.API / "Maps.json").read_text(encoding="utf-8"))
    for year in years:
        fetch_export(client, year, force=args.force)
        if not args.skip_indicators:
            fetch_map_indicators(client, year, maps, force=args.force)
    write_manifest()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
