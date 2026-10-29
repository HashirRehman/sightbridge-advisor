from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.domain.interfaces.generation import GenerationError, GenerationMode
from app.domain.models.answers import TokenUsage
from app.infrastructure.bedrock.generator import BedrockAnswerGenerator
from tests.factories import IDENTIFICATION_TEXT, passage

MODEL = "us.amazon.nova-lite-v1:0"


class FakeConverseClient:
    """Stands in for boto3's bedrock-runtime client; records the Converse request."""

    def __init__(self, tool_input: dict[str, Any] | None = None, error: Exception | None = None):
        self.tool_input = tool_input
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if self.error:
            raise self.error
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "t1",
                                "name": "AnswerDraft",
                                "input": self.tool_input,
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 812, "outputTokens": 96, "totalTokens": 908},
            "metrics": {"latencyMs": 900},
        }


def _generate(client: FakeConverseClient) -> Any:
    generator = BedrockAnswerGenerator(client, MODEL, max_tokens=900, region_name="us-east-1")
    return generator.generate(
        "What is the identification period?",
        [passage("P1", IDENTIFICATION_TEXT)],
        GenerationMode.STANDARD,
    )


def test_structured_answer_is_parsed_and_request_is_bounded() -> None:
    client = FakeConverseClient(
        {
            "answerable": True,
            "explanation": [
                {
                    "text": "Property must be identified within 45 days.",
                    "citations": [{"passage_id": "P1", "quote": "identified within 45 days"}],
                }
            ],
            "key_considerations": [],
        }
    )

    result = _generate(client)

    assert result.draft.explanation[0].citations[0].passage_id == "P1"
    assert result.usage == TokenUsage(input_tokens=812, output_tokens=96)
    request = client.requests[0]
    assert request["modelId"] == MODEL
    assert request["inferenceConfig"] == {"maxTokens": 900, "temperature": 0.0}
    assert request["toolConfig"]["tools"][0]["toolSpec"]["name"] == "AnswerDraft"
    user_text = request["messages"][0]["content"][0]["text"]
    assert '<passage id="P1"' in user_text and "<question>" in user_text
    assert "untrusted reference text" in request["system"][0]["text"]


def test_bedrock_client_error_becomes_generation_error() -> None:
    throttled = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Converse"
    )
    with pytest.raises(GenerationError, match="ThrottlingException"):
        _generate(FakeConverseClient(error=throttled))


def test_deepseek_tool_requires_the_direct_answer_field() -> None:
    client = FakeConverseClient({"answerable": False})
    generator = BedrockAnswerGenerator(client, "deepseek.v3.2", 900, "ap-southeast-2")
    result = generator.generate("What is a DST?", [], GenerationMode.STANDARD)
    schema = client.requests[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert set(schema["required"]) == {"answerable", "explanation", "key_considerations"}
    assert result.draft.answerable is False
    assert result.draft.explanation == []


def test_deepseek_dictionary_output_is_still_validated() -> None:
    client = FakeConverseClient({"explanation": "invalid"})
    generator = BedrockAnswerGenerator(client, "deepseek.v3.2", 900, "ap-southeast-2")
    with pytest.raises(GenerationError, match="malformed structured output"):
        generator.generate("What is a DST?", [], GenerationMode.STANDARD)


def test_schema_violating_output_becomes_generation_error() -> None:
    with pytest.raises(GenerationError, match="malformed"):
        _generate(FakeConverseClient({"answerable": "maybe", "explanation": "not a list"}))


def test_optional_considerations_can_be_omitted() -> None:
    result = _generate(
        FakeConverseClient(
            {
                "answerable": True,
                "explanation": [
                    {
                        "text": "Property must be identified within 45 days.",
                        "citations": [{"passage_id": "P1", "quote": "identified within 45 days"}],
                    }
                ],
            }
        )
    )
    assert result.draft.explanation
    assert result.draft.key_considerations == []
