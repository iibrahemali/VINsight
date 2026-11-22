"""Groq streaming adapter and the provider router (model switching + fallback), offline."""

import json
from types import SimpleNamespace as NS

import groq
import httpx
import pytest

from vinsight.llm import ConversationRouter, Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec
from vinsight.llm.groq import GroqConversation

TOOLS = [ToolSpec(name="decode_vin", description="Decode a VIN", input_schema={"type": "object"})]


def chunk(content=None, reasoning=None, tool_calls=None, finish=None, usage=None):
    delta = NS(content=content, reasoning=reasoning, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage, x_groq=None)


def tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


def rate_limited(retry_after: str | None = None):
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, headers=headers, request=httpx.Request("POST", "https://api.groq.com"))
    return groq.RateLimitError("rate limited", response=response, body=None)


class FakeGroq:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        async def gen():
            for c in outcome:
                yield c

        return gen()


async def collect(convo):
    items = [i async for i in convo.stream("sys", TOOLS)]
    return [i for i in items if isinstance(i, Delta)], items[-1]


async def test_groq_stitches_streamed_tool_call_fragments():
    client = FakeGroq([[
        chunk(reasoning="Need to "), chunk(reasoning="decode."),
        chunk(tool_calls=[tc(0, id="call_1", name="decode_vin", args='{"vi')]),
        chunk(tool_calls=[tc(0, args='n": "X"}')], finish="tool_calls", usage=NS(prompt_tokens=10, completion_tokens=3)),
    ]])
    convo = GroqConversation(client=client, models=["m1"])
    convo.add_user("check X")

    deltas, step = await collect(convo)
    assert [d.text for d in deltas] == ["Need to ", "decode."]
    assert step.stop == "tool_use" and step.tool_calls == [ToolCall(id="call_1", name="decode_vin", input={"vin": "X"})]
    assert client.calls[0]["stream"] is True

    convo.add_tool_results([ToolResult(call=step.tool_calls[0], content={"year": 2003, "trim": None})])
    assert convo.history[-1] == {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"year": 2003}, separators=(",", ":"))}


async def test_groq_omits_tool_fields_without_tools():
    client = FakeGroq([[chunk(content="hi")]])
    convo = GroqConversation(client=client, models=["m1"])
    convo.add_user("hi")
    [_ async for _ in convo.stream("sys", [])]
    assert "tools" not in client.calls[0] and "tool_choice" not in client.calls[0]


async def test_groq_moves_to_next_model_when_rate_limited():
    client = FakeGroq([rate_limited(), [chunk(content="hi")]])
    _, step = await collect(GroqConversation(client=client, models=["a", "b"]))
    assert [c["model"] for c in client.calls] == ["a", "b"] and step.model == "b"


class Scripted:
    """A Replayable provider stub: replays steps, records everything it's given."""

    def __init__(self, steps=None, fail=None):
        self.steps, self.fail = list(steps or []), fail
        self.received: list[tuple] = []

    def add_user(self, text):
        self.received.append(("user", text))

    def add_model_turn(self, step):
        self.received.append(("model", step))

    def add_tool_results(self, results):
        self.received.append(("tools", results))

    async def stream(self, system, tools, effort=None):
        if self.fail:
            raise self.fail
        step = self.steps.pop(0)
        for t in step.texts:
            yield Delta("text", t)
        yield step


async def drain(router):
    return [i async for i in router.stream("sys", TOOLS)]


async def test_switching_models_carries_the_conversation_over():
    gemini = Scripted([Step(tool_calls=[ToolCall(id="c1", name="decode_vin", input={})], stop="tool_use")])
    groq_stub = Scripted([Step(texts=["answer"])])
    router = ConversationRouter({"gemini": lambda: gemini, "groq": lambda: groq_stub}, active="gemini")

    router.add_user("check")
    step = (await drain(router))[-1]
    router.add_tool_results([ToolResult(call=step.tool_calls[0], content={"ok": 1})])
    router.use("groq")

    assert [kind for kind, _ in groq_stub.received] == ["user", "model", "tools"]
    assert (await drain(router))[-1].texts == ["answer"] and router.active == "groq"


async def test_falls_back_when_active_provider_fails_before_output():
    router = ConversationRouter(
        {"gemini": lambda: Scripted(fail=LLMError("overloaded")), "groq": lambda: Scripted([Step(texts=["hi"])])},
        active="gemini",
    )
    router.add_user("hi")
    items = await drain(router)
    assert items[0].kind == "notice" and "Switched to GPT-OSS" in items[0].text
    assert items[-1].texts == ["hi"] and router.active == "groq"


async def test_no_fallback_after_partial_output():
    router = ConversationRouter(
        {"gemini": lambda: Scripted(fail=LLMError("broke off", partial=True)), "groq": lambda: Scripted([Step()])},
        active="gemini",
    )
    router.add_user("hi")
    with pytest.raises(LLMError, match="broke off"):
        await drain(router)


async def test_groq_short_rate_limit_waits_visibly_then_retries_same_model():
    client = FakeGroq([rate_limited("0"), [chunk(content="hi")]])
    convo = GroqConversation(client=client, models=["a", "b"])
    convo.add_user("hi")
    items = [i async for i in convo.stream("sys", [])]
    assert items[0].kind == "notice" and "Retrying" in items[0].text
    assert [c["model"] for c in client.calls] == ["a", "a"] and items[-1].model == "a"


async def test_groq_long_rate_limit_moves_on_without_waiting():
    client = FakeGroq([rate_limited("300"), [chunk(content="hi")]])
    convo = GroqConversation(client=client, models=["a", "b"])
    convo.add_user("hi")
    items = [i async for i in convo.stream("sys", [])]
    assert [c["model"] for c in client.calls] == ["a", "b"] and not any(getattr(i, "kind", "") == "notice" for i in items)


async def test_failure_notices_are_short_and_friendly():
    router = ConversationRouter(
        {"gemini": lambda: Scripted(fail=LLMError("429 RESOURCE_EXHAUSTED {'error': {...a very long json...}}")),
         "groq": lambda: Scripted([Step(texts=["hi"])])},
        active="gemini",
    )
    router.add_user("hi")
    notice = (await drain(router))[0].text
    assert notice == "Gemini is over its free-tier limit. Switched to GPT-OSS." and "{" not in notice
