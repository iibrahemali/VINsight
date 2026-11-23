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

    kind: Literal["thought", "text", "notice", "wait"]  # wait: pacing to a rate limit; text is the seconds
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
    def __init__(self, message: str, partial: bool = False, retry_in: float | None = None, scope: str = ""):
        super().__init__(message)
        # True when some output had already streamed before the failure, so a retry elsewhere
        # would show the user a second, different beginning.
        self.partial = partial
        # For rate limits: how long until the model has room again, and whether that's this minute's
        # budget or the day's ("minute" / "day").
        self.retry_in = retry_in
        self.scope = scope


def _kind(error: Exception) -> str:
    text = str(error).lower()
    if any(k in text for k in ("429", "rate limit", "rate-limit", "quota", "resource_exhausted", "413")):
        return "limit"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "other"


def short_reason(error: Exception) -> str:
    """A few words for why a model failed, for user-facing notices (details go to the log)."""
    if _kind(error) == "limit":
        return "has used up its free quota for today" if getattr(error, "scope", "") == "day" else "is busy this minute"
    return {"timeout": "didn't respond in time"}.get(_kind(error), "is unavailable right now")


def friendly_error(error: Exception) -> str:
    """What to tell the user when no model could answer."""
    kind = _kind(error)
    if kind == "limit":
        if getattr(error, "scope", "") == "day":
            return ("The free AI quota for today is used up. It resets within a day; "
                    "or add your own free API key under API keys.")
        wait = getattr(error, "retry_in", None)
        when = f"in about {max(1, round(wait))} seconds" if wait else "in a minute"
        return f"The free AI models are busy right now. Try again {when}, or add your own free API key under API keys."
    if kind == "timeout":
        return "The model didn't respond in time. Please try again."
    return "The model couldn't answer this time. Please try again."


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
