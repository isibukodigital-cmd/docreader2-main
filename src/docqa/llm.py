"""OpenAI-compatible chat backends with randomized starting provider and fallback.

PRIVACY: using these backends sends the user's question and the retrieved passages
(only ones the user may read) to a third-party provider. Disclose this to customers.
OpenRouter requests ask providers not to retain/train on prompts (data_collection=deny).
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

import requests

from .index import Hit

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"
DEFAULT_CEREBRAS_MODEL: str | None = None
DEFAULT_NVIDIA_NIM_MODEL = "meta/llama-3.3-70b-instruct"

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMUnavailableError(RuntimeError):
    """Every configured model provider failed; the caller should return 503."""


class ProviderError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class OpenAICompatBackend:
    """POST {base_url}/chat/completions to an OpenAI-compatible provider."""

    def __init__(self, base_url: str, api_key: str, model: str, name: str = "openai-compat",
                 timeout: float = 30, max_tokens: int = 400, retries: int = 1,
                 backoff: float = 0.5, extra_body: dict | None = None,
                 extra_headers: dict | None = None, session=None):
        if not base_url or not model:
            raise ValueError("base_url and model are required")
        if not api_key:
            raise ValueError(f"API key for {name} is required")
        self.base_url = base_url.rstrip("/")
        self.model, self.name = model, name
        self.timeout, self.max_tokens = timeout, max_tokens
        self.retries, self.backoff = max(0, retries), backoff
        self.extra_body = dict(extra_body or {})
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                        **(extra_headers or {})}

    def _payload(self, messages: list[dict]) -> dict:
        # Only fields every OpenAI-compatible server accepts (Groq rejects logprobs,
        # logit_bias, n != 1, messages[].name). Near-zero temperature = deterministic.
        body = {"model": self.model, "messages": messages,
                "temperature": 0.0, "max_tokens": self.max_tokens, "stream": False}
        body.update(self.extra_body)
        return body

    def _once(self, messages: list[dict]) -> str:
        try:
            r = self.session.post(f"{self.base_url}/chat/completions", headers=self.headers,
                                  json=self._payload(messages), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"{self.name}: network error ({type(exc).__name__})") from exc
        if r.status_code != 200:
            # Never include the response body/key in the message: it may echo prompt text.
            raise ProviderError(f"{self.name}: HTTP {r.status_code}",
                                retryable=r.status_code in RETRYABLE_STATUS)
        try:
            text = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.name}: malformed response") from exc
        if not isinstance(text, str) or not text.strip():
            raise ProviderError(f"{self.name}: empty completion")
        return text.strip()

    def generate(self, question: str, hits: list[Hit], messages: list[dict]) -> str:
        last: ProviderError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._once(messages)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt >= self.retries:
                    break
                time.sleep(self.backoff * (2 ** attempt))
        assert last is not None
        raise last


@dataclass
class FallbackBackend:
    """Try each backend once, optionally choosing a random starting provider."""
    backends: list = field(default_factory=list)
    last_errors: list[str] = field(default_factory=list)
    randomize_order: bool = False

    def generate(self, question: str, hits: list[Hit], messages: list[dict]) -> str:
        errors: list[str] = []
        providers = list(self.backends)
        if self.randomize_order and len(providers) > 1:
            start = random.randrange(len(providers))
            providers = providers[start:start + 1] + providers[:start] + providers[start + 1:]
        for backend in providers:
            try:
                return backend.generate(question, hits, messages)
            except ProviderError as exc:
                errors.append(str(exc))
        self.last_errors = errors
        raise LLMUnavailableError("; ".join(errors) or "no model providers configured")


def build_llm_backend(env: dict | None = None):
    """Build a randomized starting chain from the configured providers."""
    e = env if env is not None else os.environ
    chain = []
    if e.get("GROQ_API_KEY"):
        chain.append(OpenAICompatBackend(
            e.get("GROQ_BASE_URL", GROQ_BASE_URL), e["GROQ_API_KEY"],
            e.get("GROQ_MODEL", DEFAULT_GROQ_MODEL), name="groq"))
    if e.get("OPENROUTER_API_KEY"):
        chain.append(OpenAICompatBackend(
            e.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL), e["OPENROUTER_API_KEY"],
            e.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL), name="openrouter",
            # Ask OpenRouter to route only to providers that don't store/train on prompts.
            extra_body={"provider": {"data_collection": "deny", "allow_fallbacks": True}},
            extra_headers={"X-Title": e.get("DOCQA_APP_TITLE", "Doc Reader")}))
    if e.get("CEREBRAS_API_KEY"):
        cerebras_model = e.get("CEREBRAS_MODEL")
        if not cerebras_model:
            raise ValueError(
                "CEREBRAS_API_KEY requires CEREBRAS_MODEL; "
                "no unverified Cerebras model default is used"
            )
        chain.append(OpenAICompatBackend(
            e.get("CEREBRAS_BASE_URL", CEREBRAS_BASE_URL), e["CEREBRAS_API_KEY"],
            cerebras_model, name="cerebras"))
    if e.get("NVIDIA_NIM_API_KEY"):
        chain.append(OpenAICompatBackend(
            e.get("NVIDIA_NIM_BASE_URL", NVIDIA_NIM_BASE_URL), e["NVIDIA_NIM_API_KEY"],
            e.get("NVIDIA_NIM_MODEL", DEFAULT_NVIDIA_NIM_MODEL), name="nvidia-nim"))
    if not chain:
        raise ValueError(
            "DOCQA_BACKEND=llm requires GROQ_API_KEY, OPENROUTER_API_KEY, "
            "CEREBRAS_API_KEY, or NVIDIA_NIM_API_KEY"
        )
    return FallbackBackend(chain, randomize_order=True)
