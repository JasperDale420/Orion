.PHONY: test test-unit test-integration test-eod test-coverage lint clean-artifacts help

help:
	@echo "Available commands:"
	@echo "  make test              Run all tests"
	@echo "  make test-unit         Run unit tests only"
	@echo "  make test-integration  Run integration tests only"
	@echo "  make test-eod          Run E2E tests only"
	@echo "  make test-coverage     Run tests with coverage report"
	@echo "  make lint              Run ruff and black check"
	@echo "  make clean-artifacts   Remove test artifacts"

test:
	pytest

test-unit:
	pytest tests/unit

test-integration:
	pytest tests/integration

test-eod:
	pytest tests/e2e

test-coverage:
	pytest --cov=src --cov-report=term-missing --cov-report=html --cov-report=xml

lint:
	ruff check .
	black --check .

clean-artifacts:
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -f .coverage
