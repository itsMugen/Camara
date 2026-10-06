.PHONY: install constraints lint test test-all up down

# The host port MongoDB is published on, as in compose.yaml (optionally set in .env).
-include .env
MONGO_HOST_PORT ?= 27017

# pip in a venv made with `python -m venv`, `uv pip` in one made with `uv venv` (which has no pip)
PIP := $(shell python -m pip --version >/dev/null 2>&1 && echo "python -m pip" || echo "uv pip")

install:            ## install the package and dev tools at the pinned versions (Python 3.11+)
	@python -c 'import sys; v = sys.version_info; ok = v >= (3, 11); \
	  ok or print(f"error: Python 3.11+ is required, this is Python {v.major}.{v.minor}.", \
	  "Create the venv with `uv venv --python 3.12` or `python3.12 -m venv .venv`.", file=sys.stderr); \
	  sys.exit(0 if ok else 1)'
	$(PIP) install -e ".[dev]" -c constraints.txt

constraints:        ## re-pin constraints.txt to the versions installed in this environment
	{ sed -n "1,3p" constraints.txt; $(PIP) freeze --exclude-editable; } > constraints.tmp
	mv constraints.tmp constraints.txt

lint:               ## static checks
	ruff check .
	ruff format --check .
	mypy

test:               ## unit tests (no database needed)
	pytest

test-all:           ## unit + integration tests against MongoDB on localhost (make up)
	SHORTENER_TEST_MONGO_URI=mongodb://localhost:$(MONGO_HOST_PORT) pytest --cov

up:                 ## start MongoDB in Docker, published on 127.0.0.1:MONGO_HOST_PORT (default 27017)
	docker compose up -d --wait mongo

down:               ## stop MongoDB (links persist in the mongo-data volume)
	docker compose down
