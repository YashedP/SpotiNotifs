import asyncio
import io
import json
import logging
import unittest

from flask import Flask
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from logging_config import configure_logging
from telemetry import instrument_flask, traced


class TelemetryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exporter = InMemorySpanExporter()
        cls.provider = TracerProvider()
        cls.provider.add_span_processor(SimpleSpanProcessor(cls.exporter))
        trace.set_tracer_provider(cls.provider)

    def setUp(self):
        self.exporter.clear()
        self.logs = io.StringIO()
        self.previous_handlers = logging.getLogger().handlers[:]
        configure_logging(service="test", stream=self.logs)

    def tearDown(self):
        logging.getLogger().handlers = self.previous_handlers

    def test_web_trace_correlates_logs_without_oauth_values(self):
        app = Flask(__name__)
        instrument_flask(app)

        @app.get("/callback")
        def callback():
            logging.getLogger(__name__).info("safe callback event")
            return "ok"

        @app.get("/health")
        def health():
            return "ok"

        client = app.test_client()
        response = client.get("/callback?code=private-code", headers={
            "Authorization": "Bearer private-token",
            "traceparent": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01",
        })
        self.assertEqual(response.status_code, 200)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].context.trace_id, int("1234567890abcdef1234567890abcdef", 16))
        for event in map(json.loads, self.logs.getvalue().splitlines()):
            self.assertEqual(event["trace_id"], "1234567890abcdef1234567890abcdef")
            self.assertEqual(event["span_id"], format(spans[0].context.span_id, "016x"))
        self.assertNotIn("private-", spans[0].to_json())
        self.assertNotIn("private-", self.logs.getvalue())
        client.get("/health")
        self.assertEqual(len(self.exporter.get_finished_spans()), 1)

    def test_concurrent_jobs_keep_distinct_trace_contexts(self):
        @traced("notifier.user")
        async def operation():
            await asyncio.sleep(0)
            logging.getLogger(__name__).info("user operation")

        async def run():
            await asyncio.gather(operation(), operation())

        asyncio.run(run())
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertNotEqual(spans[0].context.trace_id, spans[1].context.trace_id)
        logged_ids = {json.loads(line)["trace_id"] for line in self.logs.getvalue().splitlines()}
        self.assertEqual(logged_ids, {format(span.context.trace_id, "032x") for span in spans})
