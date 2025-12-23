# Testing Guide

This repository uses `pytest` for testing, with a focus on **Vertical Slice** architecture.

## 🧪 Quick Start

```bash
# Install dependencies
pip install .[dev]

# Run the full suite
make test

# Run specific layers
make test-unit         # Fast, isolated component tests
make test-integration  # SLOW. Tests DB, Queue, and Contracts.
make test-eod          # End-to-End user journeys.
```

## 🏗️ Test Architecture

### 1. Unit Tests (`tests/unit/`)
- **Goal**: Verify logic in isolation (Solvers, Signals, Parsers).
- **Speed**: < 10ms per test.
- **Rules**:
    - **NO** Network calls.
    - **NO** Database I/O (use in-memory mocks if needed, but prefer pure logic tests).
    - **MOCK** all external dependencies (`AlpacaClient`, `RedpandaProducer`).

### 2. Integration Tests (`tests/integration/`)
- **Goal**: Verify infrastructure wiring and contracts.
- **Speed**: Slower.
- **Rules**:
    - **YES** Database interaction (uses test DB).
    - **YES** Queue interaction (uses test topics or mocked producer with validation).
    - **MOCK** Third-party APIs (Alpaca, UW) using `aioresponses` or `respx`.

### 3. End-to-End Tests (`tests/e2e/`)
- **Goal**: Verify system boot and critical paths.
- **Tactics**: Simulate a full run of the `main_ingest` or `main_execution` loop.

## 🧰 Tools & Conventions

- **Pytest**: Core runner.
- **Pytest-Cov**: Coverage reporting.
- **Pytest-Asyncio**: For async/await support.
- **AioResponses**: For mocking HTTP clients.

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
Tests run automatically on GitHub Actions for every PR.
- **Hygiene**: `pre-commit` (ruff, black, mypy).
- **Tests**: Full `pytest` suite.
- **Sonar**: Quality gate analysis.

