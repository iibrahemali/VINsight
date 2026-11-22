"""Claude via the Anthropic SDK."""

import json

import anthropic

from vinsight.config import settings
from vinsight.llm.base import Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeConversation:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.history: list[dict] = []

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        # Not token-streamed yet: Claude isn't offered in the UI's model picker. The finished step
        # is emitted as deltas so the loop and UI behave the same as for streaming providers.
        step = await self._generate(system, tools)
        for thought in step.thoughts:
            yield Delta("thought", thought)
        for text in step.texts:
            yield Delta("text", text)
        yield step

    async def _generate(self, system: str, tools: list[ToolSpec]) -> Step:
        try:
            response = await self._client.beta.messages.create(
                model=settings.vinsight_model,
                max_tokens=16000,
                system=system,
                tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
                messages=self.history,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": settings.vinsight_effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        except TypeError as e:  # the SDK raises this when no credentials are configured
            raise LLMError(f"Claude API not configured: {e}") from e

        # Keep the full content (thinking blocks included) so the next request is a valid continuation.
        self.history.append({"role": "assistant", "content": response.content})

        step = Step(
            model=response.model,
            usage={
                key: getattr(response.usage, key, 0) or 0
                for key in ("input_tokens", "output_tokens", "cache_read_input_tokens")
            },
        )
        for block in response.content:
            if block.type == "thinking" and block.thinking:
                step.thoughts.append(block.thinking)
            elif block.type == "text" and block.text:
                step.texts.append(block.text)
            elif block.type == "tool_use":
                step.tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        step.stop = {"refusal": "refusal", "max_tokens": "max_tokens"}.get(
            response.stop_reason, "tool_use" if step.tool_calls else "end"
        )
        return step

    def add_model_turn(self, step: Step) -> None:
        """Replay another provider's turn (after a fallback). Thinking from other models is dropped."""
        content: list[dict] = [{"type": "text", "text": t} for t in step.texts]
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in step.tool_calls]
        self.history.append({"role": "assistant", "content": content or [{"type": "text", "text": "…"}]})

    def add_tool_results(self, results: list[ToolResult]) -> None:
        blocks = []
        for r in results:
            block = {
                "type": "tool_result",
                "tool_use_id": r.call.id,
                "content": r.content if isinstance(r.content, str) else json.dumps(r.content),
            }
            if r.is_error:
                block["is_error"] = True
            blocks.append(block)
        self.history.append({"role": "user", "content": blocks})
