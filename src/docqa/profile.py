"""Per-tenant configuration for a domain-neutral document assistant.

One code base, many customers: everything that differs between companies lives
in a small JSON "profile" instead of in the code. A profile controls who the
assistant says it is, what the documents are, how it writes answers, what it
says when it cannot answer, how queries are expanded with the customer's own
vocabulary, and the retrieval / chunking settings.

Safety rules (citations, abstention, permission filtering, untrusted sources)
are NOT profile settings a customer can switch off in the prompt; the few that
are switchable (`require_citation`, `check_grounding`) are explicit flags.

Unknown keys are rejected so a typo like "anser_style" fails loudly instead of
being silently ignored. Keys starting with "_" (for example "_comment") are
ignored, which gives JSON files a place for notes.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_NOT_FOUND_TEXT = "I couldn't find this in the documents you have access to."
ANSWER_STYLES = ("concise", "standard", "detailed")


@dataclass
class Profile:
    # identity ------------------------------------------------------------
    tenant_id: str = "default"          # written to the audit log
    name: str = "Document Assistant"    # what the assistant calls itself
    organisation: str = ""              # the tenant or organisation's name
    domain: str = "the documents provided"
    audience: str = "people using the service"

    # voice ---------------------------------------------------------------
    language: str = "English"
    tone: str = "professional and precise"
    answer_style: str = "concise"       # concise | standard | detailed
    extra_instructions: list[str] = field(default_factory=list)
    not_found_text: str = DEFAULT_NOT_FOUND_TEXT

    # vocabulary ----------------------------------------------------------
    # term -> other words people use for the same thing, e.g.
    # {"notice period": ["terminate", "termination"]}. Used for retrieval only.
    glossary: dict[str, list[str]] = field(default_factory=dict)

    # retrieval and ingestion --------------------------------------------
    k: int = 4
    max_chars: int = 900
    overlap: int = 150
    default_acl: list[str] = field(default_factory=lambda: ["admin"])
    min_dense: float | None = None      # drop weak embedding-only hits; None = off

    # safety switches -----------------------------------------------------
    require_citation: bool = True
    check_grounding: bool = True        # reject unsupported claims and figures

    def __post_init__(self) -> None:
        if self.answer_style not in ANSWER_STYLES:
            raise ValueError(f"answer_style must be one of {ANSWER_STYLES}, got {self.answer_style!r}")
        if self.k < 1:
            raise ValueError("k must be at least 1")
        if self.max_chars < 100 or not 0 <= self.overlap < self.max_chars:
            raise ValueError("need max_chars >= 100 and 0 <= overlap < max_chars")
        if not self.default_acl or not all(isinstance(g, str) for g in self.default_acl):
            raise ValueError("default_acl must be a non-empty list of group names")
        if not all(isinstance(x, str) for x in self.extra_instructions):
            raise ValueError("extra_instructions must be a list of strings")
        for term, synonyms in self.glossary.items():
            if not isinstance(synonyms, list) or not all(isinstance(s, str) for s in synonyms):
                raise ValueError(f"glossary entry {term!r} must map to a list of strings")
        if not self.not_found_text.strip():
            raise ValueError("not_found_text must not be empty")

    # -- helpers ----------------------------------------------------------
    def expand_query(self, question: str) -> str:
        """Append the customer's synonyms for any glossary term found in the question."""
        lowered = question.lower()
        extra: list[str] = []
        for term, synonyms in self.glossary.items():
            if re.search(rf"\b{re.escape(term.lower())}\b", lowered):
                extra.extend(synonyms)
        return f"{question} {' '.join(extra)}" if extra else question

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown profile setting(s): {', '.join(unknown)}. "
                             f"Valid settings: {', '.join(sorted(known))}")
        return cls(**data)


def load_profile(path: str | Path | None = None) -> Profile:
    """Load a profile from JSON, or return the neutral default when no path is given."""
    if path is None:
        return Profile()
    return Profile.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
