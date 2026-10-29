"""Expand domain abbreviations for retrieval; the user's question stays unchanged."""

import re

_EXPANSIONS = (
    (r"\b1031\b", "1031 like-kind real estate"),
    (r"\b721\b", "721 UPREIT operating partnership"),
    (r"\bDSTs?\b", "DST Delaware statutory trust"),
)


def expand_retrieval_query(question: str) -> str:
    for pattern, replacement in _EXPANSIONS:
        question = re.sub(pattern, replacement, question, flags=re.IGNORECASE)
    return question
