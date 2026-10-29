"""Deterministic pre-generation policy. Runs before any model call, so critical refusals never
depend on the model following its prompt.

Order matters: injection → personal decision → personal framing → scope.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.domain.policies.safety import normalise_for_checks


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    CAPABILITIES = "capabilities"
    PROMPT_INJECTION = "prompt_injection"
    # Asks for a personal recommendation, suitability/eligibility decision or tax figure:
    # refused locally, with no model call.
    PERSONALIZED_ADVICE = "personalized_advice"
    # A general question phrased about the asker's own situation ("should I report it on
    # Form 8824?"): answered as general education, never as advice.
    PERSONAL_FRAMING = "personal_framing"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome
    in_scope_terms: bool  # mentions 1031/DST/721/REIT vocabulary
    matched_rule: str | None = None


def _rx(*patterns: str) -> list[tuple[str, re.Pattern[str]]]:
    return [(p, re.compile(p, re.IGNORECASE)) for p in patterns]


_INJECTION = _rx(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your|system)\b.{0,20}\b(instructions?|rules|prompts?|guidelines|directions)",
    r"\b(reveal|show|print|repeat|output|tell me)\b.{0,30}\b(system prompt|hidden prompt|instructions|your prompt|your rules)",
    r"\bsystem prompt\b",
    r"\byou are (now|no longer)\b",
    r"\b(pretend|act|behave) (to be|as if|as an?|like)\b",
    r"\b(developer|god|dan|jailbreak) mode\b|\bjailbreak\b",
    r"\bnew (instructions|rules|persona)\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
    r"\b(without|no) (disclaimers?|restrictions|guardrails|filters)\b",
)  # fmt: skip

_PERSONALIZED = _rx(
    # Requests for a recommendation or a decision about the asker's own situation.
    r"\bshould (i|we|my clients?|our clients?|they|he|she) (invest|buy|sell|put|move|choose|pick|use|go with|switch|do a|do an|do the)\b",
    r"\b(do|would|can) you (recommend|suggest|advise)\b",
    r"\b(recommend|suggest)\b.{0,40}\b(for|to) (me|us|my|our)\b",
    r"\bwhat would you do\b",
    r"\b(good|bad|wise|smart|right|best|better|suitable) (idea|choice|option|investment|move|fit) for (me|us|my|our)\b",
    r"\bis (it|this|that|a dst|a 1031|a 721)\b.{0,40}\b(right|suitable|appropriate|good|wise|smart) for (me|us|my|our)\b",
    # Personal figures or tax computations.
    r"\b(i|my|we|our)\b[^.?!]{0,60}\$\s?\d",
    r"\$\s?\d[\d,.]*\s*(k|m|million|thousand)?\b[^.?!]{0,60}\b(i|my|we|our)\b",
    r"\b(i|my|we|our)\b[^.?!]{0,60}\b\d[\d,.]*\s*(k|m|mm|million|thousand|dollars)\b",
    r"\bhow much (tax|taxes)?\s?(will|would|do|should|can|could) (i|we|my clients?)\b",
    r"\b(calculate|compute|estimate|figure out) (my|our|my client'?s?)\b",
    # Picking specific products, sponsors or eligibility determinations.
    r"\bwhich (dst|reit|sponsor|fund|property|offering|investment|program)s?\b.{0,30}\b(should|best|buy|choose|pick)\b",
    r"\b(best|top|safest) (dst|reit|sponsor|offering|program)s?\b",
    r"\b(am i|are we|is my client|are my clients|do i|does my client) (an? )?(accredited|eligible|qualif)",
)  # fmt: skip

_PERSONAL_FRAMING = _rx(
    r"\bshould (i|we|my clients?|our clients?|they|he|she)\b",
    r"\bwhat (should|would you have) (i|we|my clients?)\b",
    r"\bmy (tax|taxes|tax bill|tax liability|tax situation|basis|capital gains?|portfolio)\b",
)  # fmt: skip

_IN_SCOPE = re.compile(
    r"\b(1031|721|like[- ]kind|exchange|exchanges|exchanger|dsts?|delaware statutory|statutory trust|"
    r"reits?|upreit|downreit|operating partnership|op units?|replacement property|relinquished|"
    r"qualified intermediary|boot|identification period|exchange period|45[- ]day|180[- ]day|"
    r"tenants?[- ]in[- ]common|tic|accredited investor|private placement|regulation d|reg d|"
    r"rev(enue)?\.? rul(ing)?\.? 2004-86|section 1031|section 721|deferred gain|capital gains? deferral|"
    r"carryover basis|form 8824|master lease|sponsor)\b",
    re.IGNORECASE,
)

# Topics Sightbridge or advisors may raise but that this assistant deliberately does not cover.
_OUT_OF_SCOPE = re.compile(
    r"\b(opportunity zones?|qualified opportunity funds?|qofs?|crypto(currency)?|bitcoin|"
    r"ethereum|stocks?|options trading|forex|401\s?\(?k\)?|ira|roth|annuit(y|ies)|life insurance|"
    r"mortgage rates?|weather|recipe|sports?|election|poem|joke)\b",
    re.IGNORECASE,
)

# Match complete requests for application help, not questions about arbitrary subjects.
_ASSISTANT_ROLE = re.compile(
    r"(?:what(?: is|'s) your (?:job|role|purpose|function|specialty|speciality)|"
    r"what (?:is your job|are you here|are you designed|were you built) to do|"
    r"who are you|what (?:do you actually do|is this assistant for|are you)|"
    r"(?:describe|explain|tell me about) your (?:job|role|purpose)|"
    r"(?:can|could) you (?:tell me|explain) what your (?:job|role|purpose) is)"
    r"(?: here| in this app| as an assistant)?(?:[.!?]+)?",
    re.IGNORECASE,
)

_CAPABILITIES = re.compile(
    r"(?:what (?:can you do(?: for me)?|do you do|is your purpose|are your capabilities|can i ask you)|"
    r"who are you|how can you help(?: me)?|what can you help(?: me)? with|"
    r"what (?:topics|questions) (?:can|do) you (?:cover|answer|help with)|"
    r"what (?:kind of |types? of )?information (?:can you|you can|do you) "
    r"(?:give|provide|offer|share)(?: to)?(?: me)?|"
    r"tell me (?:about yourself|what you can do)|"
    r"what (?:are you able to do|do you help with)|"
    r"how (?:do|could|would) you help(?: me)?|help|hello|hi|hey)(?:[.!?]+)?",
    re.IGNORECASE,
)

# Advice phrasing that must never appear in a generated answer, whatever the question was.
_ADVICE_LANGUAGE = _rx(
    r"\b(you|your clients?) should (invest|buy|sell|choose|consider investing|do)\b",
    r"\bI (would )?(recommend|suggest|advise)\b",
    r"\bwe (would )?(recommend|suggest|advise)\b",
    r"\b(is|would be) (a )?(good|great|suitable|right|smart|wise) (fit|choice|option|investment|idea) for you\b",
    r"\byour \$\s?\d",
)  # fmt: skip


def _first_match(rules: list[tuple[str, re.Pattern[str]]], text: str) -> str | None:
    return next((name for name, rx in rules if rx.search(text)), None)


def evaluate_question(question: str) -> PolicyDecision:
    question = normalise_for_checks(question)
    in_scope = bool(_IN_SCOPE.search(question))

    if rule := _first_match(_INJECTION, question):
        return PolicyDecision(PolicyOutcome.PROMPT_INJECTION, in_scope, rule)
    if rule := _first_match(_PERSONALIZED, question):
        return PolicyDecision(PolicyOutcome.PERSONALIZED_ADVICE, in_scope, rule)
    if rule := _first_match(_PERSONAL_FRAMING, question):
        return PolicyDecision(PolicyOutcome.PERSONAL_FRAMING, in_scope, rule)
    if not in_scope and (m := _OUT_OF_SCOPE.search(question)):
        return PolicyDecision(PolicyOutcome.OUT_OF_SCOPE, in_scope, m.group(0).lower())
    # Normalise conversational wrappers only for self-description matching. Safety and
    # topical checks above always inspect the complete original question.
    help_question = " ".join(question.replace("\u2019", "'").split())
    help_question = re.sub(
        r"^(?:(?:hi|hello|hey)[,!]?\s+)?(?:please\s+)?", "", help_question, flags=re.I
    )
    if _ASSISTANT_ROLE.fullmatch(help_question):
        return PolicyDecision(PolicyOutcome.CAPABILITIES, in_scope, "assistant_role")
    if _CAPABILITIES.fullmatch(help_question):
        return PolicyDecision(PolicyOutcome.CAPABILITIES, in_scope, "assistant_capabilities")
    # Questions with no topical vocabulary are still allowed through: retrieval decides.
    return PolicyDecision(PolicyOutcome.ALLOW, in_scope)


def contains_advice_language(text: str) -> bool:
    return _first_match(_ADVICE_LANGUAGE, text) is not None
