"""Bounded LangGraph workflow.

generate -> validate -> execute -> evaluate -> (generate | finalize)

The loop is bounded three ways: attempt budget (checked in evaluate_stop), deadline, and a
LangGraph recursion limit derived from max attempts.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from langgraph.graph import END, StateGraph

from ..agents import prompt_templates as pt
from ..agents.providers.base import CodeProvider
from ..core.budgets import Budget
from ..core.config import Settings
from ..core.deadlines import Deadline
from ..core.fingerprints import structural_fingerprint
from ..core.policies import evaluate_stop
from ..domain.enums import CostSource, TaskStatus, TerminationReason
from ..domain.exceptions import ProviderError
from ..domain.models import AttemptRecord, CodeArtifact, FinalReport, TaskSpec, VerificationResult
from ..sandbox.base import Sandbox
from ..verification.ast_validator import extract_code
from ..verification.static_checks import StaticPolicy
from ..verification.test_runner import execute_and_verify
from ..verification.verification_policy import validate_artifact
from .state import SafeLoopState

LIMITATIONS = [
    "Passing acceptance tests is evidence, not proof of correctness.",
    "AST/static checks are a limited structural filter, not a security boundary.",
    "Container isolation shares the host kernel; see SECURITY.md for residual risks.",
]


@dataclass
class Deps:
    provider: CodeProvider
    sandbox: Sandbox
    settings: Settings = field(default_factory=Settings)
    static_policy: StaticPolicy = field(default_factory=StaticPolicy)
    is_cancelled: Callable[[], bool] = lambda: False
    on_attempt: Callable[[TaskSpec, AttemptRecord, CodeArtifact | None, VerificationResult | None], None] = (
        lambda *a: None
    )
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def _call_provider(deps: Deps, kind: str, prompt: str, timeout: float):
    """Bounded retries with backoff for transient errors only."""
    attempts = deps.settings.provider_max_retries + 1
    for i in range(attempts):
        try:
            fn = deps.provider.generate_code if kind == "generate" else deps.provider.repair_code
            return fn(prompt, timeout=timeout)
        except ProviderError as exc:
            if not exc.transient or i == attempts - 1:
                raise
            deps.sleep(deps.settings.provider_backoff_seconds * (2**i))
    raise AssertionError("unreachable")


def build_graph(deps: Deps):
    def init(state: SafeLoopState) -> SafeLoopState:
        t = state["task"]
        return {
            "budget": Budget(t.maximum_attempts, t.maximum_cost),
            "deadline": Deadline(t.timeout_seconds, deps.clock),
            "attempts": [], "artifacts": [], "usages": [], "artifact": None, "verification": None,
            "pending_error": "", "done": False, "status": TaskStatus.RUNNING, "reason": None,
            "policy": "", "meta": {"prompt_version": pt.PROMPT_VERSION, **deps.provider.get_model_metadata()},
        }

    def _finish(status: TaskStatus, reason: TerminationReason, policy: str) -> SafeLoopState:
        return {"done": True, "status": status, "reason": reason, "policy": policy}

    def generate(state: SafeLoopState) -> SafeLoopState:
        t, budget, deadline = state["task"], state["budget"], state["deadline"]
        prev = state.get("artifact")
        ver = state.get("verification")
        if prev is None:
            kind = "generate"
            prompt = pt.generation_prompt(t.user_request, t.requirements, t.constraints, t.test_strategy)
        else:
            kind = "repair"
            failed = [d.node_id for d in (ver.test_details if ver else []) if d.outcome != "passed"]
            diag = (ver.sanitized_diagnostics if ver else "") or state.get("pending_error", "")
            prompt = pt.repair_prompt(t.user_request, prev.source_code, failed, diag, budget.attempts_remaining)
        # Hard checks BEFORE any model call.
        if state["task"].status == TaskStatus.CANCELLED or deps.is_cancelled():
            return _finish(TaskStatus.CANCELLED, TerminationReason.USER_CANCELLED, "cancel")
        if deadline.expired:
            return _finish(TaskStatus.DEADLINE_EXCEEDED, TerminationReason.DEADLINE, "deadline")
        if not budget.can_afford(deps.provider.estimate_cost(prompt)):
            return _finish(TaskStatus.BUDGET_EXHAUSTED, TerminationReason.COST_LIMIT, "cost")
        budget.record_attempt()
        n = budget.attempts_used
        timeout = min(deps.settings.model_call_timeout_seconds, max(0.1, deadline.remaining))
        started = deps.clock()
        try:
            resp = _call_provider(deps, kind, prompt, timeout)
        except ProviderError:
            return _finish(TaskStatus.PROVIDER_ERROR, TerminationReason.PROVIDER_FAILURE, "provider")
        resp.usage.latency_seconds = deps.clock() - started
        cost = resp.usage.cost_usd
        if cost is None:
            cost = deps.provider.estimate_cost(prompt)
            if cost is not None:
                resp.usage.cost_usd, resp.usage.cost_source = cost, CostSource.ESTIMATED
        budget.record_cost(cost, resp.usage.cost_source)
        usages = [*state["usages"], resp.usage]
        ex = extract_code(resp.text, deps.settings.max_artifact_bytes)
        if ex.source is None:
            ver = VerificationResult(
                task_id=t.task_id, artifact_id="none", failure_category=ex.failure,
                sanitized_diagnostics=ex.detail,
            )
            return {"usages": usages, "artifact": prev, "verification": ver, "last_prompt": prompt,
                    "pending_error": ex.detail, "meta": {**state["meta"], f"attempt{n}_started": str(started)}}
        art = CodeArtifact(
            task_id=t.task_id, attempt_number=n, source_code=ex.source, content_hash=ex.content_hash,
            provider_metadata=resp.usage.model_dump(include={"provider", "model"}),
        )
        return {"usages": usages, "artifact": art, "artifacts": [*state["artifacts"], art],
                "verification": None, "last_prompt": prompt, "pending_error": ""}

    def validate(state: SafeLoopState) -> SafeLoopState:
        if state.get("verification") is not None:  # malformed output already recorded
            return {}
        art = state["artifact"]
        assert art is not None
        return {"verification": validate_artifact(art, deps.static_policy)}

    def execute(state: SafeLoopState) -> SafeLoopState:
        ver, art = state["verification"], state["artifact"]
        assert ver is not None
        if not (ver.syntax_valid and ver.static_checks_passed):
            return {}  # malformed/invalid output: nothing to execute
        assert art is not None
        return {"verification": execute_and_verify(deps.sandbox, art, state["task"].test_strategy, ver)}

    def evaluate(state: SafeLoopState) -> SafeLoopState:
        t, budget, ver = state["task"], state["budget"], state["verification"]
        art = state.get("artifact")
        arts = state["artifacts"]
        stagnated = False
        if art is not None and len(arts) > 1 and ver is not None and ver.artifact_id == art.artifact_id:
            earlier = arts[:-1]
            stagnated = any(a.content_hash == art.content_hash for a in earlier) or (
                structural_fingerprint(art.source_code) is not None
                and structural_fingerprint(art.source_code) == structural_fingerprint(earlier[-1].source_code)
            )
        decision = evaluate_stop(
            verification=ver, budget=budget, deadline=state["deadline"], stagnated=stagnated,
            cancelled=deps.is_cancelled(),
            next_call_cost=deps.provider.estimate_cost(state.get("last_prompt", "")),
        )
        outcome = decision.status.value if decision.stop and decision.status else "REPAIR"
        rec = AttemptRecord(
            attempt_number=budget.attempts_used,
            artifact_id=art.artifact_id if art else None,
            verification_id=ver.verification_id if ver else None,
            repair_reason=("" if ver is None else ver.failure_category.value),
            cost_usd=state["usages"][-1].cost_usd or 0.0 if state["usages"] else 0.0,
            duration=ver.execution_duration if ver else 0.0,
            outcome=outcome,
        )
        deps.on_attempt(t, rec, art, ver)
        update: SafeLoopState = {"attempts": [*state["attempts"], rec]}
        if decision.stop:
            assert decision.status is not None and decision.reason is not None
            update.update(_finish(decision.status, decision.reason, decision.policy))
        return update

    def finalize(state: SafeLoopState) -> SafeLoopState:
        t, budget, ver = state["task"], state["budget"], state.get("verification")
        # A terminal state set by generate() before any verification still gets a report.
        status = state["status"]
        reason = state["reason"]
        assert reason is not None
        art = state.get("artifact")
        summary = "no verification performed"
        if ver is not None:
            summary = (
                f"syntax_valid={ver.syntax_valid} static_ok={ver.static_checks_passed} "
                f"passed={ver.tests_passed} failed={ver.tests_failed} "
                f"category={ver.failure_category.value}"
            )
        report = FinalReport(
            task_id=t.task_id, final_status=status,
            final_artifact_id=art.artifact_id if (art and status == TaskStatus.SUCCESS) else None,
            verification_summary=summary, attempt_count=budget.attempts_used,
            total_duration=state["deadline"].elapsed, total_cost_usd=budget.cost_usd,
            cost_sources=sorted(budget.sources), termination_reason=reason,
            triggered_policy=state["policy"], limitations=list(LIMITATIONS),
            reproducibility_metadata={k: str(v) for k, v in state["meta"].items() if not k.endswith("_started")},
        )
        return {"report": report}

    def after_generate(state: SafeLoopState) -> str:
        return "finalize" if state.get("done") else "validate"

    def after_evaluate(state: SafeLoopState) -> str:
        return "finalize" if state.get("done") else "generate"

    g = StateGraph(SafeLoopState)
    for name, fn in [("init", init), ("generate", generate), ("validate", validate),
                     ("execute", execute), ("evaluate", evaluate), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.set_entry_point("init")
    g.add_edge("init", "generate")
    g.add_conditional_edges("generate", after_generate, {"finalize": "finalize", "validate": "validate"})
    g.add_edge("validate", "execute")
    g.add_edge("execute", "evaluate")
    g.add_conditional_edges("evaluate", after_evaluate, {"finalize": "finalize", "generate": "generate"})
    g.add_edge("finalize", END)
    return g.compile()


def run_task(task: TaskSpec, deps: Deps) -> FinalReport:
    graph = build_graph(deps)
    limit = 6 * task.maximum_attempts + 10  # hard cap on graph steps
    final = graph.invoke({"task": task}, config={"recursion_limit": limit})
    return final["report"]
