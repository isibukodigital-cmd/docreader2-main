import pytest
import requests

from docqa.llm import (FallbackBackend, LLMUnavailableError, OpenAICompatBackend,
                       build_llm_backend)
from docqa.answer import OllamaBackend, OllamaUnavailableError


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def ok(text):
    return Resp(200, {"choices": [{"message": {"content": text}}]})


class Session:
    def __init__(self, *seq, order=None):
        self.seq, self.calls = list(seq), []
        self.order = order

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.order is not None:
            self.order.append(url)
        item = self.seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def backend(session, **kw):
    return OpenAICompatBackend("https://x.test/v1/", "key", "m", name="t", session=session,
                               backoff=0, **kw)


def test_success_sends_expected_request():
    s = Session(ok("  hi [1] "))
    assert backend(s).generate("q", [], [{"role": "user", "content": "q"}]) == "hi [1]"
    url, kw = s.calls[0]
    assert url == "https://x.test/v1/chat/completions"
    assert kw["headers"]["Authorization"] == "Bearer key"
    assert kw["json"]["temperature"] == 0.0 and kw["json"]["stream"] is False
    assert kw["timeout"] == 30
    assert "logprobs" not in kw["json"] and "n" not in kw["json"]


def test_retries_transient_then_succeeds():
    s = Session(Resp(429), ok("fine"))
    assert backend(s, retries=1).generate("q", [], []) == "fine"
    assert len(s.calls) == 2


def test_does_not_retry_client_errors():
    s = Session(Resp(401))
    with pytest.raises(Exception) as e:
        backend(s, retries=2).generate("q", [], [])
    assert len(s.calls) == 1 and "401" in str(e.value)


def test_network_error_and_malformed_and_empty_are_provider_errors():
    for item in (requests.ConnectionError("boom"), Resp(200, {"nope": 1}), ok("  ")):
        with pytest.raises(Exception):
            backend(Session(item), retries=0).generate("q", [], [])


def test_error_message_never_contains_body_or_key():
    s = Session(Resp(500, {"error": "echo of secret prompt key"}))
    with pytest.raises(Exception) as e:
        backend(s, retries=0).generate("q", [], [])
    assert "secret" not in str(e.value) and "key" not in str(e.value).replace("HTTP", "")


def test_fallback_uses_second_backend_and_raises_when_all_fail():
    a, b = backend(Session(Resp(503)), retries=0), backend(Session(ok("from b")), retries=0)
    assert FallbackBackend([a, b]).generate("q", [], []) == "from b"
    c, d = backend(Session(Resp(503)), retries=0), backend(Session(Resp(500)), retries=0)
    with pytest.raises(LLMUnavailableError):
        FallbackBackend([c, d]).generate("q", [], [])


def test_build_chain_from_env_order_and_openrouter_privacy():
    chain = build_llm_backend({
        "GROQ_API_KEY": "g",
        "OPENROUTER_API_KEY": "o",
        "CEREBRAS_API_KEY": "c",
        "CEREBRAS_MODEL": "verified-cerebras-model",
        "NVIDIA_NIM_API_KEY": "n",
    })
    assert [b.name for b in chain.backends] == ["groq", "openrouter", "cerebras", "nvidia-nim"]
    assert chain.randomize_order is True
    groq, openrouter, cerebras, nvidia_nim = chain.backends
    assert groq.base_url.startswith("https://api.groq.com")
    assert openrouter.extra_body["provider"]["data_collection"] == "deny"
    assert cerebras.base_url == "https://api.cerebras.ai/v1"
    assert cerebras.model == "verified-cerebras-model"
    assert nvidia_nim.base_url == "https://integrate.api.nvidia.com/v1"
    assert nvidia_nim.model == "meta/llama-3.3-70b-instruct"
    assert [b.name for b in build_llm_backend({"OPENROUTER_API_KEY": "o"}).backends] == ["openrouter"]
    assert [b.name for b in build_llm_backend({
        "CEREBRAS_API_KEY": "c",
        "CEREBRAS_MODEL": "verified-cerebras-model",
    }).backends] == ["cerebras"]
    assert [b.name for b in build_llm_backend({"NVIDIA_NIM_API_KEY": "n"}).backends] == ["nvidia-nim"]


def test_fallback_reaches_cerebras_after_groq_and_openrouter_fail(monkeypatch):
    monkeypatch.setattr("docqa.llm.random.randrange", lambda size: 0)
    chain = build_llm_backend({
        "GROQ_API_KEY": "g",
        "OPENROUTER_API_KEY": "o",
        "CEREBRAS_API_KEY": "c",
        "CEREBRAS_MODEL": "verified-cerebras-model",
    })
    sessions = [Session(Resp(404)), Session(Resp(404)), Session(ok("from cerebras"))]
    for provider, session in zip(chain.backends, sessions):
        provider.session = session
        provider.retries = 0

    assert chain.generate("q", [], []) == "from cerebras"
    assert all(len(session.calls) == 1 for session in sessions)


def test_randomized_start_fails_over_through_remaining_providers(monkeypatch):
    monkeypatch.setattr("docqa.llm.random.randrange", lambda size: size - 1)
    chain = build_llm_backend({
        "GROQ_API_KEY": "g",
        "OPENROUTER_API_KEY": "o",
        "CEREBRAS_API_KEY": "c",
        "CEREBRAS_MODEL": "verified-cerebras-model",
        "NVIDIA_NIM_API_KEY": "n",
    })
    attempted_urls = []
    sessions = [
        Session(Resp(404), order=attempted_urls),
        Session(Resp(404), order=attempted_urls),
        Session(ok("from cerebras"), order=attempted_urls),
        Session(Resp(404), order=attempted_urls),
    ]
    for provider, session in zip(chain.backends, sessions):
        provider.session = session
        provider.retries = 0

    assert chain.generate("q", [], []) == "from cerebras"
    assert attempted_urls == [
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
        "https://api.cerebras.ai/v1/chat/completions",
    ]


def test_build_requires_a_key():
    with pytest.raises(ValueError):
        build_llm_backend({})


def test_build_cerebras_requires_explicit_model():
    with pytest.raises(ValueError, match="CEREBRAS_MODEL"):
        build_llm_backend({"CEREBRAS_API_KEY": "c"})


def test_ollama_network_failure_is_a_typed_outage(monkeypatch):
    def fail_post(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "post", fail_post)
    backend = OllamaBackend(host="http://127.0.0.1:11434")
    with pytest.raises(OllamaUnavailableError, match="Ollama"):
        backend.generate("q", [], [])
