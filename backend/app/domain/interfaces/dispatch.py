from typing import Any, Protocol


class DispatchError(Exception):
    pass


class AnswerDispatcher(Protocol):
    def dispatch(self, payload: dict[str, Any]) -> None: ...
