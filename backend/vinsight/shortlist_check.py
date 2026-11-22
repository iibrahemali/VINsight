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


def _window(years: str) -> tuple[int, int] | None:
    found = [int(y) for y in re.findall(r"(?:19|20)\d{2}", years)]
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
