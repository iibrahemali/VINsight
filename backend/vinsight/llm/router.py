"""One conversation that can move between providers: because the user picked another model, or
because the active one failed and a backup took over.

Providers keep history in incompatible native formats, so the router keeps a provider-neutral
log and rebuilds a provider's history from it whenever that provider becomes active. Turns the
provider wrote itself stay native only while it remains active; after a switch, everything is
replayed through `add_model_turn` (natively for Groq/Claude, as text for Gemini).
"""

import logging
import time
from collections.abc import Callable

from vinsight.llm.base import Conversation, Delta, LLMError, Replayable, Step, ToolResult, ToolSpec, _kind, short_reason

LIMIT_COOLDOWN_S = 120  # a model that just hit its rate limit is skipped for this long

log = logging.getLogger(__name__)


class ConversationRouter:
    def __init__(
        self,
        factories: dict[str, Callable[[], Replayable]],
        active: str,
        fallback: bool = True,
        labels: dict[str, str] | None = None,
    ):
        # Order matters: on failure, the first other provider in this order takes over.
        self._factories = factories
        self._labels = {**_LABELS, **(labels or {})}
        self._cooling: dict[str, float] = {}  # provider -> time until which it's skipped (rate-limited)
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

    def rebind(self, factories: dict[str, Callable[[], Replayable]]) -> None:
        """Use new provider factories (e.g. the visitor changed their API keys) without losing the
        conversation: the active provider is rebuilt from the neutral log."""
        self._factories = factories
        target = self.active if self.active in factories else next(iter(factories))
        self.active = ""  # force use() to rebuild
        self.use(target)

    def add_user(self, text: str) -> None:
        self._log.append(("user", text))
        self._convo.add_user(text)

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        """Stream from the active provider; if it fails before any output, try every other provider in
        order (each has its own quota) until one answers, and stay on that one."""
        tried: list[str] = []
        now = time.monotonic()
        if self.fallback and self._cooling.get(self.active, 0) > now:
            # The chosen model hit its limit moments ago: go straight to one that isn't cooling down.
            ready = next((p for p in self._factories if self._cooling.get(p, 0) <= now), None)
            if ready and ready != self.active:
                skipped = self.active
                self.use(ready)
                yield Delta("notice", f"{self._labels.get(skipped, skipped)} is over its free-tier limit; "
                                      f"using {self._labels.get(ready, ready)} for now.")
        while True:
            try:
                async for item in self._convo.stream(system, tools, effort):
                    if isinstance(item, Step):
                        self._log.append(("model", item))
                    yield item
                return
            except LLMError as e:
                tried.append(self.active)
                if _kind(e) == "limit":
                    self._cooling[self.active] = time.monotonic() + LIMIT_COOLDOWN_S
                # Fall back only if nothing reached the user yet; otherwise they'd see two answers spliced.
                nxt = next((p for p in self._factories if p not in tried), None) if self.fallback else None
                if e.partial or nxt is None:
                    raise
                failed = self.active
                log.warning("model %s failed, switching to %s: %s", failed, nxt, e)
                self.use(nxt)
                yield Delta("notice", f"{self._labels.get(failed, failed)} {short_reason(e)}. "
                                      f"Switched to {self._labels.get(nxt, nxt)}.")

    def add_prefetched(self, step: Step, results: list[ToolResult]) -> None:
        """Record tool calls the app ran before the model's first step, as if the model had made them."""
        self._log.append(("model", step))
        self._convo.add_model_turn(step)
        self.add_tool_results(results)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self._log.append(("tools", results))
        self._convo.add_tool_results(results)


_LABELS = {"gemini": "Gemini", "groq": "GPT-OSS", "claude": "Claude"}
