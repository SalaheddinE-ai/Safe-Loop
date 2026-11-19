import json
import logging

import httpx
import pytest

from safeloop.agents.model_router import ModelRouter
from safeloop.agents.providers.fake import FakeProvider
from safeloop.agents.providers.litellm_provider import LiteLLMProvider
from safeloop.agents.providers.ollama import OllamaProvider
from safeloop.domain.enums import CostSource
from safeloop.domain.exceptions import InvalidTaskError, ProviderError
from safeloop.domain.models import TaskSpec
from safeloop.observability.logging import JsonFormatter, redact


def _ollama(handler):
    return OllamaProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ollama_success_usage_and_no_fabricated_cost():
    p = _ollama(lambda r: httpx.Response(200, json={"response": "x=1", "prompt_eval_count": 7, "eval_count": 3}))
    r = p.generate_code("hi", timeout=5)
    assert r.text == "x=1" and (r.usage.input_tokens, r.usage.output_tokens) == (7, 3)
    assert r.usage.cost_source == CostSource.UNAVAILABLE and r.usage.cost_usd is None


@pytest.mark.parametrize("status,transient", [(500, True), (429, True), (404, False), (400, False)])
def test_ollama_error_classification(status, transient):
    p = _ollama(lambda r: httpx.Response(status))
    with pytest.raises(ProviderError) as e:
        p.generate_code("x", timeout=1)
    assert e.value.transient is transient


def test_ollama_transport_error_transient_and_malformed_permanent():
    def boom(r):
        raise httpx.ConnectError("down")
    with pytest.raises(ProviderError) as e:
        _ollama(boom).generate_code("x", timeout=1)
    assert e.value.transient
    with pytest.raises(ProviderError) as e2:
        _ollama(lambda r: httpx.Response(200, json={"nope": 1})).generate_code("x", timeout=1)
    assert not e2.value.transient


def test_litellm_with_injected_module(monkeypatch):
    import sys
    import types

    class U: prompt_tokens, completion_tokens = 1000, 500
    class Msg: content = "x=1"
    class Ch: message = Msg()
    class Resp: choices = [Ch()]; usage = U()
    mod = types.SimpleNamespace(completion=lambda **kw: Resp())
    monkeypatch.setitem(sys.modules, "litellm", mod)
    r = LiteLLMProvider("m", price_per_1k=(0.001, 0.002)).generate_code("p", timeout=1)
    assert r.usage.cost_source == CostSource.ESTIMATED and abs(r.usage.cost_usd - 0.002) < 1e-9
    r2 = LiteLLMProvider("m").generate_code("p", timeout=1)
    assert r2.usage.cost_usd is None and r2.usage.cost_source == CostSource.UNAVAILABLE


def test_router_preference_default_and_unknown():
    r = ModelRouter({"a": FakeProvider(["x"]), "b": FakeProvider(["y"])}, ["b"])
    t = lambda **kw: TaskSpec(user_request="implement add(a, b)", test_strategy="x", **kw)
    assert r.select(t()).key == "b"
    assert r.select(t(model_preferences=["a"])).key == "a"
    with pytest.raises(InvalidTaskError):
        r.select(t(model_preferences=["zzz"]))


def test_log_redaction_and_no_prompt_or_code():
    rec = logging.LogRecord("safeloop", logging.INFO, "", 0, "evt", None, None)
    rec.fields = {"api_key": "sk-abcdefghijklmnopqrstuv", "note": "Bearer abcdefghijkl1234",
                  "prompt": "SECRET PROMPT", "source_code": "print(1)", "task": "t1"}
    out = json.loads(JsonFormatter().format(rec))
    assert out["api_key"] == "[REDACTED]" and "abcdefghijkl1234" not in out["note"]
    assert "prompt" not in out and "source_code" not in out and out["task"] == "t1"
    assert {"timestamp", "severity", "service", "event"} <= out.keys()
    assert redact({"nested": {"password": "x"}})["nested"]["password"] == "[REDACTED]"
