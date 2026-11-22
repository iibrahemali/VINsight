"""Checks the advisor's recommendations against NHTSA data, in code, after the fact.

The advisor recommends models from general knowledge (fast, conversational); this module then
pulls the complaint and recall counts for the years around each recommendation and flags any
year with a clear complaint spike. The result is shown on screen and fed back to the advisor.
"""

import asyncio
import re
import statistics

from vinsight.models import ShortlistEntry
from vinsight.nhtsa import NHTSAClient, NHTSAError

SPIKE_RATIO = 1.8  # a year this many times the median of its neighbours...
SPIKE_MIN = 100  # ...and with at least this many complaints, is flagged


_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_SPOKEN_YEAR = re.compile(
    r"\b(?:twenty|two thousand(?: and)?)[\s-]+(?:(twenty)(?:[\s-]+(" + "|".join(_ONES) + r"))?|(" + "|".join(_TEENS) + r")|(" + "|".join(_ONES) + r"))\b",
    re.I,
)


def spoken_years_to_digits(text: str) -> str:
    """The voice advisor says "twenty twenty one"; the year check needs "2021"."""

    def convert(m: re.Match) -> str:
        twenty, ones_after_twenty, teen, ones = m.groups()
        if twenty:
            return str(2020 + _ONES.get((ones_after_twenty or "").lower(), 0))
        if teen:
            return str(2000 + _TEENS[teen.lower()])
        return str(2000 + _ONES[ones.lower()])

    return _SPOKEN_YEAR.sub(convert, text)


def _window(years: str) -> tuple[int, int] | None:
    found = [int(y) for y in re.findall(r"(?:19|20)\d{2}", spoken_years_to_digits(years))]
    if not found:
        return None
    lo, hi = min(found), max(found)
    lo, hi = lo - 2, hi + 1  # include neighbours so a spike is visible against them
    return max(lo, hi - 11), hi


def spikes(stats) -> list[int]:
    flagged = []
    for y in stats:
        others = [o.complaints for o in stats if o.year != y.year]
        if others and y.complaints >= SPIKE_MIN and y.complaints >= SPIKE_RATIO * max(1, statistics.median(others)):
            flagged.append(y.year)
    return flagged


async def check(entry: ShortlistEntry, client: NHTSAClient) -> ShortlistEntry:
    window = _window(entry.years)
    if window is None:
        return entry.model_copy(update={"checked": True, "data_note": "No model years given to check."})
    try:
        comparison = await client.compare_model_years(entry.make.upper(), entry.model, *window)
    except NHTSAError as e:
        return entry.model_copy(update={"checked": True, "data_note": f"Couldn't check NHTSA: {e}"})
    stats = comparison.years
    if not any(y.complaints or y.recalls for y in stats):
        return entry.model_copy(update={"checked": True, "data_note": "NHTSA has no records under that name."})
    avoid = spikes(stats)
    note = None
    if avoid:
        worst = max((y for y in stats if y.year in avoid), key=lambda y: y.complaints)
        top = worst.top_components[0].lower() if worst.top_components else "various parts"
        note = (f"NHTSA shows {worst.complaints:,} complaints for the {worst.year} {entry.make} {entry.model}, "
                f"far more than nearby years, mostly about {top}.")
    return entry.model_copy(update={"checked": True, "year_stats": stats, "avoid": avoid, "data_note": note})


async def check_all(entries: list[ShortlistEntry]) -> list[ShortlistEntry]:
    async with NHTSAClient() as client:
        return list(await asyncio.gather(*(e if e.checked else check(e, client) for e in entries))) if entries else []
