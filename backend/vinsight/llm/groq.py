"""Groq (OpenAI-compatible chat completions): gpt-oss-120b, fast and free-tier friendly."""

import asyncio
import json
import logging
import math

import groq

from vinsight.config import settings
from vinsight.llm import limits
from vinsight.llm.base import Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec

log = logging.getLogger(__name__)


def _group_recalls(recalls: list[dict]) -> list[dict]:
    """Recalls by component, for the model: every campaign number stays (for citations), plus one
    consequence per group and the park-it / park-outside flags. Two dozen recalls go from ~2,400
    tokens to a few hundred."""
    groups: dict[str, dict] = {}
    for r in recalls:
        component = (r.get("component") or "OTHER").split(":")[0].strip()
        g = groups.setdefault(component, {"component": component, "campaigns": [], "consequence": r.get("consequence")})
        g["campaigns"].append(r.get("campaign_number"))
        g["consequence"] = g["consequence"] or r.get("consequence")
        for flag in ("park_it", "park_outside"):
            if r.get(flag):
                g[flag] = True
    return sorted(groups.values(), key=lambda g: (not g.get("park_it"), not g.get("park_outside"), -len(g["campaigns"])))


def _for_model(value):
    """The model's copy of a tool result: recalls grouped, URLs dropped (the UI keeps the full result)."""
    if isinstance(value, dict):
        out = {k: _for_model(v) for k, v in value.items() if k != "url"}
        if isinstance(value.get("recalls"), list) and value["recalls"] and isinstance(value["recalls"][0], dict):
            out["recalls"] = _group_recalls(value["recalls"])
        return out
    if isinstance(value, list):
        return [_for_model(v) for v in value]
    return value


# Fields that are the point of the result, not incidental prose: a read web page's text (already
# trimmed to the user's focus by read_link) must reach the model, or it re-reads the page in a loop.
_KEEP_LONG = {"text": 3000}


def _compact(value, max_str: int, key: str = ""):
    """Shrink a tool result for a tight token budget: drop empty/false/null fields and long-text tails.
    IDs and numbers are never touched, so citations still resolve."""
    if isinstance(value, dict):
        out = {k: _compact(v, max_str, k) for k, v in value.items() if v not in (None, False, "", [], {})}
        return {k: v for k, v in out.items() if v not in ("", [], {})}
    if isinstance(value, list):
        return [_compact(v, max_str, key) for v in value]
    limit = _KEEP_LONG.get(key, max_str)
    if isinstance(value, str) and len(value) > limit:
        return value[:limit].rsplit(" ", 1)[0] + "…"
    return value


def _estimate_tokens(payload) -> int:
    """Rough input size, for pacing: JSON-heavy text runs about 3.5 characters a token."""
    return int(len(json.dumps(payload, separators=(",", ":"))) / 3.5)


class GroqConversation:
    def __init__(self, client: groq.AsyncGroq | None = None, models: list[str] | None = None, api_key: str | None = None):
        # No SDK retries: it would wait out rate limits silently (sometimes for minutes). stream()
        # handles them itself, telling the user when it waits and moving on when the wait is long.
        self._client = client or groq.AsyncGroq(
            api_key=api_key or settings.groq_api_key, max_retries=0, timeout=settings.groq_timeout_s
        )
        self._models = models or settings.groq_models
        self._key = limits.key_id(api_key or settings.groq_api_key)
        self.history: list[dict] = []

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        messages = [{"role": "system", "content": system}, *self.history]
        tool_args = (
            {
                "tools": [
                    {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                    for t in tools
                ],
                "tool_choice": "auto",
            }
            if tools
            else {}  # Groq rejects tool_choice: null, so omit both when there are no tools
        )
        # Groq counts the input *and* the reserved reply against the per-minute budget.
        need = _estimate_tokens([messages, tool_args]) + settings.groq_max_completion_tokens
        response, model, last_error = None, None, None
        for model in self._models:
            async for item in self._open(model, messages, tool_args, need, effort):
                if isinstance(item, Delta):
                    yield item
                elif isinstance(item, Exception):
                    last_error = item
                else:
                    response = item
            if response is not None:
                break
        if response is None:
            raise self._give_up(last_error)

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

    async def _open(self, model: str, messages: list, tool_args: dict, need: int, effort: str | None):
        """Start a stream on one model, pacing to its per-minute budget. Yields notices, then either the
        stream or the exception that made this model give up (so the next model can be tried)."""
        for _ in range(3):
            if (out := limits.blocked(model, self._key)) and out[1] == "day":
                yield LLMError(f"{model} used its free daily quota", retry_in=out[0], scope="day")
                return
            # Wait for room this minute if it comes soon; a long wait is worse than another model.
            wait = max(limits.wait_for(model, self._key, need), out[0] if out else 0.0)
            if wait > settings.groq_max_wait_s:
                yield LLMError(f"{model} is busy for {wait:.0f}s", retry_in=wait, scope="minute")
                return
            if wait > 0:
                yield Delta("wait", str(math.ceil(wait)))
                await asyncio.sleep(wait)
            try:
                stream = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_completion_tokens=settings.groq_max_completion_tokens,
                    stream=True,
                    **tool_args,
                    **({"reasoning_effort": effort} if effort and model.startswith("openai/gpt-oss") else {}),
                    # Qwen otherwise puts its reasoning inline in <think> tags; ask for it separately.
                    **({"reasoning_format": "parsed"} if model.startswith("qwen/") else {}),
                )
                limits.note_headers(model, self._key, getattr(getattr(stream, "response", None), "headers", None))
                yield stream
                return
            except groq.RateLimitError as e:
                scope, seconds = limits.limit_scope(e), limits.retry_in(e)
                log.info("groq %s rate-limited (%s, retry in %s): %s", model, scope, seconds, e)
                limits.block(model, self._key, seconds if seconds is not None else (3600 if scope == "day" else 20), scope)
                if scope == "day":
                    yield LLMError(str(e), retry_in=seconds, scope="day")
                    return
                # A per-minute limit: loop round, which waits (visibly) if the wait is short.
            except groq.APIStatusError as e:
                if e.status_code == 413:  # bigger than this model's per-minute budget: try the next
                    yield LLMError(f"request too large for {model}: {e.message}", scope="minute")
                    return
                if isinstance(e, groq.InternalServerError):
                    yield e
                    return
                raise LLMError(f"Groq rejected the request: {e.message}") from e
            except (groq.APITimeoutError, groq.APIConnectionError) as e:
                yield e
                return
        out = limits.blocked(model, self._key)
        yield LLMError(f"{model} stayed busy", retry_in=out[0] if out else None, scope="minute")

    def _give_up(self, error: Exception | None) -> LLMError:
        if isinstance(error, LLMError):
            # Report the soonest any of the models frees up, and "day" only if every one is out for the day.
            outs = [limits.blocked(m, self._key) for m in self._models]
            scope = "day" if outs and all(o and o[1] == "day" for o in outs) else "minute"
            waits = [o[0] for o in outs if o]
            return LLMError(f"all Groq models unavailable or rate-limited: {error}", retry_in=min(waits) if waits else error.retry_in, scope=scope)
        return LLMError(f"all Groq models unavailable ({type(error).__name__}: {error})")

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
            payload = r.content if isinstance(r.content, str) else _compact(_for_model(r.content), settings.groq_max_text_chars)
            content = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
            self.history.append(
                {"role": "tool", "tool_call_id": r.call.id, "content": f"ERROR: {content}" if r.is_error else content}
            )
