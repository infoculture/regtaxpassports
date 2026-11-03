"""Пути и параметры доступа к порталу."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
EXPORTS = RAW / "exports"          # ZIP из /api/TaxPassports/ExportAll
API = RAW / "api"                  # JSON-ответы справочных методов и показателей карты
SNAPSHOTS = RAW / "snapshots"      # ранее скачанные архивы: snapshots/<дата>/<год>.zip
DB_DIR = DATA / "db"
DB_PATH = DB_DIR / "regtaxpassports.duckdb"
PARQUET = DATA / "parquet"

BASE_URL = "https://analytic.nalog.gov.ru/api/TaxPassports"
FIRST_YEAR = 2014  # портал строит список периодов от 2014 до MaxYear

# Портал не отвечает с зарубежных IP. Прокси задаётся переменной окружения,
# например REGTAX_PROXY=socks5h://127.0.0.1:1080 (RU-выход, см. docs/collection.md).
PROXY = os.environ.get("REGTAX_PROXY") or None
USER_AGENT = os.environ.get(
    "REGTAX_USER_AGENT",
    "Mozilla/5.0 (compatible; regtaxpassports-collector/0.1; +https://github.com/infoculture/finguide)",
)
INTERVAL = float(os.environ.get("REGTAX_INTERVAL", "1.0"))  # секунд между запросами
TIMEOUT = float(os.environ.get("REGTAX_TIMEOUT", "180"))
