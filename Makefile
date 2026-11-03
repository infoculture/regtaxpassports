PYTHON ?= .venv/bin/python
# Портал доступен только с российских IP; см. docs/collection.md
REGTAX_PROXY ?=

.PHONY: venv collect build catalog test notebook notebook-run all

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

# Тетрадка с примерами: зависимости .[notebook] и JupyterLab
notebook:
	uv pip install --python $(PYTHON) -e ".[notebook]"
	$(PYTHON) -m jupyter lab docs/examples.ipynb

# Пересчитать тетрадку и сохранить результаты ячеек в файл
notebook-run:
	uv pip install --python $(PYTHON) -e ".[notebook]"
	$(PYTHON) -m jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.record_timing=False docs/examples.ipynb

all: collect build catalog test
