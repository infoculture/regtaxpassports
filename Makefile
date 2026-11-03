PYTHON ?= .venv/bin/python
# Портал доступен только с российских IP; см. docs/collection.md
REGTAX_PROXY ?=

.PHONY: venv collect build catalog test all

venv:
	uv venv .venv && uv pip install --python $(PYTHON) -e ".[dev]"

collect:
	REGTAX_PROXY=$(REGTAX_PROXY) $(PYTHON) -m regtaxpassports.cli collect

build:
	$(PYTHON) -m regtaxpassports.cli build

catalog:
	$(PYTHON) -m regtaxpassports.cli catalog

test:
	$(PYTHON) -m pytest -q tests

all: collect build catalog test
