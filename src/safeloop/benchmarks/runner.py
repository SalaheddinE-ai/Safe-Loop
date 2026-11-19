"""Benchmark runner. Requires a real sandbox for real results. Never writes numbers it did not measure."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ..agents.providers.base import CodeProvider
from ..core.config import Settings
from ..domain.models import TaskSpec
from ..sandbox.base import Sandbox
from ..workflow.graph import Deps, run_task
from .baselines import CONFIGS
from .metrics import RunResult, Summary, summarize

DATASET = Path(__file__).parent / "datasets" / "tasks_v1.json"


def load_tasks() -> tuple[str, list[dict]]:
    d = json.loads(DATASET.read_text())
    return d["version"], d["tasks"]


def run_benchmark(provider_factory: Callable[[dict, str, int], CodeProvider], sandbox: Sandbox, *,
                  configs: tuple[str, ...] = ("A", "B", "C"), repeats: int = 1, max_attempts: int = 3,
                  tasks: list[dict] | None = None, settings: Settings | None = None,
                  timeout_seconds: float = 120.0, max_cost: float = 1.0) -> list[RunResult]:
    """provider_factory(task, config_key, repeat) -> provider (fresh per run)."""
    if tasks is None:
        _, tasks = load_tasks()
    results: list[RunResult] = []
    for cfg_key in configs:
        cfg = CONFIGS[cfg_key]
        for rep in range(repeats):
            for t in tasks:
                spec = TaskSpec(user_request=t["request"], test_strategy=t["tests"],
                                maximum_attempts=cfg.max_attempts or max_attempts,
                                timeout_seconds=timeout_seconds, maximum_cost=max_cost)
                seen: list[int] = []

                def on_attempt(task, rec, art, ver, seen=seen):
                    if ver is not None and (ver.tests_passed + ver.tests_failed) > 0:
                        seen.append(ver.tests_passed)

                deps = Deps(provider=provider_factory(t, cfg_key, rep), sandbox=sandbox,
                            settings=settings or Settings(), static_policy=cfg.static_policy,
                            on_attempt=on_attempt)
                rep_ = run_task(spec, deps)
                regression = any(seen[i] < max(seen[:i]) for i in range(1, len(seen)))
                results.append(RunResult(
                    task_id=t["id"], config=cfg_key, repeat=rep, status=rep_.final_status.value,
                    attempts=rep_.attempt_count,
                    first_attempt_success=(rep_.final_status.value == "SUCCESS" and rep_.attempt_count == 1),
                    regression=regression, executed_attempts=len(seen), duration=rep_.total_duration,
                    cost_usd=rep_.total_cost_usd if any(s.value != "UNAVAILABLE" for s in rep_.cost_sources)
                    else None,
                    termination_reason=rep_.termination_reason.value))
    return results


def write_report(results: list[RunResult], out_dir: Path, *, model: str, repeats: int,
                 harness_selftest: bool = False) -> Path:
    version, tasks = load_tasks()
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[Summary] = [summarize(k, [r for r in results if r.config == k])
                                for k in sorted({r.config for r in results})]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    meta = {"benchmark_version": version, "n_tasks": len(tasks), "repeats": repeats, "model": model,
            "generated_at": stamp, "harness_selftest": harness_selftest}
    (out_dir / f"run_{stamp}.json").write_text(json.dumps(
        {"meta": meta, "summaries": [asdict(s) for s in summaries], "runs": [asdict(r) for r in results]}, indent=1))

    def f(x, pct=False):
        return "n/a (not measured)" if x is None else (f"{x:.1%}" if pct else f"{x:.3f}")
    lines = [f"# Benchmark run {stamp}", "",
             f"Benchmark v{version}; {len(tasks)} tasks x {repeats} repeat(s); model: {model}.", ""]
    if harness_selftest:
        lines += [("> **HARNESS SELF-TEST: scripted provider/sandbox. These numbers validate the harness only "
                  "and say nothing about model quality.**"), ""]
    lines += ["A small benchmark does not establish universal performance.", "",
              ("| Config | runs | pass@1 | final success | regression (denominator) | median repairs | "
              "cost/task (known n) | median lat (s) | p95 lat (s) | provider err | sandbox fail | budget exh |"),
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(f"| {s.config} | {s.n_runs} | {f(s.pass_at_1, True)} | {f(s.final_success_rate, True)} | "
                     f"{f(s.regression_rate, True)} ({s.regression_denominator}) | {f(s.median_repair_attempts)} | "
                     f"{f(s.cost_per_task)} ({s.cost_known_runs}) | {f(s.median_latency)} | {f(s.p95_latency)} | "
                     f"{f(s.provider_error_rate, True)} | {f(s.sandbox_failure_rate, True)} | "
                     f"{f(s.budget_exhaustion_rate, True)} |")
    path = out_dir / f"run_{stamp}.md"
    path.write_text("\n".join(lines) + "\n")
    return path
