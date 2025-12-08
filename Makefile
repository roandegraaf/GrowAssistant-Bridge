.PHONY: help install install-dev test lint format typecheck run clean

PYTHON := python3
VENV := .venv
BIN := $(VENV)/bin

help:
	@echo "GrowAssistant-Bridge Development Commands"
	@echo "=========================================="
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make install          Install production dependencies"
	@echo "  make install-dev      Install development dependencies"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             Run ruff and black checks"
	@echo "  make format           Auto-format with black and isort"
	@echo "  make typecheck        Run mypy type checking"
	@echo ""
	@echo "Testing & Running:"
	@echo "  make test             Run pytest with coverage"
	@echo "  make run              Run the application"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean            Remove cache files and directories"
	@echo ""
	@echo "Quick Start:"
	@echo "  ./setup-dev.sh        Setup development environment (one command)"
	@echo ""

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

install-dev: install
	$(PYTHON) -m pip install -r requirements-dev.txt
	pre-commit install

test:
	$(BIN)/pytest tests/ -v --cov=app --cov=web --cov=external_integrations --cov-report=term-missing --cov-report=html

lint:
	$(BIN)/ruff check .
	$(BIN)/black --check .

format:
	$(BIN)/black .
	$(BIN)/isort .

typecheck:
	$(BIN)/mypy app/ web/ external_integrations/ --ignore-missing-imports

run:
	$(PYTHON) -m app.main

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name *.egg-info -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name .coverage -delete 2>/dev/null || true
	@echo "Cleaned cache files"
