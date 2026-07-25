# Testing Guide

This repository uses `pytest` for testing, with a focus on **Vertical Slice** architecture.

## 🧪 Quick Start

```bash
# Install dependencies
uv sync

# Run the full suite
uv run pytest

# Run specific layers
uv run pytest tests/unit          # Fast, isolated component tests
uv run pytest tests/integration   # SLOW. Tests DB, Queue, and Contracts.
uv run pytest tests/e2e           # End-to-end tests (needs real TimescaleDB on port 5440 — see docs/testing-guide.md)
```

## 🏗️ Test Architecture

### 1. Unit Tests (`tests/unit/`)
- **Goal**: Verify logic in isolation (Solvers, Signals, Parsers).
- **Speed**: < 10ms per test.
- **Rules**:
    - **NO** Network calls.
    - **NO** Database I/O (use in-memory mocks if needed, but prefer pure logic tests).
    - **MOCK** all external dependencies (`AlpacaClient`, gateway readers, broker clients).

### 2. Integration Tests (`tests/integration/`)
- **Goal**: Verify infrastructure wiring and contracts.
- **Speed**: Slower.
- **Rules**:
    - **YES** Database interaction (uses test DB).
    - **YES** Queue interaction (uses test topics or mocked producer with validation).
    - **MOCK** Third-party APIs (Alpaca, UW) using `unittest.mock`.

### 3. End-to-End Tests (`tests/e2e/`)
- **Goal**: Verify system boot and critical paths.
- **Tactics**: Simulate a full run of the `orion.ingestion` or `main_execution` loop.

## 🧰 Tools & Conventions

- **Pytest**: Core runner.
- **Pytest-Cov**: Coverage reporting.
- **Pytest-Asyncio**: For async/await support.

### Mocking Strategy
We use `unittest.mock` and `pytest fixtures`.

**Example Pattern:**
```python
@pytest.fixture
def mock_alpaca():
    with patch("orion.connectors.alpaca.AlpacaClient") as mock:
        yield mock

def test_signal_generation(mock_alpaca):
    # Setup
    mock_alpaca.get_bars.return_value = [...]

    # Act
    result = generate_signals(...)

    # Assert
    assert result.action == "BUY"
```

## 🔍 CI / CD
Tests run automatically on GitHub Actions for every PR (`.github/workflows/ci.yml`).
- **Hygiene**: `pre-commit run --all-files` (ruff + ruff-format, detect-secrets, trailing-whitespace/EOF/YAML/merge-conflict checks).
- **Type check**: `mypy src/orion` (separate CI step, not part of pre-commit).
- **Tests**: `pytest` against in-memory SQLite (`--ignore=tests/e2e`), then a dedicated E2E smoke step against a real Postgres/pgvector service container with `alembic upgrade head` applied first.
- **Sonar**: SonarQube quality-gate analysis (skipped if `SONAR_TOKEN` isn't set).
