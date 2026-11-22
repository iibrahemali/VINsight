"""The provider interface the agent loop is written against.

Each provider keeps the conversation in its own native format (thought signatures, thinking
blocks and tool-call ids differ between vendors and must be echoed back exactly), and exposes
just enough for the loop: add a user message, stream one model step, add tool results.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    content: Any  # JSON-serializable payload, or an error message when is_error
    is_error: bool = False


@dataclass
class Delta:
    """A piece of a step as it streams in."""

    kind: Literal["thought", "text", "notice"]
    text: str


@dataclass
class Step:
    """One finished model step. Its texts/thoughts were already streamed as Deltas."""

    texts: list[str] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: Literal["end", "tool_use", "refusal", "max_tokens"] = "end"
    usage: dict[str, int] = field(default_factory=dict)
    model: str | None = None


class LLMError(RuntimeError):
    def __init__(self, message: str, partial: bool = False):
        super().__init__(message)
        # True when some output had already streamed before the failure, so a retry elsewhere
        # would show the user a second, different beginning.
        self.partial = partial


class Conversation(Protocol):
    def add_user(self, text: str) -> None: ...

    def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None) -> AsyncIterator[Delta | Step]:
        """Run one model step: yield Deltas as they arrive, then exactly one Step, and append the
        model's turn to the history. `effort` ("low"/"medium"/"high") is a reasoning-depth hint that
        providers map to their own setting or ignore."""
        ...

    def add_tool_results(self, results: list[ToolResult]) -> None: ...


class Replayable(Conversation, Protocol):
    """A provider that can take another provider's turns into its native history."""

    def add_model_turn(self, step: Step) -> None: ...
