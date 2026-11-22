"""The Gemini adapter, offline: streamed parsing, history echo, model fallback, text replay."""

from types import SimpleNamespace as NS

import httpx
import pytest
from google.genai import errors, types

from vinsight.llm import Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec
from vinsight.llm.gemini import GeminiConversation

TOOLS = [ToolSpec(name="decode_vin", description="Decode a VIN", input_schema={"type": "object"})]


def chunk(*parts, finish=None, usage=None):
    return NS(
        candidates=[NS(content=types.Content(role="model", parts=list(parts)), finish_reason=finish)],
        usage_metadata=usage,
    )


USAGE = NS(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=2, cached_content_token_count=0)


class FakeClient:
    """Each outcome is an exception to raise when the stream opens, or a list of chunks to stream."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models_called = []
        self.aio = NS(models=NS(generate_content_stream=self._stream))

    async def _stream(self, model, contents, config):
        self.models_called.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        async def gen():
            for c in outcome:
                yield c

        return gen()


async def collect(convo, tools=TOOLS):
    items = [i async for i in convo.stream("sys", tools)]
    return [i for i in items if isinstance(i, Delta)], items[-1]


async def test_streams_thoughts_and_text_then_returns_step():
    fc = types.Part(function_call=types.FunctionCall(id="c1", name="decode_vin", args={"vin": "X"}))
    client = FakeClient([[
        chunk(types.Part(text="plan", thought=True)),
        chunk(types.Part(text="Check")),
        chunk(types.Part(text="ing."), fc, finish=types.FinishReason.STOP, usage=USAGE),
    ]])
    convo = GeminiConversation(client=client, models=["m1"])
    convo.add_user("check X")

    deltas, step = await collect(convo)

    assert [(d.kind, d.text) for d in deltas] == [("thought", "plan"), ("text", "Check"), ("text", "ing.")]
    assert isinstance(step, Step) and step.stop == "tool_use" and step.tool_calls[0].name == "decode_vin"
    assert step.usage == {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 0}
    assert len(convo.history[-1].parts) == 4  # every streamed part echoed back (signatures live on parts)

    convo.add_tool_results([ToolResult(call=step.tool_calls[0], content={"year": 2003})])
    fr = convo.history[-1].parts[0].function_response
    assert fr.name == "decode_vin" and fr.id == "c1" and fr.response == {"result": {"year": 2003}}


async def test_locally_generated_ids_are_not_echoed():
    fc = types.Part(function_call=types.FunctionCall(name="decode_vin", args={}))
    convo = GeminiConversation(client=FakeClient([[chunk(fc)]]), models=["m1"])
    convo.add_user("x")
    _, step = await collect(convo)
    convo.add_tool_results([ToolResult(call=step.tool_calls[0], content="bad", is_error=True)])
    fr = convo.history[-1].parts[0].function_response
    assert fr.id is None and fr.response == {"error": "bad"}


@pytest.mark.parametrize(
    "failure",
    [
        errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}}),
        errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
        httpx.ReadTimeout("stalled"),
    ],
    ids=["overloaded", "out-of-quota", "timeout"],
)
async def test_falls_back_to_next_model(failure):
    client = FakeClient([failure, [chunk(types.Part(text="hello"))]])
    convo = GeminiConversation(client=client, models=["first", "backup"])
    convo.add_user("hi")
    _, step = await collect(convo, [])
    assert client.models_called == ["first", "backup"] and step.model == "backup"


async def test_all_models_down_is_a_clean_error():
    overloaded = errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
    convo = GeminiConversation(client=FakeClient([overloaded]), models=["only"])
    convo.add_user("hi")
    with pytest.raises(LLMError, match="unavailable") as info:
        await collect(convo, [])
    assert not info.value.partial


async def test_replays_foreign_tool_turns_as_text():
    convo = GeminiConversation(client=FakeClient([]), models=["m1"])
    call = ToolCall(id="call_9", name="get_recalls", input={"make": "HONDA"})
    convo.add_user("check")
    convo.add_model_turn(Step(texts=["Looking it up."], tool_calls=[call]))
    convo.add_tool_results([ToolResult(call=call, content={"count": 24})])

    model_turn, results_turn = convo.history[-2], convo.history[-1]
    assert model_turn.role == "model" and "get_recalls" in model_turn.parts[0].text
    assert not any(p.function_call for p in model_turn.parts)  # no unsigned function calls
    assert results_turn.role == "user" and '"count": 24' in results_turn.parts[0].text
