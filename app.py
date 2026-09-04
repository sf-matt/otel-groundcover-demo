import json
import logging
import os
import sys
import time
from typing import Any

import requests
from flask import Flask, Response, jsonify, request
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.propagate import extract
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest


class TraceContextJsonFormatter(logging.Formatter):
    """JSON logs carrying trace_id/span_id so groundcover can jump between logs and traces."""

    def format(self, record: logging.LogRecord) -> str:
        span_context = trace.get_current_span().get_span_context()
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if span_context.is_valid:
            payload["trace_id"] = f"{span_context.trace_id:032x}"
            payload["span_id"] = f"{span_context.span_id:016x}"
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(TraceContextJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "otel-groundcover-demo"),
            "service.version": os.getenv("APP_VERSION", "dev"),
            "deployment.environment.name": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
        }
    )


def configure_telemetry(resource: Resource) -> None:
    provider = TracerProvider(resource=resource)
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # No endpoint= here on purpose: letting the exporter read
        # OTEL_EXPORTER_OTLP_ENDPOINT itself makes it auto-append the
        # correct per-signal path (/v1/traces here, /v1/metrics,
        # /v1/logs for the exporters below). Passing endpoint= explicitly
        # disables that auto-append entirely - confirmed directly against
        # the installed SDK - which is exactly what broke metrics/logs
        # when OTEL_EXPORTER_OTLP_ENDPOINT was set to the /v1/traces URL
        # for the trace exporter specifically. Set this env var to the
        # bare base endpoint (no /v1/... suffix) everywhere it's configured.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def configure_metrics(resource: Resource) -> None:
    """Push metrics via OTLP, in addition to the /metrics scrape endpoint below.

    Safe to enable everywhere: on k8s this just adds a second, redundant path
    for the same custom gauge alongside the existing Prometheus scrape - no
    duplication risk the way logs have (see configure_logs_export).
    """
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        metrics.set_meter_provider(MeterProvider(resource=resource))
        return
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


def configure_logs_export(resource: Resource) -> None:
    """Push logs via OTLP - only when explicitly enabled, e.g. on Fargate.

    On k8s, groundcover's sensor already tails pod stdout directly; enabling
    this there too would double-ingest every log line. OTEL_LOGS_ENABLED
    keeps this opt-in per environment rather than tied to the same endpoint
    check traces/metrics use.
    """
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_LOGS_ENABLED") != "true":
        return
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))


def link_deploy_trace() -> None:
    """If the deploy pipeline passed a traceparent, emit one startup span as its child.

    This connects the CI/CD deploy trace (exported by the export-traces workflow) to
    this running instance of the workload, so an RCA can jump from "what changed"
    straight to "what the app did right after that change went live".
    """
    traceparent = os.getenv("TRACEPARENT")
    if not traceparent:
        return
    ctx = extract({"traceparent": traceparent})
    with tracer.start_as_current_span("app.startup", context=ctx) as span:
        span.set_attribute("demo.deploy.traceparent", traceparent)
        logger.info("linked startup to deploy trace %s", traceparent)


configure_logging()
_resource = build_resource()
configure_telemetry(_resource)
configure_metrics(_resource)
configure_logs_export(_resource)
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
tracer = trace.get_tracer(__name__)
logger = logging.getLogger("otel-groundcover-demo")

WORKLOAD_QUEUE_DEPTH = Gauge(
    "demo_workload_queue_depth",
    "Simulated backlog depth for this workload, set via /work?queue_depth=N",
)
WORKLOAD_QUEUE_DEPTH_OTEL = metrics.get_meter(__name__).create_gauge(
    "demo_workload_queue_depth",
    description="Simulated backlog depth for this workload, set via /work?queue_depth=N",
)

link_deploy_trace()


@app.get("/")
def index() -> Any:
    return jsonify({
        "service": "otel-groundcover-demo",
        "status": "ok",
        "routes": [
            "/healthz",
            "/metrics",
            "/work",
            "/fail/http-500",
            "/fail/exception",
            "/fail/timeout",
            "/fail/downstream",
        ],
    })


@app.get("/healthz")
def healthz() -> Any:
    return jsonify({"status": "healthy"})


@app.get("/metrics")
def metrics_endpoint() -> Any:
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.get("/work")
def work() -> Any:
    with tracer.start_as_current_span("demo.work") as span:
        workload = request.args.get("workload", "default")
        queue_depth = float(request.args.get("queue_depth", "0"))
        span.set_attribute("demo.workload", workload)
        span.set_attribute("demo.queue_depth", queue_depth)
        WORKLOAD_QUEUE_DEPTH.set(queue_depth)
        WORKLOAD_QUEUE_DEPTH_OTEL.set(queue_depth)
        time.sleep(0.02)
        return jsonify({"status": "completed", "workload": workload, "queue_depth": queue_depth})


@app.get("/fail/http-500")
def http_500() -> Any:
    with tracer.start_as_current_span("demo.failure.http_500") as span:
        span.set_attribute("demo.failure.kind", "http_500")
        span.set_status(trace.Status(trace.StatusCode.ERROR, "intentional HTTP 500"))
        logger.error("intentional HTTP 500 triggered")
        return jsonify({"error": "intentional HTTP 500"}), 500


@app.get("/fail/exception")
def exception_failure() -> Any:
    with tracer.start_as_current_span("demo.failure.exception") as span:
        try:
            raise RuntimeError("intentional exception for tracing")
        except RuntimeError as error:
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(error)))
            logger.error("intentional exception triggered: %s", error)
            return jsonify({"error": str(error)}), 500


@app.get("/fail/timeout")
def timeout_failure() -> Any:
    seconds = min(float(request.args.get("seconds", "2")), 10.0)
    with tracer.start_as_current_span("demo.failure.timeout") as span:
        span.set_attribute("demo.timeout.seconds", seconds)
        logger.warning("intentional slow response: sleeping %ss", seconds)
        time.sleep(seconds)
        return jsonify({"error": "intentional slow response", "slept_seconds": seconds}), 504


@app.get("/fail/downstream")
def downstream_failure() -> Any:
    target = request.args.get("url", "http://127.0.0.1:9/unreachable")
    with tracer.start_as_current_span("demo.failure.downstream") as span:
        span.set_attribute("demo.downstream.url", target)
        try:
            requests.get(target, timeout=0.5)
        except requests.RequestException as error:
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR, "intentional downstream failure"))
            logger.error("intentional downstream failure calling %s: %s", target, error)
            return jsonify({"error": "downstream request failed", "detail": str(error)}), 502
        return jsonify({"error": "downstream unexpectedly succeeded"}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
