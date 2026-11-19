"""Generate the five provisioned Grafana dashboards. Run: python scripts/gen_dashboards.py"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "monitoring/grafana/dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
W = "$__rate_interval"


def panel(i, title, exprs, kind="timeseries", x=0, y=0, w=12, h=8, unit=None, desc=None, ds=DS):
    p = {"id": i, "type": kind, "title": title, "datasource": ds, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [{"refId": chr(65 + n), "expr": e, "legendFormat": lf, "datasource": ds}
                     for n, (e, lf) in enumerate(exprs)],
         "fieldConfig": {"defaults": {**({"unit": unit} if unit else {}), "noValue": "No data"}, "overrides": []}}
    if desc:
        p["description"] = desc
    return p


def dash(uid, title, panels):
    return {"uid": uid, "title": title, "schemaVersion": 39, "version": 1, "editable": True, "refresh": "30s",
            "time": {"from": "now-6h", "to": "now"}, "tags": ["safeloop"], "panels": panels}


def q(p, expr):
    return f"histogram_quantile({p}, sum by (le) (rate({expr}_bucket[{W}])))"


D = {}
D["a-overview"] = dash("sl-overview", "Safe-Loop Overview", [
    panel(1, "Task volume by status", [(f"sum by (status) (increase(safeloop_tasks_total[{W}]))", "{{status}}")], x=0, y=0),
    panel(2, "Success rate (finished tasks)", [(
        'sum(increase(safeloop_tasks_total{status="SUCCESS"}[1h])) / clamp_min(sum(increase(safeloop_tasks_total[1h])), 1)',
        "success")], "stat", x=12, y=0, w=6, unit="percentunit"),
    panel(3, "Active tasks", [("safeloop_active_tasks", "active")], "stat", x=18, y=0, w=6),
    panel(4, "Task duration p50/p95", [(q(0.5, "safeloop_task_duration_seconds"), "p50"),
                                       (q(0.95, "safeloop_task_duration_seconds"), "p95")], x=0, y=8, unit="s"),
    panel(5, "Guardrail terminations by reason", [(
        f"sum by (reason) (increase(safeloop_guardrail_terminations_total[{W}]))", "{{reason}}")], x=12, y=8),
    panel(6, "Structured logs (Loki)", [('{service="api"} | json', "")], "logs", x=0, y=16, w=24, ds=LOKI),
])
D["b-workflow"] = dash("sl-workflow", "Agent and Workflow Performance", [
    panel(1, "Generation latency p95 by kind", [(
        f"histogram_quantile(0.95, sum by (le, kind) (rate(safeloop_generation_duration_seconds_bucket[{W}])))",
        "{{kind}}")], unit="s"),
    panel(2, "Verification latency p50/p95", [(q(0.5, "safeloop_verification_duration_seconds"), "p50"),
                                              (q(0.95, "safeloop_verification_duration_seconds"), "p95")],
          x=12, unit="s"),
    panel(3, "Repair attempts", [(f"increase(safeloop_repairs_total[{W}])", "repairs")], y=8),
    panel(4, "Model calls per finished task (proxy for attempts/task)", [(
        f"sum(rate(safeloop_generation_requests_total[{W}])) / clamp_min(sum(rate(safeloop_tasks_total[{W}])), 1e-9)",
        "calls/task")], x=12, y=8, desc="Approximation: includes provider errors and retries."),
    panel(5, "Verification outcomes by failure category", [(
        f"sum by (failure_category) (increase(safeloop_verification_runs_total[{W}]))", "{{failure_category}}")],
        y=16, w=24),
])
D["c-economics"] = dash("sl-economics", "Model Economics", [
    panel(1, "Tokens by type (where reported)", [(
        f"sum by (model, type) (increase(safeloop_tokens_total[{W}]))", "{{model}} {{type}}")]),
    panel(2, "Model call latency p95", [(
        f"histogram_quantile(0.95, sum by (le, model) (rate(safeloop_model_call_duration_seconds_bucket[{W}])))",
        "{{model}}")], x=12, unit="s"),
    panel(3, "Cost by source (reported vs estimated)", [(
        f"sum by (source) (increase(safeloop_model_cost_usd_total[{W}]))", "{{source}}")], y=8, unit="currencyUSD",
        desc="Only calls with a known cost contribute. Local models (Ollama) report no cost."),
    panel(4, "Cost per finished task (known cost only)", [(
        "sum(increase(safeloop_model_cost_usd_total[1h])) / clamp_min(sum(increase(safeloop_tasks_total[1h])), 1)",
        "$/task")], "stat", x=12, y=8, w=6, unit="currencyUSD"),
    panel(5, "Cost per successful task (known cost only)", [(
        'sum(increase(safeloop_model_cost_usd_total[1h])) / clamp_min(sum(increase(safeloop_tasks_total{status="SUCCESS"}[1h])), 1)',
        "$/success")], "stat", x=18, y=8, w=6, unit="currencyUSD"),
])
D["d-sandbox"] = dash("sl-sandbox", "Sandbox Reliability", [
    panel(1, "Sandbox failures by kind", [(
        f"sum by (kind) (increase(safeloop_sandbox_failures_total[{W}]))", "{{kind}}")],
        desc="kinds: startup, execution, timeout, resource_limit, cleanup"),
    panel(2, "Execution duration p50/p95", [(q(0.5, "safeloop_sandbox_execution_duration_seconds"), "p50"),
                                            (q(0.95, "safeloop_sandbox_execution_duration_seconds"), "p95")],
          x=12, unit="s"),
    panel(3, "Running sandboxes", [("safeloop_running_sandboxes", "running")], y=8),
    panel(4, "Cleanup failures", [(
        f'increase(safeloop_sandbox_failures_total{{kind="cleanup"}}[{W}])', "cleanup")], x=12, y=8),
])
D["e-guardrails"] = dash("sl-guardrails", "Guardrails", [
    panel(1, "Budget exhaustion", [(f'increase(safeloop_tasks_total{{status="BUDGET_EXHAUSTED"}}[{W}])', "budget")]),
    panel(2, "Deadline exceeded", [(f'increase(safeloop_tasks_total{{status="DEADLINE_EXCEEDED"}}[{W}])', "deadline")], x=12),
    panel(3, "Stagnation detections", [(
        f'increase(safeloop_guardrail_terminations_total{{reason="STAGNATION"}}[{W}])', "stagnation")], y=8),
    panel(4, "Policy blocks", [(f'increase(safeloop_tasks_total{{status="BLOCKED_BY_POLICY"}}[{W}])', "blocked")], x=12, y=8),
    panel(5, "Retry-limit terminations", [(
        f'increase(safeloop_guardrail_terminations_total{{reason="MAX_ATTEMPTS_REACHED"}}[{W}])', "max attempts")], y=16),
])
for name, d in D.items():
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1))
print("wrote", len(D), "dashboards")
