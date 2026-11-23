"""One conversation that can move between providers: because the user picked another model, or
because the active one failed and a backup took over.

Providers keep history in incompatible native formats, so the router keeps a provider-neutral
log and rebuilds a provider's history from it whenever that provider becomes active. Turns the
provider wrote itself stay native only while it remains active; after a switch, everything is
replayed through `add_model_turn` (natively for Groq/Claude, as text for Gemini).
"""

import logging
from collections.abc import Callable

from vinsight.config import settings
from vinsight.llm import limits
from vinsight.llm.base import Conversation, Delta, LLMError, Replayable, Step, ToolResult, ToolSpec, _kind, short_reason

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

    def _quota_key(self, provider: str) -> tuple[str, str]:
        """Where a provider's out-of-quota state is remembered: shared by every chat using the same key."""
        factory = self._factories.get(provider)
        api_key = getattr(factory, "keywords", {}).get("api_key")
        return f"provider:{provider}", limits.key_id(api_key)

    def _unavailable(self, provider: str) -> bool:
        """Out of quota for longer than is worth waiting (the provider paces short waits itself)."""
        out = limits.blocked(*self._quota_key(provider))
        return bool(out) and (out[1] == "day" or out[0] > settings.groq_max_wait_s)

    async def stream(self, system: str, tools: list[ToolSpec], effort: str | None = None):
        """Stream from the active provider; if it fails before any output, try every other provider in
        order (each has its own quota) until one answers, and stay on that one. Providers known to be
        out of quota (in any chat) are skipped without trying them."""
        tried: list[str] = []
        if self.fallback and self._unavailable(self.active):
            ready = next((p for p in self._factories if not self._unavailable(p)), None)
            if ready and ready != self.active:
                skipped = self.active
                out = limits.blocked(*self._quota_key(skipped))
                why = "has used up its free quota for today" if out and out[1] == "day" else "is busy right now"
                self.use(ready)
                yield Delta("notice", f"{self._labels.get(skipped, skipped)} {why}; using {self._labels.get(ready, ready)}.")
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
                    seconds = e.retry_in if e.retry_in is not None else (3600 if e.scope == "day" else 60)
                    limits.block(*self._quota_key(self.active), seconds, e.scope or "minute")
                # Fall back only if nothing reached the user yet; otherwise they'd see two answers spliced.
                candidates = [p for p in self._factories if p not in tried and not self._unavailable(p)]
                nxt = candidates[0] if self.fallback and candidates else None
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
