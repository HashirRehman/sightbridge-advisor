import logging
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from app.application.rag.prompts import SYSTEM_PROMPT, render_user_message
from app.domain.interfaces.generation import GenerationError, GenerationMode, GenerationResult
from app.domain.models.answers import AnswerDraft, LabeledPassage, TokenUsage
from app.infrastructure.bedrock.invocation import invoke_bounded

logger = logging.getLogger(__name__)


class BedrockAnswerGenerator:
    """Bedrock Converse via LangChain, parsed into the AnswerDraft schema."""

    def __init__(
        self,
        client: Any,
        model_id: str,
        max_tokens: int,
        region_name: str,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        llm = ChatBedrockConverse(
            client=client,
            region_name=region_name,
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        if model_id.startswith("deepseek."):
            # DeepSeek otherwise omits optional arrays and puts the entire direct answer
            # in key_considerations. Require all fields in its tool schema, while keeping
            # the shared parser tolerant of omitted empty arrays in negative responses.
            schema = AnswerDraft.model_json_schema()
            schema["required"] = ["answerable", "explanation", "key_considerations"]
            self._chain = llm.with_structured_output(schema, include_raw=True)
        else:
            self._chain = llm.with_structured_output(AnswerDraft, include_raw=True)

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(
        self, question: str, passages: list[LabeledPassage], mode: GenerationMode
    ) -> GenerationResult:
        messages = [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(render_user_message(question, passages, mode)),
        ]
        try:
            result = invoke_bounded(lambda: self._chain.invoke(messages), self._timeout_seconds)
        except TimeoutError as exc:
            raise GenerationError("model invocation timed out") from exc
        except (ClientError, BotoCoreError) as exc:
            code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else "transport"
            logger.warning("bedrock invocation failed", extra={"error_code": code})
            raise GenerationError(f"bedrock error: {code}") from exc

        if not isinstance(result, dict):
            raise GenerationError("model returned malformed structured output")
        parsed = result.get("parsed")
        if isinstance(parsed, dict):
            try:
                parsed = AnswerDraft.model_validate(parsed)
            except ValidationError as exc:
                raise GenerationError("model returned malformed structured output") from exc
        if not isinstance(parsed, AnswerDraft):
            error = result.get("parsing_error")
            logger.warning("model output failed schema validation", extra={"error": str(error)})
            raise GenerationError("model returned malformed structured output") from (
                error if isinstance(error, (ValidationError, Exception)) else None
            )

        raw = result.get("raw")
        usage = None
        if isinstance(raw, AIMessage) and raw.usage_metadata:
            usage = TokenUsage(
                input_tokens=raw.usage_metadata["input_tokens"],
                output_tokens=raw.usage_metadata["output_tokens"],
            )
        return GenerationResult(draft=parsed, model_id=self._model_id, usage=usage)
