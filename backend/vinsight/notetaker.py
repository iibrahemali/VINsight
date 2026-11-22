"""The note-taker: a second, small agent that listens to the advisor call and keeps the buyer's
profile up to date.

Splitting this out of the advisor keeps the voice fast: the advisor only talks (one model step
per turn instead of a tool round-trip), and the note-taker runs after each exchange on a smaller
model with its own rate-limit budget (gpt-oss-20b on Groq), so it never delays speech.
"""

import json

import groq

from vinsight.config import settings
from vinsight.models import BuyerProfile, ShortlistEntry

_LIST_FIELDS = ("must_haves", "dealbreakers")

PROMPT = """\
You keep notes for a car-buying advisor's call. Given the current notes and the latest exchange,
return the updated notes as one JSON object with any of these keys:
budget, use, commute, passengers, cargo, climate, fuel, experience, timeline (short strings),
must_haves, dealbreakers (arrays of short strings),
shortlist (array of {"make": "Honda", "model": "Civic", "years": "2018-2020", "why": "great
mileage"}: only models the advisor actually recommended, with the make and model spelled the
standard way, e.g. "Subaru" / "Impreza", "Mazda" / "Mazda3", "Toyota" / "Corolla").
Include only keys whose value is new or changed in this exchange. Use the buyer's own words, kept
short. Never guess: if something wasn't said, leave it out. Return {} if nothing changed."""


def merge(profile: BuyerProfile, update: dict) -> BuyerProfile:
    """Apply an update without losing earlier answers: scalars overwrite, lists accumulate,
    the shortlist is replaced whenever the advisor gives a new one."""
    data = profile.model_dump(exclude={"source"})
    for key, value in update.items():
        if key not in data or value in (None, "", [], {}):
            continue
        if key in _LIST_FIELDS:
            items = value if isinstance(value, list) else [value]
            data[key] = list(dict.fromkeys([*data[key], *(str(v) for v in items)]))
        elif key == "shortlist":
            entries = []
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, dict) and v.get("make") and v.get("model"):
                    entries.append(ShortlistEntry(**{k: str(v.get(k) or "") for k in ("make", "model", "years", "why")}))
            if entries:  # a new recommendation replaces the old one
                data[key] = [e.model_dump() for e in entries]
        else:
            data[key] = str(value)
    return BuyerProfile(**data)


async def take_notes(
    profile: BuyerProfile, user: str, advisor: str, client: groq.AsyncGroq | None = None, api_key: str | None = None
) -> BuyerProfile:
    client = client or groq.AsyncGroq(api_key=api_key or settings.groq_api_key, max_retries=2, timeout=20)
    notes = json.dumps(profile.model_dump(exclude={"source"}, exclude_defaults=True))
    response = await client.chat.completions.create(
        model=settings.groq_notetaker_model,
        reasoning_effort="low",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": f"Current notes: {notes}\n\nBuyer said: {user}\n\nAdvisor said: {advisor}"},
        ],
    )
    try:
        update = json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return profile
    return merge(profile, update if isinstance(update, dict) else {})
