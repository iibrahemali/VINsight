"""The agent loop: the model plans, calls VINsight's MCP tools, and synthesizes a cited answer.

The loop is hand-written on purpose. Each step emits a `TraceEvent`, so the CLI, the web UI and
the eval harness all see the same reasoning trace with per-step latency. It is written against
`vinsight.llm.Conversation`, so it runs unchanged on Gemini, GPT-OSS (Groq) or Claude, and it
streams: thinking and answer text reach the UI token by token.
"""

import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from mcp import Client, StdioServerParameters
from pydantic import BaseModel

from vinsight.agent.prompts import DEFAULT_PERSONA, PERSONAS
from vinsight.config import settings
from vinsight.llm import Conversation, ConversationRouter, LLMError, Step, ToolCall, ToolResult, ToolSpec, new_conversation
from vinsight.llm.base import friendly_error

log = logging.getLogger(__name__)


class TraceEvent(BaseModel):
    type: Literal["notice", "thinking", "tool_call", "tool_result", "text", "error", "done", "profile"]
    turn: int
    name: str | None = None
    input: dict[str, Any] | None = None
    output: Any = None
    text: str | None = None
    ok: bool = True
    ms: float | None = None
    usage: dict[str, int] | None = None
    model: str | None = None
    persona: str | None = None


def _mcp_server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "vinsight.mcp_server"])


class ToolBox:
    """A live connection to the VINsight MCP server, shareable by many conversations."""

    def __init__(self, mcp_server: Any = None):
        # mcp_server may be an in-process MCPServer (tests) or anything mcp.Client accepts.
        self._mcp = Client(mcp_server or _mcp_server_params())
        self.specs: list[ToolSpec] = []

    async def __aenter__(self) -> "ToolBox":
        await self._mcp.__aenter__()
        listed = await self._mcp.list_tools()
        self.specs = [
            ToolSpec(name=t.name, description=t.description or "", input_schema=t.input_schema)
            for t in sorted(listed.tools, key=lambda t: t.name)  # stable order keeps prompt caches warm
        ]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._mcp.__aexit__(*exc)

    def subset(self, names: frozenset[str]) -> list[ToolSpec]:
        return [t for t in self.specs if t.name in names]

    async def call(self, call: ToolCall, turn: int) -> tuple[ToolResult, TraceEvent]:
        start = time.perf_counter()
        try:
            result = await self._mcp.call_tool(call.name, call.input)
            ok = not result.is_error
            if result.structured_content is not None:
                payload = result.structured_content
            else:
                payload = "\n".join(c.text for c in result.content if c.type == "text")
        except Exception as e:  # transport failure; report it to the model rather than crash the turn
            ok, payload = False, f"tool transport error: {e}"
        ms = (time.perf_counter() - start) * 1000
        event = TraceEvent(type="tool_result", turn=turn, name=call.name, output=payload, ok=ok, ms=ms)
        return ToolResult(call=call, content=payload, is_error=not ok), event


class Prefetch(BaseModel):
    """A tool call the app runs before the model's first step, because its order is fixed (e.g.
    X-raying an attached listing). `shown_input` is what the model sees in its history, so a long
    listing isn't stored twice."""

    name: str
    input: dict[str, Any]
    shown_input: dict[str, Any] | None = None


class Agent:
    """One conversation, which can move between personas and models. Many agents can share a ToolBox."""

    def __init__(self, tools: ToolBox, conversation: ConversationRouter | Conversation | None = None):
        self.tools = tools
        self.conversation = conversation or new_conversation()

    async def run(
        self,
        user_message: str,
        persona: str = DEFAULT_PERSONA,
        provider: str | None = None,
        prefetch: list[Prefetch] | None = None,
        vin: str | None = None,
    ) -> AsyncIterator[TraceEvent]:
        """Run one user turn. `thinking` and `text` events are streamed deltas, not whole blocks."""
        p = PERSONAS.get(persona, PERSONAS[DEFAULT_PERSONA])
        allowed = self.tools.subset(p.tools)
        if provider and isinstance(self.conversation, ConversationRouter):
            self.conversation.use(provider)
        self.conversation.add_user(user_message)
        usage: dict[str, int] = {}
        model = None
        started = time.perf_counter()
        pending: list[ToolCall] = []  # tool calls the model made whose results aren't recorded yet

        try:
            if isinstance(self.conversation, ConversationRouter):
                if prefetch:
                    async for event in self._prefetch(prefetch, "attach"):
                        yield event
                if vin:
                    # A VIN check always runs the same steps, so code runs them, not the model: decode,
                    # then recalls + complaints together. The model then answers in one step, which also
                    # keeps the request small enough for Groq's free per-minute token limit.
                    decoded: list[ToolResult] = []
                    async for event in self._prefetch([Prefetch(name="decode_vin", input={"vin": vin})], "vin", decoded):
                        yield event
                    v = decoded[0].content if decoded and not decoded[0].is_error else None
                    if isinstance(v, dict) and v.get("make") and v.get("model") and v.get("year"):
                        car = {"make": v["make"], "model": v["model"], "year": v["year"]}
                        lookups = [Prefetch(name="get_recalls", input=car), Prefetch(name="get_complaints", input=car)]
                        async for event in self._prefetch(lookups, "car"):
                            yield event

            for turn in range(settings.vinsight_max_turns):
                t0 = time.perf_counter()
                step: Step | None = None
                try:
                    # Watchdog: a stalled stream must end the turn with a message, never hang it.
                    async with asyncio.timeout(settings.step_timeout_s):
                        async for item in self.conversation.stream(p.system_prompt, allowed, p.effort):
                            if isinstance(item, Step):
                                step = item
                            elif item.kind == "notice":
                                yield TraceEvent(type="notice", turn=turn, text=item.text)
                            else:
                                yield TraceEvent(type="thinking" if item.kind == "thought" else "text", turn=turn, text=item.text)
                except TimeoutError:
                    log.warning("model step timed out after %ss", settings.step_timeout_s)
                    yield TraceEvent(type="error", turn=turn, text="The model took too long to answer. Please try again.", ok=False)
                    return
                except LLMError as e:
                    log.warning("no model could answer: %s", e)
                    yield TraceEvent(type="error", turn=turn, text=friendly_error(e), ok=False, persona=p.id)
                    return
                assert step is not None, "provider stream ended without a Step"
                model = step.model or model
                for key, value in step.usage.items():
                    usage[key] = usage.get(key, 0) + value

                if step.stop == "refusal":
                    yield TraceEvent(type="error", turn=turn, text="The model declined this request.", ok=False)
                    return
                if step.stop == "max_tokens":
                    yield TraceEvent(type="error", turn=turn, text="Response hit the token limit.", ok=False)
                    return
                if not step.tool_calls:
                    break

                pending = list(step.tool_calls)
                for call in step.tool_calls:
                    yield TraceEvent(type="tool_call", turn=turn, name=call.name, input=call.input,
                                     ms=(time.perf_counter() - t0) * 1000)
                # Independent calls (e.g. recalls + complaints) run concurrently; results go back together.
                outcomes = await asyncio.gather(*(self._call(call, allowed, turn) for call in step.tool_calls))
                for _, event in outcomes:
                    yield event
                self.conversation.add_tool_results([r for r, _ in outcomes])
                pending = []
            else:
                yield TraceEvent(type="error", turn=turn, text="Stopped after the maximum number of steps.", ok=False)

            yield TraceEvent(
                type="done", turn=turn, ms=(time.perf_counter() - started) * 1000, usage=usage, model=model, persona=p.id
            )
        finally:
            # Stopped by the user (task cancelled / stream closed) while tools were running: record
            # them as cancelled so the history stays valid and the next message can continue it.
            if pending:
                self.conversation.add_tool_results(
                    [ToolResult(call=c, content="cancelled: the user stopped this answer", is_error=True) for c in pending]
                )

    async def _prefetch(
        self, items: list[Prefetch], tag: str, collect: list[ToolResult] | None = None
    ) -> AsyncIterator[TraceEvent]:
        """Run tool calls before the model's first step and record them as if the model had made them."""
        runs = [(f, ToolCall(id=f"prefetch-{tag}-{i}", name=f.name, input=f.input)) for i, f in enumerate(items)]
        for f, call in runs:
            yield TraceEvent(type="tool_call", turn=0, name=f.name, input=f.shown_input or f.input)
        outcomes = await asyncio.gather(*(self.tools.call(call, 0) for _, call in runs))
        for _, event in outcomes:
            yield event
        shown = [ToolCall(id=c.id, name=c.name, input=f.shown_input or f.input) for f, c in runs]
        results = [ToolResult(call=sc, content=r.content, is_error=r.is_error) for sc, (r, _) in zip(shown, outcomes)]
        self.conversation.add_prefetched(Step(tool_calls=shown, stop="tool_use"), results)
        if collect is not None:
            collect.extend(results)

    async def _call(self, call: ToolCall, allowed: list[ToolSpec], turn: int) -> tuple[ToolResult, TraceEvent]:
        # The model only sees this persona's tools, but enforce it here too rather than trust that.
        if call.name not in {t.name for t in allowed}:
            msg = f"tool {call.name!r} is not available to this persona"
            return ToolResult(call=call, content=msg, is_error=True), TraceEvent(
                type="tool_result", turn=turn, name=call.name, output=msg, ok=False, ms=0
            )
        return await self.tools.call(call, turn)
