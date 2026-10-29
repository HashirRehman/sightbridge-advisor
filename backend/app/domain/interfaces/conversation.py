from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.domain.interfaces.generation import GenerationError
from app.domain.interfaces.safety import SafetyVerdict


class ConversationPlan(BaseModel):
    safety_verdict: SafetyVerdict | None = None
    model_id: str | None = None
    action: Literal["search", "help", "clarify"] = Field(
        description="search for factual questions; help for app/source information; clarify when "
        "the topic is ambiguous. A vague exchange question needs clarification if no single "
        "exchange type is established in history."
    )
    query: str = Field(
        default="",
        max_length=1000,
        description="Standalone search question explicitly naming "
        "the intended topic and any established exchange type. Resolve follow-ups from history. "
        "Never silently change section numbers. Empty for help or clarify.",
    )
    alternative_query: str = Field(
        default="",
        max_length=1000,
        description="Alternative search wording for "
        "the same factual intent, explicitly naming its topic. Empty for help or clarify.",
    )
    search_queries: list[str] = Field(default_factory=list, max_length=3)
    reply: str = Field(
        default="",
        max_length=1600,
        description="For help: a tailored reply about app capabilities "
        "or actual inventory, without teaching tax facts or defining exchanges. For clarify: one "
        "short question to resolve ambiguity. For search: empty.",
    )


class ConversationRouter(Protocol):
    def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan: ...


class ConversationResponder(Protocol):
    model_id: str

    def respond(self, question: str, history: list[dict[str, str]]) -> str: ...


class ConversationNeedsEvidence(GenerationError):
    def __init__(self, queries: list[str]) -> None:
        super().__init__("conversation reply requires source evidence")
        self.queries = queries
