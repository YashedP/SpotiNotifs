"""OTLP traces correlated with JSON stdout logs, without request payload capture."""

import functools
import inspect
import os
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_configured = False


def configure_tracing(service: str) -> None:
    global _configured
    if _configured or os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return
    if not (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
        return
    service_name = os.getenv("OTEL_SERVICE_NAME", f"spotinotifs-{service}")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def traced(name: str):
    """Trace one operation, preserving asyncio context and never recording args/errors."""
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                with trace.get_tracer(__name__).start_as_current_span(
                    name, record_exception=False, set_status_on_exception=False
                ) as span:
                    try:
                        return await function(*args, **kwargs)
                    except BaseException:
                        span.set_status(Status(StatusCode.ERROR, "operation failed"))
                        raise
            return asynchronous

        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            with trace.get_tracer(__name__).start_as_current_span(
                name, record_exception=False, set_status_on_exception=False
            ) as span:
                try:
                    return function(*args, **kwargs)
                except BaseException:
                    span.set_status(Status(StatusCode.ERROR, "operation failed"))
                    raise
        return synchronous
    return decorate


def inject_trace_context(headers: dict[str, str]) -> None:
    TraceContextTextMapPropagator().inject(headers)


def instrument_flask(app) -> None:
    from flask import g, request

    @app.before_request
    def start_request_trace():
        if request.path == "/health":
            return
        # Header lookup is case sensitive in the generic carrier.
        context = TraceContextTextMapPropagator().extract(
            {key.lower(): value for key, value in request.headers}
        )
        route = str(request.url_rule) if request.url_rule else "unmatched"
        manager = trace.get_tracer(__name__).start_as_current_span(
            f"{request.method} {route}", context=context, kind=SpanKind.SERVER,
            attributes={"http.request.method": request.method, "http.route": route},
            record_exception=False, set_status_on_exception=False,
        )
        g.trace_manager = manager
        g.request_span = manager.__enter__()
        g.trace_started_at = time.monotonic()

    @app.after_request
    def finish_request_trace(response):
        if span := getattr(g, "request_span", None):
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR, "HTTP server error"))
            app.logger.info("HTTP request completed", extra={
                "event": "request_complete", "method": request.method,
                "route": str(request.url_rule) if request.url_rule else "unmatched",
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - g.trace_started_at) * 1000, 3),
            })
        return response

    @app.teardown_request
    def close_request_trace(error):
        if manager := g.pop("trace_manager", None):
            if error:
                g.request_span.set_status(Status(StatusCode.ERROR, "request failed"))
            manager.__exit__(None, None, None)
