from functools import partial

from vinsight.config import settings
from vinsight.keys import Keys
from vinsight.llm.base import Conversation, Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec
from vinsight.llm.router import ConversationRouter

# What the UI offers. A provider appears only if a key for it is available (the visitor's or the server's).
PROVIDERS = {
    "gemini": {"label": "Gemini", "detail": "Google · Gemini Flash", "key": "gemini"},
    "groq": {"label": "GPT-OSS 120B", "detail": "OpenAI open model · on Groq", "key": "groq"},
    "claude": {"label": "Claude", "detail": "Anthropic · Opus", "key": "anthropic"},
}


def _factory(provider: str):
    if provider == "claude":
        from vinsight.llm.claude import ClaudeConversation

        return ClaudeConversation
    if provider == "groq":
        from vinsight.llm.groq import GroqConversation

        return GroqConversation
    from vinsight.llm.gemini import GeminiConversation

    return GeminiConversation


def available_providers(keys: Keys | None = None) -> list[str]:
    keys = keys or Keys.server()
    return [p for p, info in PROVIDERS.items() if getattr(keys, info["key"])]


def provider_factories(keys: Keys | None = None) -> tuple[str, dict]:
    """The starting provider and a factory per usable provider, bound to these keys."""
    keys = keys or Keys.server()
    available = available_providers(keys)
    # Start on the configured provider if it has a key, else on whichever does (e.g. Groq-only setups).
    active = settings.vinsight_provider if settings.vinsight_provider in available else (available or [settings.vinsight_provider])[0]
    backup = settings.vinsight_backup_provider
    # Fallback order: the configured backup first, then anything else that has a key.
    names = list(dict.fromkeys([active, *([backup] if backup in available else []), *available]))
    return active, {n: partial(_factory(n), api_key=getattr(keys, PROVIDERS[n]["key"])) for n in names}


def new_conversation(keys: Keys | None = None) -> ConversationRouter:
    """A conversation that starts on VINSIGHT_PROVIDER, can switch to any configured provider, and
    falls back to VINSIGHT_BACKUP_PROVIDER when the active one fails."""
    active, factories = provider_factories(keys)
    return ConversationRouter(factories, active=active, fallback=settings.vinsight_backup_provider != "none")


__all__ = [
    "PROVIDERS", "Conversation", "ConversationRouter", "Delta", "LLMError", "Step", "ToolCall", "ToolResult",
    "ToolSpec", "available_providers", "new_conversation", "provider_factories",
]
