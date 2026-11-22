"""Cheap, model-free checks that an answer really comes from its cited sources.

A model can cite [1] and still state a figure that is not in source [1]. For
contracts, invoices and policies a wrong number is the costly failure, so every
figure and detected unit must appear together in its cited source text. Claim
checks also compare negation and capitalized name tokens. These are lexical
heuristics, not proof that an answer is semantically entailed.
"""
from __future__ import annotations

import re

from .index import tokenize

_CITE = re.compile(r"\[\d+\]")
_CITE_NUMBER = re.compile(r"\[(\d+)\]")
_CLAIM_SENTENCE = re.compile(r"(?<=[.!?])\s+(?!\[\d+\])|\n+")
_ENTITY_WORD = re.compile(r"\b[A-Z][A-Za-z0-9&.'’-]*\b")
_ENTITY_EXCLUSIONS = {
    "a", "an", "the", "this", "that", "these", "those", "it", "we", "our",
    "your", "their", "i", "he", "she", "they", "if", "when", "where", "while",
    "as", "for", "in", "on", "at", "by", "under", "according", "note", "figure",
    "table", "section", "appendix", "r", "zar", "usd", "eur", "gbp", "cad", "aud",
}
# "48,500", "48 500" (space or non-breaking space) and "48500" count as the same figure.
# The group before the first separator has 1-3 digits, so "2024 100" stays two figures.
_SEP = "[ ,\u00a0\u202f]"
_THOUSANDS = re.compile(rf"(?<!\d)\d{{1,3}}(?:{_SEP}\d{{3}})+(?!\d)")
_SEPARATOR = re.compile(_SEP)
_FIGURE = re.compile(r"\d+(?:\.\d+)?")
_NUMBER_MENTION = re.compile(
    r"(?<![\d.])(?:"
    r"\d{1,3}(?:[ ,\u00a0\u202f]\d{3})+"
    r"|"
    r"\d+(?![ ,\u00a0\u202f]\d{3})"
    r")(?:\.\d+)?(?!\d)(?!\.\d)"
)
_CURRENCY_PREFIX = re.compile(
    r"(?i)(?<![A-Z])(?P<unit>R|ZAR|USD|EUR|GBP|CAD|AUD|\$|€|£)\s*$"
)
_UNIT_SUFFIX = re.compile(
    r"(?i)^\s*(?:"
    r"(?P<percent>%|(?:percent(?:age)?|per\s+cent)\b)|"
    r"(?P<duration>days?|weeks?|months?|years?|hours?|minutes?)\b|"
    r"(?P<currency>rand|rands|ZAR|USD|EUR|GBP|CAD|AUD)\b"
    r")"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_NEGATION = re.compile(
    r"\b(?:no|not|never|neither|nor|without|cannot|can't|couldn't|doesn't|don't|"
    r"isn't|aren't|wasn't|weren't|won't|wouldn't|shouldn't|mustn't|except)\b",
    re.IGNORECASE,
)
_WRITTEN_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100",
}
_VERSION = re.compile(r"(?<![\w])\d+(?:\.\d+){2,}(?![\w])")
_ABBREVIATION_SENTENCE_END = re.compile(
    r"(?:Pty|Ltd|Inc|Co|Dr|Mr|Mrs|Ms|No)\.\)?$",
    re.IGNORECASE,
)
_STOP = {"the", "a", "an", "of", "for", "to", "in", "is", "are", "was", "were", "by", "and",
         "or", "with", "be", "at", "this", "that", "it", "its", "on", "as", "per"}
_CLAIM_TERMS = _STOP | {"according", "document", "documents", "source", "sources", "states",
                        "says", "shows", "indicates", "reported", "provided"}


def merge_thousands(text: str) -> str:
    """Remove thousands separators inside numbers: 'R48 500' and 'R48,500' both become 'R48500'."""
    return _THOUSANDS.sub(lambda m: _SEPARATOR.sub("", m.group(0)), text)


def _normalize_written_numbers(text: str) -> str:
    text = re.sub(
        r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety|hundred)\s*\(\d+\)",
        lambda match: re.search(r"\((\d+)\)", match.group(0)).group(1),
        text,
        flags=re.IGNORECASE,
    )
    def replace(match: re.Match) -> str:
        return _WRITTEN_NUMBERS.get(match.group(0).casefold(), match.group(0))
    return re.sub(
        r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety|hundred)\b",
        replace,
        text,
        flags=re.IGNORECASE,
    )


def _split_sentences(text: str, answer: bool = False) -> list[str]:
    splitter = _CLAIM_SENTENCE if answer else _SENTENCE
    raw = [part.strip() for part in splitter.split(text) if part.strip()]
    sentences: list[str] = []
    for part in raw:
        if (sentences
                and _ABBREVIATION_SENTENCE_END.search(sentences[-1])):
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return sentences


def figures(text: str) -> set[str]:
    """Numbers in a text, normalised (thousands separators and citation marks removed)."""
    return set(_FIGURE.findall(merge_thousands(_CITE.sub("", text))))


def ungrounded_figures(answer_text: str, question: str, sources: list[str]) -> list[str]:
    """Figures or explicit units in the answer that do not occur in cited source text."""
    del question
    allowed: set[tuple[str, str | None]] = set()
    for s in sources:
        allowed.update(_number_mentions(s))
    return sorted({
        figure for figure, unit in _number_mentions(answer_text)
        if (figure, unit) not in allowed
    })


def _number_mentions(text: str) -> set[tuple[str, str | None]]:
    text = _normalize_written_numbers(text)
    text = _CITE.sub("", text)
    mentions: set[tuple[str, str | None]] = set()
    for match in _NUMBER_MENTION.finditer(text):
        version_span = text[max(0, match.start() - 4):match.end() + 4]
        if _VERSION.search(version_span):
            continue
        prefix = text[max(0, match.start() - 12):match.start()]
        suffix = text[match.end():match.end() + 20]
        currency = _CURRENCY_PREFIX.search(prefix)
        unit = None
        if currency:
            currency_name = currency.group("unit").casefold()
            unit = {
                "r": "currency:zar",
                "zar": "currency:zar",
                "$": "currency:usd",
                "usd": "currency:usd",
                "€": "currency:eur",
                "eur": "currency:eur",
                "£": "currency:gbp",
                "gbp": "currency:gbp",
                "cad": "currency:cad",
                "aud": "currency:aud",
            }[currency_name]
        else:
            suffix_match = _UNIT_SUFFIX.match(suffix)
            if suffix_match:
                token = suffix_match.group("percent") or suffix_match.group("duration")
                if token:
                    token = token.casefold()
                    if token in {"%", "percent", "percentage", "per cent"}:
                        unit = "percent"
                    else:
                        unit = re.sub(r"s$", "", token)
                else:
                    currency_name = suffix_match.group("currency").casefold()
                    unit = {
                        "rand": "currency:zar",
                        "rands": "currency:zar",
                        "zar": "currency:zar",
                        "usd": "currency:usd",
                        "eur": "currency:eur",
                        "gbp": "currency:gbp",
                        "cad": "currency:cad",
                        "aud": "currency:aud",
                    }[currency_name]
        number = _SEPARATOR.sub("", match.group(0))
        mentions.add((number, unit))
    return mentions


def _named_terms(text: str) -> set[str]:
    return {
        word.casefold()
        for word in _ENTITY_WORD.findall(text)
        if word.casefold() not in _ENTITY_EXCLUSIONS
        and not re.fullmatch(r"(?:r|zar|usd|eur|gbp|cad|aud)?\d[\d,.]*", word, re.IGNORECASE)
    }


def unsupported_claims(answer_text: str, sources_by_number: dict[int, str],
                       min_coverage: float = 0.6) -> list[str]:
    """Find uncited or lexically unsupported answer sentences.

    This deterministic check is a conservative guard, not a proof of entailment.
    Each answer sentence must cite at least one supplied source, at least 60%
    of its non-trivial terms must occur in a cited source sentence, its negation
    must match, and its capitalized terms must occur in that source sentence.
    """
    failures: list[str] = []
    for sentence in _split_sentences(answer_text, answer=True):
        numbers = {int(n) for n in _CITE_NUMBER.findall(sentence) if int(n) in sources_by_number}
        claim_terms = {t for t in tokenize(_CITE.sub("", sentence)) if t not in _CLAIM_TERMS}
        if not claim_terms:
            continue
        if not numbers:
            failures.append(sentence)
            continue
        claim_text = _CITE.sub("", sentence)
        claim_negated = bool(_NEGATION.search(claim_text))
        claim_names = _named_terms(claim_text)
        supported = False
        for number in numbers:
            for source_sentence in _split_sentences(sources_by_number[number]):
                source_terms = {
                    t for t in tokenize(source_sentence) if t not in _CLAIM_TERMS
                }
                coverage = len(claim_terms & source_terms) / len(claim_terms)
                if (
                    coverage >= min_coverage
                    and claim_negated == bool(_NEGATION.search(source_sentence))
                    and claim_names.issubset(set(tokenize(source_sentence)))
                ):
                    supported = True
                    break
            if supported:
                break
        if not supported:
            failures.append(sentence)
    return failures


def best_quote(answer_text: str, chunk_text: str, limit: int = 240) -> str:
    """The sentence of a source that best matches the answer, for showing next to a citation."""
    wanted = {t for t in tokenize(_CITE.sub("", answer_text))} - _STOP
    sentences = _split_sentences(chunk_text)
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: len(wanted & set(tokenize(s))))
    return best if len(best) <= limit else best[: limit - 1].rstrip() + "…"
