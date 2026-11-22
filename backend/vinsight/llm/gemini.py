"""Gemini via the google-genai SDK (Google AI Studio API key)."""

import asyncio
import inspect
import json
import uuid

import httpx
from google import genai
from google.genai import errors, types

from vinsight.config import settings
from vinsight.llm.base import Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec

_LOCAL_ID = "local-"


def _http_options() -> types.HttpOptions:
    # Under load Gemini either 503s or stalls. Retry once, cap the whole request generously (answers
    # stream for a while), and separately cap the wait for the first chunk in stream().
    return types.HttpOptions(
        timeout=int(settings.gemini_stream_timeout_s * 1000),
        retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=4.0),
    )


class GeminiConversation:
    def __init__(self, client: genai.Client | None = None, models: list[str] | None = None):
        self._client = client or genai.Client(api_key=settings.gemini_api_key, http_options=_http_options())
        self._models = models or settings.gemini_models
        self.history: list[types.Content] = []
        # Tool calls replayed from another provider. Gemini can't accept function calls it didn't
        # sign, so those turns and their results are replayed as plain text instead.
        self._text_replayed_ids: set[str] = set()

    def add_user(self, text: str) -> None:
        self.history.append(types.Content(role="user", parts=[types.Part(text=text)]))

    def _config(self, system: str, tools: list[ToolSpec]) -> types.GenerateContentConfig:
        declarations = [
            types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.input_schema)
            for t in tools
        ]
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)] if declarations else None,
            # The loop runs tools itself (over MCP) so it can trace and time each call.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )

    async def _open(self, model: str, config: types.GenerateContentConfig):
        """Start a stream and wait for its first chunk, so a stalled model fails fast."""
        stream = self._client.aio.models.generate_content_stream(model=model, contents=self.history, config=config)
        if inspect.isawaitable(stream):
            stream = await stream
        iterator = stream.__aiter__()
        first = await asyncio.wait_for(iterator.__anext__(), timeout=settings.gemini_timeout_s)
        return first, iterator

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        config = self._config(system, tools)
        last_error: Exception | None = None
        for model in self._models:
            try:
                first, iterator = await self._open(model, config)
                break
            except (errors.ServerError, httpx.TimeoutException, asyncio.TimeoutError, StopAsyncIteration) as e:
                last_error = e  # overloaded, stalled or empty: try the next model
            except errors.ClientError as e:
                if e.code == 429:  # this model's quota is used up; another model has its own
                    last_error = e
                    continue
                raise LLMError(f"Gemini rejected the request: {e.message or e}") from e
        else:
            raise LLMError(
                f"all Gemini models unavailable, out of quota or timed out ({type(last_error).__name__}: {last_error})"
            )

        step = Step(model=model)
        parts: list[types.Part] = []
        finish = None
        streamed = False
        chunk = first
        try:
            while True:
                if u := chunk.usage_metadata:
                    step.usage = {
                        "input_tokens": u.prompt_token_count or 0,
                        "output_tokens": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
                        "cache_read_input_tokens": u.cached_content_token_count or 0,
                    }
                candidate = (chunk.candidates or [None])[0]
                if candidate is not None:
                    finish = candidate.finish_reason or finish
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        parts.append(part)
                        if part.text and not part.function_call:
                            streamed = True
                            yield Delta("thought" if part.thought else "text", part.text)
                try:
                    chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
        except (errors.APIError, httpx.HTTPError) as e:
            raise LLMError(f"Gemini stream broke off: {e}", partial=streamed) from e

        if not parts:
            step.stop = "refusal" if finish in _BLOCKED else "end"
            yield step
            return

        # Append every part untouched: Gemini 3 requires its thought signatures echoed back.
        self.history.append(types.Content(role="model", parts=parts))
        for part in parts:
            if part.function_call:
                fc = part.function_call
                call_id = fc.id or f"{_LOCAL_ID}{uuid.uuid4().hex}"
                step.tool_calls.append(ToolCall(id=call_id, name=fc.name, input=dict(fc.args or {})))
            elif part.text and part.thought:
                step.thoughts.append(part.text)
            elif part.text:
                step.texts.append(part.text)

        if step.tool_calls:
            step.stop = "tool_use"
        elif finish == types.FinishReason.MAX_TOKENS:
            step.stop = "max_tokens"
        elif finish in _BLOCKED:
            step.stop = "refusal"
        yield step

    def add_model_turn(self, step: Step) -> None:
        """Replay another provider's turn as text (see _text_replayed_ids)."""
        lines = list(step.texts)
        for call in step.tool_calls:
            lines.append(f"[I called the tool {call.name} with {json.dumps(call.input)}]")
            self._text_replayed_ids.add(call.id)
        self.history.append(types.Content(role="model", parts=[types.Part(text="\n".join(lines) or "…")]))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        native, replayed = [], []
        for r in results:
            if r.call.id in self._text_replayed_ids:
                label = "error" if r.is_error else "result"
                replayed.append(f"[Tool {label} for {r.call.name}: {json.dumps(r.content)}]")
                continue
            response = {"error": r.content} if r.is_error else {"result": r.content}
            part = types.Part.from_function_response(name=r.call.name, response=json.loads(json.dumps(response)))
            if not r.call.id.startswith(_LOCAL_ID):  # only echo ids Gemini itself issued
                part.function_response.id = r.call.id
            native.append(part)
        if native:
            self.history.append(types.Content(role="user", parts=native))
        if replayed:
            self.history.append(types.Content(role="user", parts=[types.Part(text="\n".join(replayed))]))


_BLOCKED = (types.FinishReason.SAFETY, types.FinishReason.PROHIBITED_CONTENT, types.FinishReason.BLOCKLIST)
