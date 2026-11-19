from __future__ import annotations

import time
from pathlib import Path

import typer

from ..domain.models import TaskSpec

app = typer.Typer(help="Safe-Loop CLI", no_args_is_help=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the API (binds to localhost by default; there is no auth unless SAFELOOP_API_TOKEN is set)."""
    import uvicorn
    uvicorn.run("safeloop.api.app:app_factory", factory=True, host=host, port=port)


@app.command()
def run(request: str = typer.Option(..., help="Task description"),
        tests_file: Path = typer.Option(..., exists=True, help="pytest file importing `solution`"),
        model: str = typer.Option("", help="Provider key (e.g. ollama, litellm)"),
        attempts: int = 3, timeout: float = 120.0, budget: float = 1.0) -> None:
    """Submit a task and wait for its final report."""
    from ..application.bootstrap import build_manager
    mgr = build_manager()
    spec = TaskSpec(user_request=request, test_strategy=tests_file.read_text(), maximum_attempts=attempts,
                    timeout_seconds=timeout, maximum_cost=budget, model_preferences=[model] if model else [])
    tid = mgr.submit(spec)
    typer.echo(f"task {tid} submitted")
    while True:
        t = mgr.repo.get_task(tid)
        if t and t["status"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(0.5)
    r = mgr.repo.get_report(tid)
    typer.echo(r.model_dump_json(indent=2) if r else "no report")
    mgr.shutdown()


@app.command("list")
def list_cmd(limit: int = 20) -> None:
    from ..application.bootstrap import build_manager
    items, total = build_manager().repo.list_tasks(limit=limit)
    for t in items:
        typer.echo(f"{t['task_id']}  {t['status']:<20} {t['model']}")
    typer.echo(f"{total} task(s)")


@app.command()
def report(task_id: str) -> None:
    from ..application.bootstrap import build_manager
    r = build_manager().repo.get_report(task_id)
    typer.echo(r.model_dump_json(indent=2) if r else "not found or not finished")


@app.command()
def doctor() -> None:
    """Check the runtime: database and sandbox availability."""
    from ..application.bootstrap import build_manager
    typer.echo(build_manager().health())


@app.command()
def benchmark(provider: str = typer.Option("ollama", help="Provider key"), repeats: int = 3,
              max_attempts: int = 3, out: Path = Path("src/safeloop/benchmarks/reports")) -> None:
    """Run baselines A/B and Safe-Loop (C) with a REAL sandbox. Fails closed if Docker is unavailable."""
    from ..application.bootstrap import build_manager
    from ..benchmarks.runner import run_benchmark, write_report
    mgr = build_manager()
    ok, detail = mgr._sandbox_health()  # type: ignore[misc]
    if not ok:
        typer.echo(f"sandbox unavailable ({detail}); refusing to benchmark without isolation", err=True)
        raise typer.Exit(2)
    prov = mgr.router.providers[provider]
    path = write_report(run_benchmark(lambda *_: prov, mgr._sandbox_factory(), repeats=repeats,
                                      max_attempts=max_attempts), out,
                        model=str(prov.get_model_metadata()), repeats=repeats)
    typer.echo(f"report written: {path}")
