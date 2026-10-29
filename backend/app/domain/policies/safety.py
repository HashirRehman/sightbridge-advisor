"""High-confidence local checks complement the semantic safety gate; neither is infallible."""

import re
import unicodedata

from app.domain.interfaces.safety import SafetyVerdict

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SSN = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")
_SECRET = re.compile(
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:password|api[ _-]?key|secret[ _-]?(?:key|access[ _-]?key)|access[ _-]?token|"
    r"bearer[ _-]?token|ssn|social security number|bank account(?: number)?)"
    r"\s*(?:is\s+|[:=]\s*)\S{4,}",
    re.I,
)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_HARM = re.compile(
    r"\b(?:help me|show me how|how (?:can|do|would) (?:i|we)|write|create|make|build)\b"
    r".{0,100}\b(?:evade taxes|launder money|hide (?:my |our |the )?(?:income|assets|gains)|"
    r"fake (?:irs|tax|bank|identity)|forge|falsify|defraud|mislead investors|"
    r"build a bomb|kill someone|poison someone|ransomware|phishing|steal passwords)\b",
    re.I,
)
_SELF_HARM = re.compile(
    r"\b(?:want to|going to|plan to|how (?:can|do) i)\s+(?:kill myself|end my life|harm myself)\b",
    re.I,
)


def normalise_for_checks(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf"
    )


def sensitive_input(text: str) -> bool:
    text = normalise_for_checks(text)
    if _EMAIL.search(text) or _SSN.search(text) or _SECRET.search(text):
        return True
    for match in _CARD.finditer(text):
        digits = [int(x) for x in match.group() if x.isdigit()]
        if not 13 <= len(digits) <= 19 or len(set(digits)) < 2:
            continue
        checksum = sum(
            (d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
            for i, d in enumerate(reversed(digits))
        )
        if checksum % 10 == 0:
            return True
    return False


def local_safety_verdict(text: str) -> SafetyVerdict:
    if sensitive_input(text):
        return SafetyVerdict.SENSITIVE_DATA
    if _SELF_HARM.search(normalise_for_checks(text)):
        return SafetyVerdict.SELF_HARM
    if _HARM.search(normalise_for_checks(text)):
        return SafetyVerdict.MISCONDUCT
    return SafetyVerdict.ALLOW


def question_for_storage(text: str) -> str:
    return (
        "[Question removed because it contained sensitive information.]"
        if sensitive_input(text)
        else text
    )
