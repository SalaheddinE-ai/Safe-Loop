"""Optional LiteLLM adapter. Costs come from LiteLLM's local price table, so they are labelled
ESTIMATED (never REPORTED). Requires `pip install litellm`. Not exercised against real APIs."""
from __future__ import annotations

from ...domain.enums import CostSource
from ...domain.exceptions import ProviderError
from ...domain.models import ModelUsage
from .base import ProviderResponse

_TRANSIENT = {"Timeout", "APIConnectionError", "RateLimitError", "ServiceUnavailableError",
              "InternalServerError", "APITimeoutError"}


class LiteLLMProvider:
    name = "litellm"

    def __init__(self, model: str, max_output_tokens: int = 2048, price_per_1k: tuple[float, float] | None = None):
        self.model, self.max_output_tokens, self.price_per_1k = model, max_output_tokens, price_per_1k

    def _call(self, prompt: str, timeout: float) -> ProviderResponse:
        try:
            import litellm
        except ImportError as exc:
            raise ProviderError("litellm is not installed", transient=False) from exc
        try:
            resp = litellm.completion(model=self.model, timeout=timeout, max_tokens=self.max_output_tokens,
                                      messages=[{"role": "user", "content": prompt}], temperature=0)
        except Exception as exc:
            raise ProviderError(f"litellm error: {type(exc).__name__}",
                                transient=type(exc).__name__ in _TRANSIENT) from exc
        try:
            text = resp.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            raise ProviderError("litellm returned a malformed response") from exc
        u = getattr(resp, "usage", None)
        inp, out = getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)
        cost, src = None, CostSource.UNAVAILABLE
        if self.price_per_1k and inp is not None and out is not None:
            cost = inp / 1000 * self.price_per_1k[0] + out / 1000 * self.price_per_1k[1]
            src = CostSource.ESTIMATED  # configured pricing, not provider-reported
        return ProviderResponse(text, ModelUsage(provider=self.name, model=self.model, input_tokens=inp,
                                                 output_tokens=out, cost_usd=cost, cost_source=src))

    def generate_code(self, prompt, *, timeout):
        return self._call(prompt, timeout)

    def repair_code(self, prompt, *, timeout):
        return self._call(prompt, timeout)

    def get_model_metadata(self):
        return {"provider": self.name, "model": self.model}

    def estimate_cost(self, prompt):
        if not self.price_per_1k:
            return None
        approx_in = max(1, len(prompt) // 4)  # rough heuristic; labelled ESTIMATED downstream
        return approx_in / 1000 * self.price_per_1k[0] + self.max_output_tokens / 1000 * self.price_per_1k[1]
