"""Free-tier budgets, shared by every chat in this process.

Groq's free tier allows ~8K tokens a minute per model, and counts a request's input *plus* the reply
it reserves. So the useful question before sending is "does this request fit in what's left this
minute?", and the answer is usually "yes, in a few seconds". This module remembers, per model and
API key, what the last response said was left and when the minute resets, and which models are out
of quota (for the minute or for the day), so every chat paces instead of failing over.
"""

import hashlib
import re
import time
from dataclasses import dataclass

_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?")


def parse_duration(text: str | None) -> float | None:
    """Groq's "7.66s", "1m16.03s", "2h3m" or "120ms" -> seconds."""
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    m = _DURATION.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    h, mins, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mins * 60 + s + ms / 1000


def key_id(api_key: str | None) -> str:
    """Visitors' own keys have their own quotas; tell them apart without keeping the key."""
    return hashlib.sha256((api_key or "").encode()).hexdigest()[:12]


@dataclass
class _Budget:
    remaining: int | None = None  # tokens left this minute, as of the last response
    limit: int | None = None  # tokens per minute
    seen_at: float = 0.0  # when `remaining` was read
    resets_at: float = 0.0  # when the minute's tokens are all back
    blocked_until: float = 0.0  # out of quota until then
    blocked_for: str = ""  # "minute" or "day"


_budgets: dict[tuple[str, str], _Budget] = {}


def _get(model: str, key: str) -> _Budget:
    return _budgets.setdefault((model, key), _Budget())


def note_headers(model: str, key: str, headers) -> None:
    """Record what a response said is left this minute (Groq has already subtracted that request)."""
    if headers is None:
        return
    remaining = headers.get("x-ratelimit-remaining-tokens")
    reset = parse_duration(headers.get("x-ratelimit-reset-tokens"))
    b = _get(model, key)
    if remaining is not None:
        try:
            b.remaining = int(float(remaining))
            b.limit = int(float(headers.get("x-ratelimit-limit-tokens") or 0)) or None
        except ValueError:
            return
        b.seen_at = time.monotonic()
        b.resets_at = b.seen_at + (reset or 60.0)


def wait_for(model: str, key: str, tokens: int) -> float:
    """Seconds to wait before a request of about `tokens` fits this minute's budget (0 = send now)."""
    b = _get(model, key)
    now = time.monotonic()
    if b.remaining is None or now >= b.resets_at or tokens <= b.remaining:
        return 0.0
    if not b.limit:
        return b.resets_at - now
    # The budget refills continuously (limit per 60 s), so wait only for the tokens that are missing.
    rate = b.limit / 60.0
    ready_at = b.seen_at + (tokens - b.remaining) / rate
    return max(0.0, min(ready_at, b.resets_at) - now)


def block(model: str, key: str, seconds: float, scope: str) -> None:
    """Mark a model out of quota for `seconds` ("minute" or "day")."""
    b = _get(model, key)
    b.blocked_until = max(b.blocked_until, time.monotonic() + seconds)
    b.blocked_for = scope


def blocked(model: str, key: str) -> tuple[float, str] | None:
    """(seconds left, "minute" | "day") while a model is out of quota, else None."""
    b = _get(model, key)
    left = b.blocked_until - time.monotonic()
    return (left, b.blocked_for) if left > 0 else None


def limit_scope(error: Exception) -> str:
    """Whether a rate-limit error is about this minute or the whole day."""
    text = str(error).lower()
    return "day" if any(k in text for k in ("per day", "(tpd)", "(rpd)", "daily", "perday")) else "minute"


_TRY_AGAIN = re.compile(r"try again in ([\dhms.]+)", re.I)


def retry_in(error: Exception) -> float | None:
    """Seconds the provider asked us to wait: the retry-after header, else "try again in 1m16s"."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            return float(headers.get("retry-after"))
        except (TypeError, ValueError):
            pass
    m = _TRY_AGAIN.search(str(error))
    return parse_duration(m.group(1).rstrip(".")) if m else None


def reset() -> None:
    """Forget everything (tests)."""
    _budgets.clear()
