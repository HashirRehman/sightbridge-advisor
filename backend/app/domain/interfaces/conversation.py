from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.domain.interfaces.safety import SafetyVerdict

PlanAction = Literal["search", "rewrite", "cite", "help", "clarify"]


@dataclass
class ConversationPlan:
    """How to answer one message.

    search: retrieve passages for `search_queries`, then generate a cited answer.
    rewrite: restate the previous answer in simpler or shorter words from its own evidence.
    cite: name the sources behind the previous answer from its own evidence.
    help/clarify: `reply` is final text about the app itself; it never carries tax facts.
    """

    action: PlanAction
    safety_verdict: SafetyVerdict = SafetyVerdict.ALLOW
    search_queries: list[str] = field(default_factory=list)
    alternative_query: str = ""
    reply: str = ""
    model_id: str | None = None

    @property
    def query(self) -> str:
        return "; ".join(self.search_queries)[:1000]


class ConversationPlanner(Protocol):
    """One semantic decision per message: safety verdict, action and resolved searches."""

    def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan: ...
