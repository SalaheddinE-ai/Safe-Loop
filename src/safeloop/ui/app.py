"""Streamlit operational UI. Talks to the API over HTTP; shows only real data (no fake metrics)."""
from __future__ import annotations

import difflib
import os

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_URL = os.environ.get("SAFELOOP_API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("SAFELOOP_API_TOKEN", "")
PROM_URL = os.environ.get("SAFELOOP_PROMETHEUS_URL", "http://localhost:9090")
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


def api(method: str, path: str, **kw):
    """Return (ok, data_or_error). Never raises: the UI must show failures clearly."""
    try:
        r = httpx.request(method, f"{API_URL}/api/v1{path}", headers=HEADERS, timeout=10, **kw)
    except httpx.HTTPError as exc:
        return False, f"API unreachable at {API_URL} ({type(exc).__name__})"
    try:
        body = r.json()
    except ValueError:
        return False, f"unexpected response (HTTP {r.status_code})"
    if r.status_code >= 400:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        return False, f"HTTP {r.status_code}: {err.get('message', 'error')} {err.get('details') or ''}"
    return True, body


def need(ok_data):
    ok, data = ok_data
    if not ok:
        st.error(data)
        return None
    return data


def page_control_center():
    st.header("Control Center")
    stats = need(api("GET", "/stats"))
    if stats is None:
        return
    total = stats["total_tasks"]
    by = stats["by_status"]
    done = sum(v for k, v in by.items() if k not in ("PENDING", "RUNNING"))
    c = st.columns(4)
    c[0].metric("Total tasks", total)
    c[1].metric("Success rate", f"{by.get('SUCCESS', 0) / done:.0%}" if done else "n/a")
    c[2].metric("Avg duration (s)", f"{stats['avg_duration_seconds']:.1f}" if stats["avg_duration_seconds"] else "n/a")
    c[3].metric("Active", by.get("RUNNING", 0) + by.get("PENDING", 0))
    st.caption(f"Cost where reported/estimated by providers: ${stats['total_cost_usd_known']:.4f}. "
               "Calls with unavailable cost contribute nothing; no price is assumed.")
    if by:
        st.plotly_chart(px.bar(x=list(by), y=list(by.values()), labels={"x": "status", "y": "tasks"}),
                        use_container_width=True)
    data = need(api("GET", "/tasks", params={"limit": 10}))
    if data:
        st.subheader("Recent executions")
        st.dataframe(pd.DataFrame(data["items"]), use_container_width=True)


def page_create():
    st.header("Create Task")
    with st.form("create"):
        req = st.text_area("Task description", height=120)
        tests = st.text_area("Acceptance tests (pytest, import from `solution`)", height=180,
                             value="from solution import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
        reqs = st.text_area("Requirements (one per line)")
        c = st.columns(4)
        model = c[0].text_input("Model/provider key (blank = default)")
        timeout = c[1].number_input("Timeout (s)", 5.0, 3600.0, 120.0)
        attempts = c[2].number_input("Max attempts", 1, 10, 3)
        budget = c[3].number_input("Budget (USD)", 0.0, 100.0, 1.0)
        submitted = st.form_submit_button("Submit")
    if submitted:
        body = {"user_request": req, "test_strategy": tests, "timeout_seconds": timeout,
                "maximum_attempts": int(attempts), "maximum_cost": budget,
                "requirements": [r for r in reqs.splitlines() if r.strip()],
                "model_preferences": [model] if model else []}
        data = need(api("POST", "/tasks", json=body))
        if data:
            st.session_state["task_id"] = data["task_id"]
            st.success(f"Task {data['task_id']} accepted. Open 'Workflow & Attempts'.")


def page_explorer():
    st.header("Task Explorer")
    c = st.columns(3)
    status = c[0].selectbox("Status", ["", "SUCCESS", "FAILED_VERIFICATION", "BUDGET_EXHAUSTED",
                                       "DEADLINE_EXCEEDED", "BLOCKED_BY_POLICY", "PROVIDER_ERROR",
                                       "SANDBOX_ERROR", "CANCELLED", "RUNNING", "PENDING"])
    model = c[1].text_input("Model (e.g. ollama/qwen2.5-coder:7b)")
    reason = c[2].text_input("Termination reason")
    params = {k: v for k, v in {"status": status, "model": model, "termination_reason": reason,
                                "limit": 50}.items() if v}
    data = need(api("GET", "/tasks", params=params))
    if data:
        st.caption(f"{data['total']} matching task(s)")
        st.dataframe(pd.DataFrame(data["items"]), use_container_width=True)
        ids = [t["task_id"] for t in data["items"]]
        if ids:
            st.session_state["task_id"] = st.selectbox("Open task", ids)


def _task_id() -> str:
    tid = st.text_input("Task ID", value=st.session_state.get("task_id", ""))
    st.session_state["task_id"] = tid
    return tid


@st.fragment(run_every=3)
def _attempts_live(tid: str):
    t = need(api("GET", f"/tasks/{tid}"))
    if not t:
        return
    st.write(f"**Status:** {t['status']}  |  **Termination:** {t['termination_reason'] or '-'}  |  "
             f"**Model:** {t['model']}")
    rows = need(api("GET", f"/tasks/{tid}/attempts"))
    if rows:
        flat = [{**i["attempt"], **({"failure": i["verification"]["failure_category"]} if i["verification"] else {})}
                for i in rows["items"]]
        if flat:
            st.dataframe(pd.DataFrame(flat), use_container_width=True)
    r = api("GET", f"/tasks/{tid}/report")
    if r[0]:
        st.json(r[1])


def page_workflow():
    st.header("Workflow & Attempts")
    tid = _task_id()
    if tid:
        st.caption("Stages: generate → validate → sandbox tests → evaluate → repair or finalize "
                   "(auto-refreshes every 3 s)")
        _attempts_live(tid)


def page_code():
    st.header("Code & Verification")
    tid = _task_id()
    if not tid:
        return
    rows = need(api("GET", f"/tasks/{tid}/attempts", params={"include_code": "true"}))
    if not rows or not rows["items"]:
        st.info("No attempts recorded yet.")
        return
    items = rows["items"]
    n = st.selectbox("Attempt", range(1, len(items) + 1))
    it = items[n - 1]
    code = (it.get("artifact") or {}).get("source_code", "")
    st.code(code or "(no artifact for this attempt)", language="python")
    v = it.get("verification")
    if v:
        st.write(f"syntax_valid={v['syntax_valid']} · static_checks_passed={v['static_checks_passed']} · "
                 f"passed={v['tests_passed']} · failed={v['tests_failed']} · category={v['failure_category']}")
        if v["test_details"]:
            st.dataframe(pd.DataFrame(v["test_details"]), use_container_width=True)
        if v["sanitized_diagnostics"]:
            st.text_area("Sanitized diagnostics", v["sanitized_diagnostics"], height=120)
    if n > 1:
        prev = (items[n - 2].get("artifact") or {}).get("source_code", "")
        diff = "\n".join(difflib.unified_diff(prev.splitlines(), code.splitlines(),
                                              f"attempt {n - 1}", f"attempt {n}", lineterm=""))
        st.subheader("Diff vs previous attempt")
        st.code(diff or "(identical)", language="diff")
    if code:
        st.download_button("Export this artifact", code, file_name=f"solution_attempt{n}.py")


def page_analytics():
    st.header("Model & Cost Analytics")
    stats = need(api("GET", "/stats"))
    if not stats:
        return
    models = stats["models"]
    if not models:
        st.info("Not yet measured: no model calls recorded.")
        return
    df = pd.DataFrame([{"model": k, "calls": v["calls"], "input_tokens": v["input_tokens"],
                        "output_tokens": v["output_tokens"], "cost_usd_known": v["cost_usd"],
                        "calls_with_cost": v["cost_known_calls"],
                        "median_latency_s": float(pd.Series(v["latencies"]).median())}
                       for k, v in models.items()])
    st.dataframe(df, use_container_width=True)
    lat = pd.DataFrame([{"model": k, "latency_s": x} for k, v in models.items() for x in v["latencies"]])
    st.plotly_chart(px.box(lat, x="model", y="latency_s"), use_container_width=True)
    ok = stats["by_status"].get("SUCCESS", 0)
    st.metric("Cost per successful task (known cost only)",
              f"${stats['total_cost_usd_known'] / ok:.4f}" if ok and stats["total_cost_usd_known"] else "n/a")


def page_reliability():
    st.header("Reliability & Guardrails")
    stats = need(api("GET", "/stats"))
    if not stats:
        return
    by, rs = stats["by_status"], stats["termination_reasons"]
    c = st.columns(5)
    for col, (label, val) in zip(c, [("Budget exhausted", by.get("BUDGET_EXHAUSTED", 0)),
                                     ("Deadline exceeded", by.get("DEADLINE_EXCEEDED", 0)),
                                     ("Provider errors", by.get("PROVIDER_ERROR", 0)),
                                     ("Sandbox errors", by.get("SANDBOX_ERROR", 0)),
                                     ("Stagnation stops", rs.get("STAGNATION", 0))], strict=True):
        col.metric(label, val)
    if rs:
        st.plotly_chart(px.pie(names=list(rs), values=list(rs.values()), title="Termination reasons"),
                        use_container_width=True)


def page_security():
    st.header("Security & Sandbox")
    st.markdown("""
**Execution policy:** generated code never runs in the API process; sandbox failures fail closed.
**Requested container limits:** non-root, no network, all capabilities dropped, no-new-privileges,
read-only rootfs, bounded tmpfs, CPU/memory/PID limits, wall-clock timeout, bounded output.
**Residual risk:** containers share the host kernel; static checks are not a security boundary.
**Security test summary:** not shown here: run `pytest tests/security`. Real-Docker isolation tests: not yet measured.
""")
    stats = need(api("GET", "/stats"))
    if stats:
        st.write("Policy blocks:", stats["by_status"].get("BLOCKED_BY_POLICY", 0),
                 "· Sandbox errors:", stats["by_status"].get("SANDBOX_ERROR", 0))


def page_health():
    st.header("System Health")
    ok, data = api("GET", "/health")
    st.write("API:", "reachable" if ok or isinstance(data, dict) else "unreachable")
    if ok:
        st.json(data)
    else:
        st.error(data)
    try:
        r = httpx.get(f"{PROM_URL}/-/healthy", timeout=3)
        st.write("Prometheus:", "healthy" if r.status_code == 200 else f"HTTP {r.status_code}")
    except httpx.HTTPError:
        st.warning(f"Prometheus not reachable at {PROM_URL}")


PAGES = {"Control Center": page_control_center, "Create Task": page_create,
         "Task Explorer": page_explorer, "Workflow & Attempts": page_workflow,
         "Code & Verification": page_code, "Model & Cost Analytics": page_analytics,
         "Reliability & Guardrails": page_reliability, "Security & Sandbox": page_security,
         "System Health": page_health}


def main():
    st.set_page_config(page_title="Safe-Loop", layout="wide")
    st.sidebar.title("Safe-Loop")
    st.sidebar.caption(f"API: {API_URL}")
    PAGES[st.sidebar.radio("Page", list(PAGES))]()


main()
