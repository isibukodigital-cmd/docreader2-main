"""Citation-first answering with pluggable model backends.

Rules the pipeline enforces:
  * the model only sees chunks the user is allowed to read;
  * an answer with no valid citation is treated as unsupported and replaced
    with a plain "couldn't find this" message;
  * every query is written to an optional audit log.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .grounding import best_quote, ungrounded_figures, unsupported_claims
from .index import HybridIndex, Hit, tokenize
from .profile import DEFAULT_NOT_FOUND_TEXT, Profile
from .prompts import SYSTEM_PROMPT, build_messages, build_system_prompt  # noqa: F401  (re-exported)

NOT_FOUND_TEXT = DEFAULT_NOT_FOUND_TEXT


class OllamaUnavailableError(RuntimeError):
    """The configured local Ollama service could not provide a completion."""


# ----------------------------------------------------------------- backends
class ExtractiveBackend:
    """No-LLM baseline: returns the best-matching sentence. For smoke tests only."""

    STOP = {"the", "a", "an", "of", "for", "to", "in", "is", "are", "what", "who", "how",
            "does", "do", "on", "its", "by", "and", "or", "with", "be", "at", "this", "that",
            "pty", "ltd"}

    def __init__(self, threshold: float = 0.6):
        self.threshold = threshold

    def generate(self, question: str, hits: list[Hit], messages: list[dict]) -> str:
        q = {t for t in tokenize(question) if t not in self.STOP}
        best, best_score, best_n = None, 0.0, 0
        for n, h in enumerate(hits, 1):
            # Words that only restate the document title say nothing about the answer.
            wanted = q - set(tokenize(h.chunk.doc_title))
            if not wanted:
                continue
            for sent in re.split(r"(?<=[.!?])\s+|\n+", h.chunk.text):
                score = len(wanted & set(tokenize(sent))) / len(wanted)
                if score > best_score:
                    best, best_score, best_n = sent.strip(), score, n
        if best and best_score >= self.threshold:
            return f"{best} [{best_n}]"
        return "NOT_FOUND"


class HFBackend:
    """Hugging Face transformers model. Good default for Colab (free T4 GPU)."""

    def __init__(self, model_id: str = "Qwen/Qwen2.5-3B-Instruct",
                 load_in_4bit: bool = False, max_new_tokens: int = 200):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.max_new_tokens = max_new_tokens
        self.tok = AutoTokenizer.from_pretrained(model_id)
        kwargs: dict = {"device_map": "auto"}
        if load_in_4bit:
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
        else:
            kwargs["torch_dtype"] = torch.float16 if torch.cuda.is_available() else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)

    def generate(self, question: str, hits: list[Hit], messages: list[dict]) -> str:
        prompt = self.tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tok(prompt, return_tensors="pt").to(self.model.device)
        out = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens, do_sample=False)
        return self.tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()


class OllamaBackend:
    """Talks to a local Ollama server (laptop or VPS). No GPU required for small models."""

    def __init__(self, model: str = "qwen2.5:3b", host: str = "http://localhost:11434",
                 timeout: int = 300):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def generate(self, question: str, hits: list[Hit], messages: list[dict]) -> str:
        import requests

        try:
            r = requests.post(f"{self.host}/api/chat", timeout=self.timeout, json={
                "model": self.model, "messages": messages, "stream": False,
                "options": {"temperature": 0},
            })
            r.raise_for_status()
            payload = r.json()
            text = payload["message"]["content"]
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise OllamaUnavailableError("Ollama could not provide a completion") from exc
        if not isinstance(text, str) or not text.strip():
            raise OllamaUnavailableError("Ollama returned an empty completion")
        return text.strip()


# ------------------------------------------------------------------- answer
@dataclass
class Answer:
    question: str
    text: str
    found: bool
    # ok | not_found | no_citation | ungrounded | no_relevant_hits | no_access_or_no_hits
    reason: str
    citations: list[dict] = field(default_factory=list)   # n, doc, loc, quote
    hits: list[Hit] = field(default_factory=list)
    raw: str = ""
    latency_s: float = 0.0
    ungrounded: list[str] = field(default_factory=list)   # figures not found in cited sources
    unsupported: list[str] = field(default_factory=list)


def log_audit(path: str | Path, user: str, question: str, ans: Answer,
              tenant_id: str = "default") -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tenant": tenant_id,
        "user": user,
        "question": question,
        "docs_retrieved": sorted({h.chunk.doc for h in ans.hits}),
        "docs_cited": sorted({c["doc"] for c in ans.citations}),
        "found": ans.found,
        "reason": ans.reason,
    }
    if ans.ungrounded:
        record["ungrounded_figures"] = ans.ungrounded
    if ans.unsupported:
        record["unsupported_claims"] = ans.unsupported
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def clean_citations(text: str, n_sources: int) -> str:
    """Remove citation markers that point at sources the model was never given."""
    def keep(m: re.Match) -> str:
        return m.group(0) if 1 <= int(m.group(1)) <= n_sources else ""
    return re.sub(r"\s*\[(\d+)\]", keep, text).strip()


def answer(question: str, index: HybridIndex, backend, k: int | None = None,
           user_groups: list[str] | None = None, user: str = "anonymous",
           audit_path: str | Path | None = None, require_citation: bool = True,
           profile: Profile | None = None, tenant_id: str | None = None) -> Answer:
    """Answer one question for one user under one customer profile.

    Order of events: expand the query with the customer's vocabulary, search only
    what the user may read, drop weak hits (if configured), ask the model, then
    accept the answer only if it cites real sources, its figures occur in those
    sources, and each cited sentence has lexical support in its cited evidence.
    """
    p = profile or Profile()
    k = k or p.k
    t0 = time.time()
    hits = index.search(p.expand_query(question), k=k, user_groups=user_groups,
                        tenant_id=tenant_id)
    found_any = bool(hits)
    if p.min_dense is not None:
        hits = [h for h in hits if h.dense is None or h.bm25 > 0 or h.dense >= p.min_dense]

    if not hits:
        reason = "no_relevant_hits" if found_any else "no_access_or_no_hits"
        ans = Answer(question, p.not_found_text, False, reason, [], [], "")
    else:
        raw = backend.generate(question, hits, build_messages(question, hits, p))
        nums = sorted({int(n) for n in re.findall(r"\[(\d+)\]", raw) if 1 <= int(n) <= len(hits)})
        text = clean_citations(raw, len(hits))
        citations = [{"n": n, "doc": hits[n - 1].chunk.doc, "loc": hits[n - 1].chunk.loc,
                      "quote": best_quote(text, hits[n - 1].chunk.text)} for n in nums]
        if raw.strip().upper().startswith("NOT_FOUND"):
            ans = Answer(question, p.not_found_text, False, "not_found", [], hits, raw)
        elif require_citation and p.require_citation and not citations:
            ans = Answer(question, p.not_found_text, False, "no_citation", [], hits, raw)
        else:
            cited_sources = {n: hits[n - 1].chunk.text for n in nums}
            cited_text = list(cited_sources.values())
            bad = ungrounded_figures(text, question, cited_text) if p.check_grounding else []
            if bad:
                ans = Answer(question, p.not_found_text, False, "ungrounded", [], hits, raw,
                             ungrounded=bad)
            else:
                unsupported = (unsupported_claims(text, cited_sources)
                               if p.check_grounding and citations else [])
                if unsupported:
                    ans = Answer(question, p.not_found_text, False, "unsupported_claim",
                                 [], hits, raw, unsupported=unsupported)
                else:
                    ans = Answer(question, text, True, "ok", citations, hits, raw)

    ans.latency_s = time.time() - t0
    if audit_path:
        log_audit(audit_path, user, question, ans, p.tenant_id)
    return ans
