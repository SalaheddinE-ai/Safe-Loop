"""Prometheus metrics. Labels are bounded (provider/model/outcome/category); no task IDs,
prompts or free-form exception strings. Metrics never influence task outcomes."""
from __future__ import annotations

import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from ..domain.enums import CostSource
from ..domain.models import ModelUsage
from .tracing import span

REGISTRY = CollectorRegistry()
_B = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

tasks_total = Counter("safeloop_tasks_total", "Tasks by terminal status", ["status"], registry=REGISTRY)
generation_requests_total = Counter("safeloop_generation_requests_total", "Model generation/repair requests",
                                    ["provider", "model", "kind", "outcome"], registry=REGISTRY)
verification_runs_total = Counter("safeloop_verification_runs_total", "Verification runs",
                                  ["failure_category"], registry=REGISTRY)
repairs_total = Counter("safeloop_repairs_total", "Repair attempts started", registry=REGISTRY)
guardrail_terminations_total = Counter("safeloop_guardrail_terminations_total",
                                       "Terminations triggered by a guardrail", ["reason"], registry=REGISTRY)
model_errors_total = Counter("safeloop_model_errors_total", "Provider errors",
                             ["provider", "model", "transient"], registry=REGISTRY)
sandbox_failures_total = Counter("safeloop_sandbox_failures_total", "Sandbox failures", ["kind"],
                                 registry=REGISTRY)
tokens_total = Counter("safeloop_tokens_total", "Tokens reported by providers",
                       ["provider", "model", "type"], registry=REGISTRY)
model_cost_usd_total = Counter(
    "safeloop_model_cost_usd_total",
    "USD cost where available. source=reported (provider) or estimated (configured pricing).",
    ["provider", "model", "source"], registry=REGISTRY)

task_duration_seconds = Histogram("safeloop_task_duration_seconds", "End-to-end task duration",
                                  buckets=_B, registry=REGISTRY)
generation_duration_seconds = Histogram("safeloop_generation_duration_seconds", "Generation latency",
                                        ["kind"], buckets=_B, registry=REGISTRY)
verification_duration_seconds = Histogram("safeloop_verification_duration_seconds",
                                          "Verification (sandbox tests) duration", buckets=_B, registry=REGISTRY)
model_call_duration_seconds = Histogram("safeloop_model_call_duration_seconds", "Model call latency",
                                        ["provider", "model"], buckets=_B, registry=REGISTRY)
sandbox_execution_duration_seconds = Histogram("safeloop_sandbox_execution_duration_seconds",
                                               "Sandbox execution duration", buckets=_B, registry=REGISTRY)

active_tasks = Gauge("safeloop_active_tasks", "Tasks currently running", registry=REGISTRY)
running_sandboxes = Gauge("safeloop_running_sandboxes", "Sandbox executions in flight", registry=REGISTRY)
queue_depth = Gauge("safeloop_queue_depth", "Tasks waiting for a worker", registry=REGISTRY)

GUARDRAIL_REASONS = {"COST_LIMIT", "DEADLINE", "MAX_ATTEMPTS_REACHED", "STAGNATION", "POLICY_VIOLATION"}


def record_usage(usage: ModelUsage) -> None:
    if usage.input_tokens is not None:
        tokens_total.labels(usage.provider, usage.model, "input").inc(usage.input_tokens)
    if usage.output_tokens is not None:
        tokens_total.labels(usage.provider, usage.model, "output").inc(usage.output_tokens)
    if usage.cost_usd is not None and usage.cost_source != CostSource.UNAVAILABLE:
        model_cost_usd_total.labels(usage.provider, usage.model, usage.cost_source.value.lower()).inc(
            usage.cost_usd)


class InstrumentedProvider:
    """Decorator: records metrics, never alters results."""

    def __init__(self, inner):
        self._inner = inner
        self.name = inner.name

    def _call(self, kind, fn, prompt, timeout):
        meta = self._inner.get_model_metadata()
        model = meta.get("model", "unknown")
        t0 = time.monotonic()
        try:
            with span("safeloop.model_call", kind=kind, provider=self.name, model=model):
                resp = fn(prompt, timeout=timeout)
        except Exception as exc:
            transient = str(bool(getattr(exc, "transient", False))).lower()
            model_errors_total.labels(self.name, model, transient).inc()
            generation_requests_total.labels(self.name, model, kind, "error").inc()
            raise
        dt = time.monotonic() - t0
        model_call_duration_seconds.labels(self.name, model).observe(dt)
        generation_duration_seconds.labels(kind).observe(dt)
        generation_requests_total.labels(self.name, model, kind, "ok").inc()
        record_usage(resp.usage)
        return resp

    def generate_code(self, prompt, *, timeout):
        return self._call("generate", self._inner.generate_code, prompt, timeout)

    def repair_code(self, prompt, *, timeout):
        return self._call("repair", self._inner.repair_code, prompt, timeout)

    def get_model_metadata(self):
        return self._inner.get_model_metadata()

    def estimate_cost(self, prompt):
        return self._inner.estimate_cost(prompt)


class InstrumentedSandbox:
    def __init__(self, inner):
        self._inner = inner

    def create_execution(self, task_id, files):
        try:
            eid = self._inner.create_execution(task_id, files)
        except Exception:
            sandbox_failures_total.labels("startup").inc()
            raise
        running_sandboxes.inc()
        return eid

    def execute_tests(self, execution_id):
        try:
            self._inner.execute_tests(execution_id)
        except Exception:
            sandbox_failures_total.labels("execution").inc()
            raise

    def collect_results(self, execution_id):
        with span("safeloop.sandbox_collect"):
            raw = self._inner.collect_results(execution_id)
        sandbox_execution_duration_seconds.observe(raw.duration)
        verification_duration_seconds.observe(raw.duration)
        if raw.timed_out:
            sandbox_failures_total.labels("timeout").inc()
        if raw.oom_killed:
            sandbox_failures_total.labels("resource_limit").inc()
        return raw

    def terminate_execution(self, execution_id):
        self._inner.terminate_execution(execution_id)

    def cleanup_execution(self, execution_id):
        try:
            self._inner.cleanup_execution(execution_id)
        except Exception:
            sandbox_failures_total.labels("cleanup").inc()
            raise
        finally:
            running_sandboxes.dec()
