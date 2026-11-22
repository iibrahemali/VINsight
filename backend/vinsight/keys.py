"""API keys for one request: the visitor's own keys (sent as headers from the Settings panel) win
over the server's .env. Keys from headers are used for that request only, never stored or logged."""

from dataclasses import dataclass

from fastapi import Request

from vinsight.config import settings

HEADERS = {"groq": "x-groq-key", "gemini": "x-gemini-key", "anthropic": "x-anthropic-key"}


@dataclass(frozen=True)
class Keys:
    groq: str | None = None
    gemini: str | None = None
    anthropic: str | None = None

    @classmethod
    def server(cls) -> "Keys":
        return cls(groq=settings.groq_api_key, gemini=settings.gemini_api_key, anthropic=settings.anthropic_api_key)

    @classmethod
    def from_request(cls, request: Request) -> "Keys":
        server = cls.server()

        def pick(name: str) -> str | None:
            own = (request.headers.get(HEADERS[name]) or "").strip()
            return own or getattr(server, name)

        return cls(groq=pick("groq"), gemini=pick("gemini"), anthropic=pick("anthropic"))

    def source(self, name: str, request: Request) -> str:
        """'yours', 'server' or 'none': shown in the Settings panel."""
        if (request.headers.get(HEADERS[name]) or "").strip():
            return "yours"
        return "server" if getattr(Keys.server(), name) else "none"
