#!/bin/sh
# Скачать все налоговые паспорта и справочники портала.
# Раньше здесь был список wget по годам 2014–2023; теперь годы берутся из
# /api/TaxPassports/MaxYear, а загрузку выполняет regtaxpassports/collect.py.
#
# Портал отвечает только российским IP. Через RU-прокси:
#   REGTAX_PROXY=socks5h://127.0.0.1:1080 ./download.sh
set -e
cd "$(dirname "$0")"
exec .venv/bin/python -m regtaxpassports.cli collect "$@"
