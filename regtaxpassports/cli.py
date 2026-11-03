"""Командная строка: regtaxpassports collect | build | catalog."""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    commands = {
        "collect": "regtaxpassports.collect",
        "build": "regtaxpassports.build_db",
        "catalog": "regtaxpassports.catalog",
    }
    if not argv or argv[0] not in commands:
        print("использование: regtaxpassports {collect|build|catalog} [параметры]\n"
              "  collect  скачать паспорта и справочники с analytic.nalog.gov.ru\n"
              "  build    собрать data/db/regtaxpassports.duckdb и data/parquet/\n"
              "  catalog  сгенерировать docs/catalog/ из базы", file=sys.stderr)
        sys.exit(2)
    module = __import__(commands[argv[0]], fromlist=["main"])
    module.main(argv[1:])


if __name__ == "__main__":
    main()
