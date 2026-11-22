from functools import partial

from vinsight.config import settings
from vinsight.keys import Keys
from vinsight.llm.base import Conversation, Delta, LLMError, Step, ToolCall, ToolResult, ToolSpec
from vinsight.llm.router import ConversationRouter

# What the model picker offers. Each option is a provider plus the model(s) to try, in order.
# An option appears only if a key for its provider is available (the visitor's or the server's).
MODELS: dict[str, dict] = {
    "gpt-oss-120b": {
        "provider": "groq", "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        "label": "GPT-OSS 120B", "detail": "OpenAI open model on Groq. Best quality, streams its reasoning.",
    },
    "gpt-oss-20b": {
        "provider": "groq", "models": ["openai/gpt-oss-20b"],
        "label": "GPT-OSS 20B", "detail": "Smaller and fastest. Has its own daily quota.",
    },
    "qwen3.8-27b": {
        "provider": "groq", "models": ["qwen/qwen3.8-27b"],
        "label": "Qwen 3.8 27B", "detail": "Alibaba open model on Groq. Quick, but no visible reasoning.",
    },
    "gemini": {"provider": "gemini", "label": "Gemini Flash", "detail": "Google. Slower on the free tier."},
    "claude": {"provider": "anthropic", "label": "Claude", "detail": "Anthropic Opus."},
}
# Older settings used provider names; map them to a model option.
ALIASES = {"groq": "gpt-oss-120b"}
LABELS = {k: v["label"] for k, v in MODELS.items()}


def resolve(option: str | None) -> str | None:
    return ALIASES.get(option, option) if option else None


def _factory(option: str, keys: Keys):
    spec = MODELS[option]
    provider = spec["provider"]
    if provider == "anthropic":
        from vinsight.llm.claude import ClaudeConversation

        return partial(ClaudeConversation, api_key=keys.anthropic)
    if provider == "groq":
        from vinsight.llm.groq import GroqConversation

        return partial(GroqConversation, api_key=keys.groq, models=spec["models"])
    from vinsight.llm.gemini import GeminiConversation

    return partial(GeminiConversation, api_key=keys.gemini)


def available_providers(keys: Keys | None = None) -> list[str]:
    """The model options usable with these keys, in display order."""
    keys = keys or Keys.server()
    return [k for k, spec in MODELS.items() if getattr(keys, spec["provider"])]


def provider_factories(keys: Keys | None = None) -> tuple[str, dict]:
    """The starting option and a factory per usable option, bound to these keys."""
    keys = keys or Keys.server()
    available = available_providers(keys)
    wanted = resolve(settings.vinsight_provider)
    # Start on the configured option if it has a key, else on whichever does.
    active = wanted if wanted in available else (available or [wanted])[0]
    backup = resolve(settings.vinsight_backup_provider)
    # Fallback order: the configured backup first, then everything else that has a key.
    names = list(dict.fromkeys([active, *([backup] if backup in available else []), *available]))
    return active, {n: _factory(n, keys) for n in names if n in MODELS}


def new_conversation(keys: Keys | None = None) -> ConversationRouter:
    """A conversation that starts on VINSIGHT_PROVIDER, can switch to any usable model, and falls
    back to VINSIGHT_BACKUP_PROVIDER (then the rest) when the active one fails."""
    active, factories = provider_factories(keys)
    return ConversationRouter(factories, active=active, fallback=settings.vinsight_backup_provider != "none", labels=LABELS)


# Back-compat name used by the API and CLI.
PROVIDERS = MODELS

__all__ = [
    "LABELS", "MODELS", "PROVIDERS", "Conversation", "ConversationRouter", "Delta", "LLMError", "Step", "ToolCall",
    "ToolResult", "ToolSpec", "available_providers", "new_conversation", "provider_factories", "resolve",
]
