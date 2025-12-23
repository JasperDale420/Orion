# Testing Guide

This repository uses `pytest` for testing.

## Prerequisites

Ensure dependencies are installed and the package is installed in editable mode:

```bash
pip install -r requirements.txt
pip install -e .
```

## Running Tests
 
We use a `Makefile` to standardize test execution.
 
```bash
make test              # Run all tests
make test-unit         # Run unit tests
make test-integration  # Run integration tests
make test-eod          # Run E2E tests
make test-coverage     # Generate HTML coverage report
```
 

 
## Configuration
 
Configuration is stored in `pyproject.toml` under `[tool.pytest.ini_options]`.

## Coverage

Coverage reports are automatically generated. To view:

```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html
```
