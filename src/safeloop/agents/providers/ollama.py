"""Ollama adapter (local models). Cost is not billed per token locally, so it is reported as
UNAVAILABLE rather than invented. Not exercised against a live Ollama server in authoring."""
from __future__ import annotations

import httpx

from ...domain.enums import CostSource
from ...domain.exceptions import ProviderError
from ...domain.models import ModelUsage
from .base import ProviderResponse


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str = "qwen2.5-coder:7b", base_url: str = "http://localhost:11434",
                 client: httpx.Client | None = None, max_output_tokens: int = 2048):
        self.model, self.base_url = model, base_url.rstrip("/")
        self._client = client or httpx.Client()
        self.max_output_tokens = max_output_tokens

    def _call(self, prompt: str, timeout: float) -> ProviderResponse:
        try:
            r = self._client.post(
                f"{self.base_url}/api/generate", timeout=timeout,
                json={"model": self.model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0, "num_predict": self.max_output_tokens}})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"ollama unreachable: {type(exc).__name__}", transient=True) from exc
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"ollama http {r.status_code}", transient=True)
        if r.status_code >= 400:
            raise ProviderError(f"ollama http {r.status_code}", transient=False)
        try:
            data = r.json()
            text = data["response"]
        except (ValueError, KeyError) as exc:
            raise ProviderError("ollama returned a malformed response", transient=False) from exc
        usage = ModelUsage(provider=self.name, model=self.model,
                           input_tokens=data.get("prompt_eval_count"),
                           output_tokens=data.get("eval_count"),
                           cost_source=CostSource.UNAVAILABLE)
        return ProviderResponse(text=text, usage=usage)

    def generate_code(self, prompt, *, timeout):
        return self._call(prompt, timeout)

    def repair_code(self, prompt, *, timeout):
        return self._call(prompt, timeout)

    def get_model_metadata(self):
        return {"provider": self.name, "model": self.model}

    def estimate_cost(self, prompt):
        return None
