"""Lexical score: quoted never-violations plus keyword overlap. No vectors."""

from __future__ import annotations

import re

WORD = re.compile(r"[a-z0-9']+")
# Double quotes always delimit a phrase; single quotes only when they are not
# apostrophes (not glued to a word character on the outside).
QUOTED = re.compile(r'"([^"]+)"|(?<!\w)\'((?:[^\']|\'(?=\w))+)\'(?!\w)')
LEADING_POLARITY = re.compile(r"^\s*(never|why)\b[\s:,-]*", re.IGNORECASE)
# Words that frame a rule but are not the thing it forbids.
FRAMING = frozenset({"never", "avoid", "without", "with", "that", "this", "from", "into", "than"})


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def tokens(text: str) -> set[str]:
    return {_stem(word) for word in WORD.findall(text.lower()) if len(word) > 2}


def _forbidden_terms(directive: str) -> set[str]:
    """Terms a never-rule names: framing words and short function words dropped."""
    body = LEADING_POLARITY.sub("", directive)
    return {term for term in tokens(body) if len(term) > 3 and term not in FRAMING}


def score_text(content: str, standards: list[dict]) -> dict:
    content = content or ""
    have = tokens(content)
    positive_terms: set[str] = set()
    violations: list[str] = []
    for row in standards:
        polarity = row.get("polarity")
        directive = str(row.get("directive", ""))
        if polarity in ("do", "why"):
            positive_terms |= tokens(directive)
        if polarity != "never":
            continue
        quotes = [left or right for left, right in QUOTED.findall(directive)]
        if quotes:
            for quote in quotes:
                if quote.lower() in content.lower():
                    violations.append(quote)
            continue
        needed = _forbidden_terms(directive)
        if needed and needed <= have:
            violations.append(directive)
    positive = (len(have & positive_terms) / len(positive_terms)) if positive_terms else 0.0
    negative = float(len(violations))
    return {
        "score": positive - negative,
        "breakdown": {"positive": positive, "negative": negative},
        "violations": violations,
    }
