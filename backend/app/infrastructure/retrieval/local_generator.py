"""Extractive stand-in for the LLM in local development (no AWS) — never production.

Quotes the leading sentences of the best passages verbatim, so the full pipeline (citation
verification, disclaimer, persistence, UI) runs end-to-end. It does not synthesise or judge
relevance; answer quality is measured with Bedrock.
"""

import re

from app.domain.interfaces.generation import GenerationMode, GenerationResult
from app.domain.models.answers import AnswerDraft, DraftCitation, DraftClaim, LabeledPassage

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in _SENTENCE.split(flat) if 40 <= len(s.strip()) <= 400]


class LocalExtractiveGenerator:
    model_id = "local-extractive-v1"

    def generate(
        self, question: str, passages: list[LabeledPassage], mode: GenerationMode
    ) -> GenerationResult:
        claims: list[DraftClaim] = []
        for passage in passages[:3]:
            for sentence in _sentences(passage.content)[:1]:
                claims.append(
                    DraftClaim(
                        text=sentence,
                        citations=[DraftCitation(passage_id=passage.label, quote=sentence)],
                    )
                )
        draft = AnswerDraft(
            answerable=bool(claims), explanation=claims[:2], key_considerations=claims[2:]
        )
        return GenerationResult(draft=draft, model_id=self.model_id, usage=None)
