"""UI smoke tests via Streamlit AppTest against a LIVE in-process API (uvicorn thread)."""
import threading
import time
import os
from pathlib import Path

import pytest
import pandas
import plotly.express
import uvicorn
from streamlit.testing.v1 import AppTest

from safeloop.api.app import create_app

TIMEOUT = int(os.environ.get("SAFELOOP_UI_TEST_TIMEOUT", "90"))
UI = str(Path(__file__).resolve().parents[2] / "src/safeloop/ui/app.py")
GOOD = "```python\ndef add(a, b):\n    return a + b  # GOOD\n```"


def test_ui_shows_clear_error_when_api_down(monkeypatch):
    monkeypatch.setenv("SAFELOOP_API_URL", "http://127.0.0.1:9")
    at = AppTest.from_file(UI, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert any("unreachable" in e.value for e in at.error)


@pytest.fixture
def live_api(manager, monkeypatch):
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(create_app(manager, api_token=None, allow_dev_scripted=True),
                                           host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    monkeypatch.setenv("SAFELOOP_API_URL", f"http://127.0.0.1:{port}")
    yield port
    server.should_exit = True; th.join(5)


def test_ui_pages_render_real_data(live_api, manager):
    import httpx
    r = httpx.post(f"http://127.0.0.1:{live_api}/api/v1/tasks", json={
        "user_request": "implement add(a, b) returning sum", "test_strategy": "def test_a(): ...",
        "dev_scripted_responses": [GOOD]})
    tid = r.json()["task_id"]; manager.wait(tid)
    at = AppTest.from_file(UI, default_timeout=20).run()
    assert not at.exception and at.metric[0].value == "1"  # total tasks
    for page in ["Task Explorer", "Model & Cost Analytics", "Reliability & Guardrails",
                 "Security & Sandbox", "System Health", "Create Task"]:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
    at.sidebar.radio[0].set_value("Code & Verification").run()
    at.text_input[0].set_value(tid).run()
    assert not at.exception and any("GOOD" in c.value for c in at.code)
