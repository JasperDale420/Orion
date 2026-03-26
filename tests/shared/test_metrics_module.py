"""
Unit tests for Metrics module (Refactor Slice 3).
"""

import pytest

from orion.shared.metrics import Metrics, init_metrics


@pytest.mark.asyncio
async def test_metrics_singleton():
    """Test metrics is a singleton."""
    m1 = await Metrics.get_instance()
    m2 = await Metrics.get_instance()
    assert m1 is m2


@pytest.mark.asyncio
async def test_metrics_has_required_metrics():
    """Test all expected metrics are defined."""
    metrics = await Metrics.get_instance()

    # Ingestion metrics
    assert hasattr(metrics, "ingest_events_total")
    assert hasattr(metrics, "ingest_candidates_total")
    assert hasattr(metrics, "ingest_loop_duration_seconds")

    # Execution metrics
    assert hasattr(metrics, "execution_decisions_total")
    assert hasattr(metrics, "execution_orders_total")
    assert hasattr(metrics, "execution_latency_seconds")
    assert hasattr(metrics, "execution_queue_depth")

    # Risk metrics
    assert hasattr(metrics, "risk_equity")
    assert hasattr(metrics, "risk_exposure")
    assert hasattr(metrics, "risk_daily_loss")
    assert hasattr(metrics, "risk_open_positions")


@pytest.mark.asyncio
async def test_init_metrics_returns_instance():
    """Test init_metrics returns Metrics instance."""
    metrics = await init_metrics()
    assert isinstance(metrics, Metrics)


@pytest.mark.asyncio
async def test_metrics_counter_increment():
    """Test counter metrics can be incremented."""
    metrics = await Metrics.get_instance()

    # Before value
    before = metrics.ingest_events_total.labels(source="TEST")._value._value

    # Increment
    metrics.ingest_events_total.labels(source="TEST").inc()

    # After value should be +1
    after = metrics.ingest_events_total.labels(source="TEST")._value._value
    assert after == before + 1


@pytest.mark.asyncio
async def test_metrics_gauge_set():
    """Test gauge metrics can be set."""
    metrics = await Metrics.get_instance()

    # Set equity gauge
    metrics.risk_equity.set(100000.0)

    # Read value
    value = metrics.risk_equity._value._value
    assert value == 100000.0


@pytest.mark.asyncio
async def test_metrics_histogram_observe():
    """Test histogram metrics can observe values."""
    metrics = await Metrics.get_instance()

    # Observe latency (histograms don't expose simple read access, just verify no error)
    metrics.execution_latency_seconds.labels(ticker="SPY").observe(0.5)
    metrics.ingest_loop_duration_seconds.observe(1.5)

    # If we got here without errors, the histogram is working
    assert True
