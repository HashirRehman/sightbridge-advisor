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


def related_topic_queries(text: str) -> list[str]:
    """Neutral searches for recognized subjects, never corrections of user identifiers."""
    topics = (
        (r"\b1031\b|\blike[- ]kind\b", "1031 exchange rules and requirements"),
        (
            r"\bdsts?\b|\bdelaware statutory trusts?\b",
            "Delaware statutory trust structure and risks",
        ),
        (
            r"\b721\b|\bupreit\b|\boperating partnership\b",
            "721 exchange operating partnership units",
        ),
    )
    return [query for pattern, query in topics if re.search(pattern, text, re.IGNORECASE)]
