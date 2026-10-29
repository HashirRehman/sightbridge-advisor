"""Conservative fast paths. Unknown wording always falls back to semantic planning."""

import re

from app.domain.policies.question_policy import evaluate_question

_TOPIC = r"(?:a |an |the )?(?:1031(?: exchange)?|721(?: exchange)?|delaware statutory trust|dst)s?"
_DIRECT = re.compile(
    rf"(?:what (?:is|are) {_TOPIC}|how (?:does|do) {_TOPIC} work|"
    rf"(?:explain|describe) {_TOPIC}(?: in (?:plain|simple|everyday) (?:words|language))?|"
    rf"what are (?:the )?(?:general )?(?:risks|deadlines|requirements) (?:of|for) {_TOPIC})[?.!]*",
    re.IGNORECASE,
)
_REWRITE = re.compile(
    r"(?:(?:can|could) you |please )?(?:make (?:that|it|the (?:last|previous) answer) "
    r"(?:shorter|simpler|easier to understand)|(?:explain|say|rewrite|summarize|summarise) "
    r"(?:that|it|the (?:last|previous) answer)(?: (?:again|more simply|in simpler words|"
    r"in plain (?:English|language)|in (?:one|two|three|1|2|3) (?:short )?sentences))?)[?.!]*",
    re.IGNORECASE,
)
_ACKNOWLEDGEMENT = re.compile(
    r"(?:ok(?:ay)?[, ]*)?(?:thanks?(?: you)?|thank you|thx|ty|got it|great|cool|perfect|"
    r"awesome|that helps|makes sense)(?: (?:so much|a lot|very much))?[.! ]*",
    re.IGNORECASE,
)
_CONFUSION = re.compile(
    r"\b(?:(?:do|did|does|still|can)(?: not|n'?t)|don'?t|didn'?t)\s+(?:really\s+)?"
    r"(?:understand|get it|get that|follow)\b|\bconfus(?:ed|ing)\b|\bwhat do you mean\b|"
    r"\bmakes? no sense\b|\b(?:not|still) clear\b|\blost me\b",
    re.IGNORECASE,
)
_HELP_FACT = re.compile(
    r"\b(?:defer(?:s|red|ring|ral)?|tax(?:es)? (?:right away|immediately)|"
    r"swap(?:ping)? (?:one |an? )?(?:investment )?property|"
    r"pool(?:ing)? (?:investor )?money|(?:trade|exchange|contribute) "
    r"(?:their |your |the )?(?:property|real estate) for (?:shares|units)|"
    r"guaranteed? (?:yield|return|tax savings?))\b",
    re.IGNORECASE,
)


def direct_factual_request(question: str) -> bool:
    return _DIRECT.fullmatch(question.strip()) is not None


def acknowledgement(question: str) -> bool:
    """A thank-you or "got it" with no new request; it needs no model or search."""
    return _ACKNOWLEDGEMENT.fullmatch(question.strip()) is not None


def confusion_followup(question: str) -> bool:
    """The reader didn't understand the last answer and names no new subject."""
    return bool(_CONFUSION.search(question)) and not evaluate_question(question).in_scope_terms


def evidence_only_rewrite(question: str) -> bool:
    # A new topic, condition, correction, deadline or "main catch" needs semantic planning.
    return _REWRITE.fullmatch(question.strip()) is not None


def evidence_reference_followup(question: str) -> bool:
    """A source request about the immediately preceding answer, with no new topic."""
    return (
        re.fullmatch(
            r"(?:which|what) (?:source|document|guide)s? (?:supports?|back up|backed up) "
            r"(?:that|those|the (?:last|previous) (?:answer|claims?|restrictions?|risks?))"
            r"(?: (?:claims?|restrictions?|risks?))?[?.!]*|"
            r"(?:what|which) (?:public )?documents? do you base that on[?.!]*",
            question.strip(),
            flags=re.IGNORECASE,
        )
        is not None
    )


def help_reply_requires_sources(reply: str) -> bool:
    """Spot common financial mechanism claims in uncited app metadata."""
    return _HELP_FACT.search(reply) is not None
