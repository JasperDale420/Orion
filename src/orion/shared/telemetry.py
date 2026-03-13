"""
OpenTelemetry integration for Orion.

This module provides optional distributed tracing capabilities.
Tracing is enabled when OTEL_ENABLED=true and opentelemetry packages are installed.

Usage:
    from orion.shared.telemetry import init_telemetry, get_tracer

    # In main startup
    init_telemetry(service_name="orion-api")

    # In code that needs tracing
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation"):
        ...
"""

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Check if OTEL is enabled
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")

# Check if opentelemetry packages are available
_otel_available = False
if OTEL_ENABLED:
    import importlib.util

    _otel_available = importlib.util.find_spec("opentelemetry") is not None
    if _otel_available:
        logger.info("OpenTelemetry packages available")
    else:
        logger.debug("OpenTelemetry packages not installed, tracing disabled")


class NoOpSpan:
    """No-op span for when tracing is disabled."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def __enter__(self) -> "NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class NoOpTracer:
    """No-op tracer for when tracing is disabled."""

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Generator[NoOpSpan, None, None]:
        yield NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> NoOpSpan:
        return NoOpSpan()


_tracer_provider: Any | None = None
_initialized = False


def init_telemetry(service_name: str = "orion") -> bool:
    """
    Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of the service for trace identification.

    Returns:
        True if tracing was initialized, False if disabled or unavailable.
    """
    global _tracer_provider, _initialized

    if _initialized:
        return _otel_available

    _initialized = True

    if not OTEL_ENABLED:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED not set)")
        return False

    if not _otel_available:
        logger.warning("OTEL_ENABLED=true but opentelemetry packages not installed")
        return False

    try:
        # Import here to avoid errors when packages not available
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create resource with service info
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": os.getenv("ORION_VERSION", "1.0.0"),
            }
        )

        # Create tracer provider
        _tracer_provider = TracerProvider(resource=resource)

        # Configure OTLP exporter (collector endpoint from env)
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

        # Set global tracer provider
        trace.set_tracer_provider(_tracer_provider)

        logger.info(f"OpenTelemetry initialized for {service_name} -> {otlp_endpoint}")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry: {e}")
        return False


def get_tracer(name: str) -> Any:
    """
    Get a tracer for the given module name.

    Returns a real tracer if OTEL is available, otherwise a NoOpTracer.

    Args:
        name: Module name (typically __name__).

    Returns:
        OpenTelemetry Tracer or NoOpTracer.
    """
    if _otel_available and OTEL_ENABLED:
        from opentelemetry import trace

        return trace.get_tracer(name)
    return NoOpTracer()


def instrument_fastapi(app: Any) -> None:
    """
    Instrument a FastAPI application for automatic tracing.

    Args:
        app: FastAPI application instance.
    """
    if not _otel_available or not OTEL_ENABLED:
        logger.debug("Skipping FastAPI instrumentation (OTEL disabled)")
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI instrumented for OpenTelemetry")
    except Exception as e:
        logger.warning(f"Failed to instrument FastAPI: {e}")
