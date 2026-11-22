"""Groq (OpenAI-compatible chat completions): gpt-oss-120b, fast and free-tier friendly."""

import json

import groq

from vinsight.config import settings
from vinsight.llm.base import Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec


def _compact(value, max_str: int):
    """Shrink a tool result for a tight token budget: drop empty/false/null fields and long-text tails.
    IDs and numbers are never touched, so citations still resolve."""
    if isinstance(value, dict):
        out = {k: _compact(v, max_str) for k, v in value.items() if v not in (None, False, "", [], {})}
        return {k: v for k, v in out.items() if v not in ("", [], {})}
    if isinstance(value, list):
        return [_compact(v, max_str) for v in value]
    if isinstance(value, str) and len(value) > max_str:
        return value[:max_str].rsplit(" ", 1)[0] + "…"
    return value


class GroqConversation:
    def __init__(self, client: groq.AsyncGroq | None = None, models: list[str] | None = None):
        # The SDK honours retry-after on 429s, so retries wait out the free tier's per-minute budget.
        self._client = client or groq.AsyncGroq(
            api_key=settings.groq_api_key, max_retries=settings.groq_max_retries, timeout=settings.groq_timeout_s
        )
        self._models = models or settings.groq_models
        self.history: list[dict] = []

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        last_error: Exception | None = None
        for model in self._models:
            try:
                tool_args = (
                    {
                        "tools": [
                            {
                                "type": "function",
                                "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                            }
                            for t in tools
                        ],
                        "tool_choice": "auto",
                    }
                    if tools
                    else {}  # Groq rejects tool_choice: null, so omit both when there are no tools
                )
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, *self.history],
                    # Groq counts the reserved completion against the per-minute budget, so keep it modest.
                    max_completion_tokens=settings.groq_max_completion_tokens,
                    stream=True,
                    **tool_args,
                    **({"reasoning_effort": effort} if effort and model.startswith("openai/gpt-oss") else {}),
                )
                break
            except (groq.RateLimitError, groq.InternalServerError, groq.APITimeoutError, groq.APIConnectionError) as e:
                last_error = e
            except groq.APIStatusError as e:
                raise LLMError(f"Groq rejected the request: {e.message}") from e
        else:
            raise LLMError(f"all Groq models unavailable or rate-limited ({type(last_error).__name__}: {last_error})")

        step = Step(model=model)
        text, reasoning, finish = [], [], None
        calls: dict[int, dict] = {}  # tool calls arrive in fragments, keyed by index
        try:
            async for chunk in response:
                usage = chunk.usage or getattr(chunk.x_groq, "usage", None)
                if usage:
                    step.usage = {"input_tokens": usage.prompt_tokens or 0, "output_tokens": usage.completion_tokens or 0}
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                delta = choice.delta
                if getattr(delta, "reasoning", None):
                    reasoning.append(delta.reasoning)
                    yield Delta("thought", delta.reasoning)
                if delta.content:
                    text.append(delta.content)
                    yield Delta("text", delta.content)
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function:
                        slot["name"] += tc.function.name or ""
                        slot["args"] += tc.function.arguments or ""
        except groq.APIError as e:
            raise LLMError(f"Groq stream broke off: {e}", partial=bool(text or reasoning)) from e

        if reasoning:
            step.thoughts.append("".join(reasoning))
        if text:
            step.texts.append("".join(text))
        for i in sorted(calls):
            slot = calls[i]
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            step.tool_calls.append(ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"], input=args))

        self.add_model_turn(step)
        if step.tool_calls:
            step.stop = "tool_use"
        elif finish == "length":
            step.stop = "max_tokens"
        elif finish == "content_filter":
            step.stop = "refusal"
        yield step

    def add_model_turn(self, step: Step) -> None:
        """Append a model turn. Also used to replay another provider's turns."""
        turn: dict = {"role": "assistant", "content": "\n\n".join(step.texts) or None}
        if step.tool_calls:
            turn["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                for c in step.tool_calls
            ]
        self.history.append(turn)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            # Compact separators and a trimmed view: on an 8K tokens-per-minute budget it matters.
            payload = r.content if isinstance(r.content, str) else _compact(r.content, settings.groq_max_text_chars)
            content = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
            self.history.append(
                {"role": "tool", "tool_call_id": r.call.id, "content": f"ERROR: {content}" if r.is_error else content}
            )
