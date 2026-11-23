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
    client = FakeGroq([rate_limited("300"), [chunk(content="hi")]])
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
    client = FakeGroq([rate_limited("1"), [chunk(content="hi")]])
    convo = GroqConversation(client=client, models=["a", "b"])
    convo.add_user("hi")
    items = [i async for i in convo.stream("sys", [])]
    assert items[0].kind == "wait" and items[0].text == "1"
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
    assert notice == "Gemini is busy this minute. Switched to GPT-OSS." and "{" not in notice


async def test_fallback_keeps_going_until_a_provider_answers():
    router = ConversationRouter(
        {"gpt": lambda: Scripted(fail=LLMError("429 tokens per day")),
         "gemini": lambda: Scripted(fail=LLMError("429 quota")),
         "qwen": lambda: Scripted([Step(texts=["hello from qwen"])])},
        active="gpt",
    )
    router.add_user("hi")
    items = await drain(router)
    notices = [i.text for i in items if getattr(i, "kind", "") == "notice"]
    assert len(notices) == 2 and items[-1].texts == ["hello from qwen"] and router.active == "qwen"


async def test_rate_limited_model_is_skipped_on_the_next_turn():
    gpt_calls = []

    def gpt():
        gpt_calls.append(1)
        return Scripted(fail=LLMError("429 rate limit"))

    router = ConversationRouter({"gpt": gpt, "qwen": lambda: Scripted([Step(texts=["a"]), Step(texts=["b"])])}, active="gpt")
    router.add_user("hi")
    await drain(router)
    router.use("gpt")  # the user's picker still says gpt
    router.add_user("again")
    items = await drain(router)
    # A fresh qwen instance answers; the rate-limited model is not streamed from again.
    assert items[0].kind == "notice" and "busy right now" in items[0].text and items[-1].texts == ["a"]
    assert router.active == "qwen"


def rate_limited_daily():
    response = httpx.Response(429, headers={"retry-after": "3000"}, request=httpx.Request("POST", "https://api.groq.com"))
    return groq.RateLimitError("Rate limit reached for model `a` on tokens per day (TPD): Limit 200000", response=response, body=None)


async def test_daily_limit_is_remembered_across_chats():
    first = FakeGroq([rate_limited_daily(), [chunk(content="hi")]])
    await collect(GroqConversation(client=first, models=["a", "b"]))
    second = FakeGroq([[chunk(content="hi again")]])
    _, step = await collect(GroqConversation(client=second, models=["a", "b"]))
    # The new chat doesn't spend a request (or the user's time) on the model that's out for the day.
    assert [c["model"] for c in second.calls] == ["b"] and step.model == "b"


async def test_paces_when_the_minute_budget_is_nearly_spent(monkeypatch):
    from vinsight.llm import limits

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("vinsight.llm.groq.asyncio.sleep", fake_sleep)
    limits.note_headers("a", limits.key_id("k"), {"x-ratelimit-remaining-tokens": "500", "x-ratelimit-reset-tokens": "9s"})
    # No per-minute limit in the headers: wait for the full reset.
    client = FakeGroq([[chunk(content="hi")]])
    convo = GroqConversation(client=client, models=["a"], api_key="k")
    convo.add_user("hi")
    items = [i async for i in convo.stream("sys", [])]
    assert items[0].kind == "wait" and 8 < slept[0] <= 9
    assert [c["model"] for c in client.calls] == ["a"]


async def test_all_busy_error_says_when_to_retry():
    from vinsight.llm.base import friendly_error

    client = FakeGroq([rate_limited("300"), rate_limited("200")])
    convo = GroqConversation(client=client, models=["a", "b"])
    convo.add_user("hi")
    with pytest.raises(LLMError) as e:
        await collect(convo)
    assert e.value.scope == "minute" and 190 < e.value.retry_in <= 200
    assert "busy" in friendly_error(e.value) and "seconds" in friendly_error(e.value)


def test_pacing_waits_only_for_the_missing_tokens():
    from vinsight.llm import limits

    limits.note_headers("m", "k", {"x-ratelimit-remaining-tokens": "1000", "x-ratelimit-limit-tokens": "8000",
                                   "x-ratelimit-reset-tokens": "52s"})
    # 8000 a minute refills ~133 a second: 2000 more tokens take ~15 s, not the full 52 s reset.
    assert 14 < limits.wait_for("m", "k", 3000) <= 15.1
    assert limits.wait_for("m", "k", 900) == 0


def test_compaction_keeps_a_read_page_text_but_trims_incidental_prose():
    from vinsight.llm.groq import _compact

    out = _compact({"text": "word " * 400, "summary": "word " * 400}, 110)
    assert len(out["text"]) > 1900 and len(out["summary"]) <= 111
