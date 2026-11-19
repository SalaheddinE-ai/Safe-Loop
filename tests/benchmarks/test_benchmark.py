import json
import subprocess
import sys
import tempfile
from pathlib import Path

from safeloop.agents.providers.fake import FakeProvider
from safeloop.benchmarks.metrics import RunResult, summarize
from safeloop.benchmarks.runner import load_tasks, run_benchmark, write_report
from tests.helpers import ScriptedSandbox, fail_run, ok_run

DS = Path(__file__).resolve().parents[2] / "src/safeloop/benchmarks/datasets"


def test_dataset_is_valid_and_tests_pass_against_reference_solutions():
    """Runs repo-owned, trusted reference fixtures (NOT model output) to validate the tests."""
    version, tasks = load_tasks()
    refs = json.loads((DS / "reference_solutions_v1.json").read_text())
    assert version == "1.0.0" and len(tasks) == len({t["id"] for t in tasks}) == len(refs)
    for t in tasks:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "solution.py").write_text(refs[t["id"]])
            (Path(d) / "test_solution.py").write_text(t["tests"])
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", d],
                               capture_output=True, text=True, cwd=d)
            assert r.returncode == 0, (t["id"], r.stdout[-500:])


def test_metrics_math_and_missing_data():
    mk = lambda **kw: RunResult(**{"task_id": "t", "config": "C", "repeat": 0, "status": "SUCCESS", "attempts": 1,
                                   "first_attempt_success": True, "regression": False, "executed_attempts": 1,
                                   "duration": 1.0, "cost_usd": None, **kw})
    runs = [mk(), mk(status="FAILED_VERIFICATION", attempts=3, first_attempt_success=False, executed_attempts=3,
                     regression=True, duration=3.0),
            mk(status="PROVIDER_ERROR", attempts=1, first_attempt_success=False, executed_attempts=0)]
    s = summarize("C", runs)
    assert s.n_runs == 3 and abs(s.pass_at_1 - 1 / 3) < 1e-9 and abs(s.final_success_rate - 1 / 3) < 1e-9
    assert s.regression_denominator == 1 and s.regression_rate == 1.0
    assert s.cost_per_task is None and s.cost_known_runs == 0 and s.cost_per_successful_task is None
    assert s.failure_breakdown == {"functional": 1, "provider": 1} and abs(s.provider_error_rate - 1 / 3) < 1e-9
    assert summarize("A", []).pass_at_1 is None


def test_harness_selftest_compares_configs(tmp_path):
    """Scripted provider fails first, succeeds second for every task: A/B must fail, C must pass."""
    _, tasks = load_tasks()
    tasks = tasks[:3]
    bad, good = "def f():\n    return 0  # BAD\n", "def f():\n    return 1  # GOOD\n"
    sb = ScriptedSandbox({"# GOOD": ok_run(), "# BAD": fail_run()})
    res = run_benchmark(lambda t, c, r: FakeProvider([bad, good]), sb, tasks=tasks, repeats=2)
    by = {k: summarize(k, [r for r in res if r.config == k]) for k in "ABC"}
    assert by["A"].final_success_rate == by["B"].final_success_rate == 0.0
    assert by["C"].final_success_rate == 1.0 and by["C"].pass_at_1 == 0.0
    assert by["C"].n_runs == 6 and by["C"].median_repair_attempts == 1
    assert by["C"].regression_rate == 0.0 and by["C"].cost_per_task is None  # fake never reports cost
    md = write_report(res, tmp_path, model="fake", repeats=2, harness_selftest=True).read_text()
    assert "HARNESS SELF-TEST" in md and "n/a (not measured)" in md
