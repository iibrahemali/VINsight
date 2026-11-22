"""Drives the real agent loop and MCP server with a scripted stand-in for the model."""

import asyncio

from vinsight.agent import PERSONAS, Agent, ToolBox
from vinsight.llm import Delta, Step, ToolCall
from vinsight.mcp_server import server


class ScriptedConversation:
    """Implements vinsight.llm.Conversation by replaying canned steps and recording what it was sent."""

    def __init__(self, steps: list[Step]):
        self.steps = list(steps)
        self.requests: list[dict] = []
        self.tool_results: list[list] = []

    def add_user(self, text: str) -> None:
        pass

    async def stream(self, system, tools, effort=None):
        self.requests.append({"system": system, "tools": [t.name for t in tools]})
        step = self.steps.pop(0)
        for thought in step.thoughts:
            yield Delta("thought", thought)
        for text in step.texts:
            for word in text.split(" "):  # stream word by word, like a real provider
                yield Delta("text", word + " ")
        yield step

    def add_model_turn(self, step) -> None:
        pass

    def add_tool_results(self, results) -> None:
        self.tool_results.append(results)


def call(id, name, **input):
    return ToolCall(id=id, name=name, input=input)


async def run(steps, message="hi", persona="inspector"):
    convo = ScriptedConversation(steps)
    async with ToolBox(server) as tools:
        events = [e async for e in Agent(tools, convo).run(message, persona)]
    return events, convo


async def test_multi_hop_decode_then_parallel_lookups():
    events, convo = await run([
        Step(thoughts=["Decode first."], tool_calls=[call("t1", "decode_vin", vin="1HGCM82633A004352")], stop="tool_use"),
        Step(
            tool_calls=[
                call("t2", "get_recalls", make="HONDA", model="Accord", year=2003),
                call("t3", "get_complaints", make="HONDA", model="Accord", year=2003),
            ],
            stop="tool_use",
        ),
        Step(texts=["**Vehicle**: 2003 Honda Accord EX-V6 [vpic:1HGCM82633A004352]"], usage={"input_tokens": 50}),
    ])

    kinds = [(e.type, e.name) for e in events if e.type in ("tool_call", "tool_result")]
    assert kinds == [
        ("tool_call", "decode_vin"), ("tool_result", "decode_vin"),
        ("tool_call", "get_recalls"), ("tool_call", "get_complaints"),
        ("tool_result", "get_recalls"), ("tool_result", "get_complaints"),
    ]
    assert all(e.ok and e.ms is not None for e in events if e.type == "tool_result")
    assert events[-1].type == "done" and events[-1].persona == "inspector"
    # Both parallel results go back to the model together, in call order.
    assert [r.call.id for r in convo.tool_results[-1]] == ["t2", "t3"]


async def test_tool_error_is_returned_to_model_not_raised():
    events, convo = await run([
        Step(tool_calls=[call("t1", "decode_vin", vin="BADVIN")], stop="tool_use"),
        Step(texts=["That VIN is too short."]),
    ])
    assert not next(e for e in events if e.type == "tool_result").ok
    sent = convo.tool_results[0][0]
    assert sent.is_error and "17 characters" in sent.content


async def test_refusal_stops_cleanly():
    events, _ = await run([Step(stop="refusal")])
    assert [e.type for e in events] == ["error"]


async def test_each_persona_sees_only_its_tools():
    for persona in PERSONAS.values():
        _, convo = await run([Step(texts=["ok"])], persona=persona.id)
        assert set(convo.requests[0]["tools"]) == set(persona.tools)
        assert persona.instructions.splitlines()[0] in convo.requests[0]["system"]


async def test_persona_cannot_call_tools_outside_its_set():
    # Coach has no decode_vin; even if the model asks for it, the loop refuses.
    events, convo = await run(
        [Step(tool_calls=[call("t1", "decode_vin", vin="1HGCM82633A004352")], stop="tool_use"), Step(texts=["ok"])],
        persona="coach",
    )
    result = next(e for e in events if e.type == "tool_result")
    assert not result.ok and "not available" in result.output


async def test_text_streams_as_deltas():
    events, _ = await run([Step(texts=["Two words"])])
    assert [e.text for e in events if e.type == "text"] == ["Two ", "words "]


async def test_stopping_mid_tool_call_keeps_history_valid():
    """Cancelling while tools run must still record a result for every pending tool call."""
    convo = ScriptedConversation([Step(tool_calls=[call("t1", "decode_vin", vin="1HGCM82633A004352")], stop="tool_use")])
    async with ToolBox(server) as tools:
        agent = Agent(tools, convo)
        started = asyncio.Event()
        original = tools.call

        async def slow_call(c, turn):
            started.set()
            await asyncio.sleep(10)
            return await original(c, turn)

        tools.call = slow_call

        async def consume():
            async for _ in agent.run("check", "inspector"):
                pass

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    [results] = convo.tool_results
    assert results[0].is_error and "cancelled" in results[0].content


async def test_vin_lookups_run_in_code_before_the_model():
    from vinsight.llm import ConversationRouter

    convo = ScriptedConversation([Step(texts=["Verdict: CAUTION"])])
    router = ConversationRouter({"x": lambda: convo}, active="x")
    async with ToolBox(server) as tools:
        events = [e async for e in Agent(tools, router).run("Check 1HGCM82633A004352", "inspector", vin="1HGCM82633A004352")]
    calls = [e.name for e in events if e.type == "tool_call"]
    assert calls == ["decode_vin", "get_recalls", "get_complaints"]
    assert len(convo.requests) == 1  # the model answered in a single step


async def test_a_stalled_model_step_ends_the_turn(monkeypatch):
    from vinsight.agent import loop

    monkeypatch.setattr(loop.settings, "step_timeout_s", 0.05)

    class Stalled(ScriptedConversation):
        async def stream(self, system, tools, effort=None):
            await asyncio.sleep(10)
            yield Step()

    async with ToolBox(server) as tools:
        events = [e async for e in Agent(tools, Stalled([])).run("hi", "coach")]
    assert [e.type for e in events] == ["error"] and "too long" in events[0].text
