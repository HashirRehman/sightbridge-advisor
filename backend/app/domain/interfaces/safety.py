from enum import StrEnum
from typing import Protocol


class SafetyVerdict(StrEnum):
    ALLOW = "allow"
    ASSISTANT_HELP = "assistant_help"
    HARMFUL = "harmful"
    SELF_HARM = "self_harm"
    MISCONDUCT = "misconduct"
    DISCRIMINATION = "discrimination"
    SENSITIVE_DATA = "sensitive_data"
    INJECTION = "injection"
    PERSONALIZED_ADVICE = "personalized_advice"
    OUT_OF_SCOPE = "out_of_scope"


class SafetyCheckError(Exception):
    pass


class SafetyGate(Protocol):
    def check(self, text: str) -> SafetyVerdict: ...
    def check_output(self, text: str) -> SafetyVerdict: ...
