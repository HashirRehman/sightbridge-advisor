from enum import StrEnum


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
