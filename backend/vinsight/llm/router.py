"""One conversation that can move between providers: because the user picked another model, or
because the active one failed and a backup took over.

Providers keep history in incompatible native formats, so the router keeps a provider-neutral
log and rebuilds a provider's history from it whenever that provider becomes active. Turns the
provider wrote itself stay native only while it remains active; after a switch, everything is
replayed through `add_model_turn` (natively for Groq/Claude, as text for Gemini).
"""

from collections.abc import Callable

from vinsight.llm.base import Conversation, Delta, LLMError, Replayable, Step, ToolResult, ToolSpec


class ConversationRouter:
    def __init__(self, factories: dict[str, Callable[[], Replayable]], active: str, fallback: bool = True):
        # Order matters: on failure, the first other provider in this order takes over.
        self._factories = factories
        self.active = active
        self.fallback = fallback
        self._convo: Conversation = factories[active]()
        self._log: list[tuple[str, object]] = []

    @property
    def providers(self) -> list[str]:
        return list(self._factories)

    def use(self, provider: str) -> None:
        """Make `provider` active for the next steps, carrying the conversation over."""
        if provider == self.active or provider not in self._factories:
            return
        convo = self._factories[provider]()
        for kind, item in self._log:
            if kind == "user":
                convo.add_user(item)
            elif kind == "model":
                convo.add_model_turn(item)
            else:
                convo.add_tool_results(item)
        self._convo, self.active = convo, provider

    def add_user(self, text: str) -> None:
        self._log.append(("user", text))
        self._convo.add_user(text)

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        try:
            async for item in self._convo.stream(system, tools, effort):
                if isinstance(item, Step):
                    self._log.append(("model", item))
                yield item
            return
        except LLMError as e:
            # Fall back only if nothing reached the user yet; otherwise they'd see two answers spliced.
            backup = next((p for p in self._factories if p != self.active), None) if self.fallback else None
            if e.partial or backup is None:
                raise
            failed = self.active
            self.use(backup)
            yield Delta("notice", f"{_LABELS.get(failed, failed)} is unavailable right now ({e}). "
                                  f"Switched to {_LABELS.get(backup, backup)}.")
        async for item in self._convo.stream(system, tools, effort):
            if isinstance(item, Step):
                self._log.append(("model", item))
            yield item

    def add_prefetched(self, step: Step, results: list[ToolResult]) -> None:
        """Record tool calls the app ran before the model's first step, as if the model had made them."""
        self._log.append(("model", step))
        self._convo.add_model_turn(step)
        self.add_tool_results(results)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self._log.append(("tools", results))
        self._convo.add_tool_results(results)


_LABELS = {"gemini": "Gemini", "groq": "GPT-OSS", "claude": "Claude"}
