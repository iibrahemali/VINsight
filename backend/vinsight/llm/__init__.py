from vinsight.config import settings
from vinsight.llm.base import Conversation, Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec
from vinsight.llm.router import ConversationRouter

# What the UI offers. A provider appears only if its API key is configured.
PROVIDERS = {
    "gemini": {"label": "Gemini", "detail": "Google · Gemini Flash", "key": "gemini_api_key"},
    "groq": {"label": "GPT-OSS 120B", "detail": "OpenAI open model · on Groq", "key": "groq_api_key"},
    "claude": {"label": "Claude", "detail": "Anthropic · Opus", "key": "anthropic_api_key"},
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


def available_providers() -> list[str]:
    return [p for p, info in PROVIDERS.items() if getattr(settings, info["key"])]


def new_conversation() -> ConversationRouter:
    """A conversation that starts on VINSIGHT_PROVIDER, can switch to any configured provider, and
    falls back to VINSIGHT_BACKUP_PROVIDER when the active one fails."""
    available = available_providers()
    # Start on the configured provider if it has a key, else on whichever does (e.g. Groq-only setups).
    active = settings.vinsight_provider if settings.vinsight_provider in available else (available or [settings.vinsight_provider])[0]
    backup = settings.vinsight_backup_provider
    # Fallback order: the configured backup first, then anything else that has a key.
    order = [active, *([backup] if backup in available else []), *available]
    names = list(dict.fromkeys(order))
    return ConversationRouter({n: _factory(n) for n in names}, active=active, fallback=backup != "none")


__all__ = [
    "PROVIDERS", "Conversation", "ConversationRouter", "Delta", "LLMError", "Step", "ToolCall", "ToolResult",
    "ToolSpec", "available_providers", "new_conversation",
]
