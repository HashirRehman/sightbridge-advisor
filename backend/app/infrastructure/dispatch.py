import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import boto3
from botocore.config import Config

from app.domain.interfaces.dispatch import DispatchError

_local_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="answer-job")


class LambdaAnswerDispatcher:
    def __init__(self, function_name: str, region: str) -> None:
        self._name = function_name
        self._client = boto3.client(
            "lambda",
            region_name=region,
            config=Config(
                connect_timeout=1,
                read_timeout=3,
                retries={"total_max_attempts": 1},
            ),
        )

    def dispatch(self, payload: dict[str, Any]) -> None:
        try:
            response = self._client.invoke(
                FunctionName=self._name,
                InvocationType="Event",
                Payload=json.dumps(payload).encode(),
            )
            if response.get("StatusCode") != 202:
                raise DispatchError("worker not accepted")
        except Exception:
            raise DispatchError("worker not available") from None


class LocalAnswerDispatcher:
    def dispatch(self, payload: dict[str, Any]) -> None:
        from app.application.chat.worker import process_answer_event

        _local_workers.submit(process_answer_event, payload)
