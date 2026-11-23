"""VINsight tools, served over MCP so any MCP-capable agent or client can use them.

Run standalone:  vinsight-mcp            (stdio)
Inspect:         mcp dev vinsight/mcp_server.py
"""

import asyncio
import datetime
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import hashlib
import re
import time

from vinsight import attachments, fueleconomy, listing
from vinsight.models import (
    ComplaintsSummary,
    FuelEconomy,
    PickResearch,
    PicksResearch,
    LinkReading,
    ListingAnalysis,
    ModelYearComparison,
    RecallsResult,
    Source,
    Vehicle,
    VinCheck,
)
from vinsight.nhtsa import NHTSAClient, NHTSAError

server = MCPServer(
    name="vinsight",
    instructions=(
        "Tools for evaluating a used car. Every result carries a `source` object; cite its `id` for "
        "any fact you repeat. Results only cover what NHTSA publishes, so an empty result means "
        "'nothing on record', not 'no problems'."
    ),
)

# httpx logs every request at INFO, which drowns the MCP server's own output.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def _nhtsa() -> AsyncIterator[NHTSAClient]:
    """Yield a client and turn expected NHTSA failures into errors the model can read and act on."""
    async with NHTSAClient() as client:
        try:
            yield client
        except NHTSAError as e:
            raise ToolError(str(e)) from e


@server.tool()
async def decode_vin(vin: str) -> Vehicle:
    """Decode a 17-character VIN (NHTSA vPIC): year, make, model, trim, engine, drivetrain.
    `warnings` flags problems such as a check-digit mismatch (a typo or a tampered VIN). Use its
    make/model/year for the other tools.
    """
    async with _nhtsa() as nhtsa:
        return await nhtsa.decode_vin(vin)


@server.tool()
async def get_recalls(make: str, model: str, year: int) -> RecallsResult:
    """NHTSA recalls for a year/make/model, with campaign numbers. Use make and model as
    decode_vin returned them. Shows recalls issued for the model, not whether this car was repaired.
    """
    async with _nhtsa() as nhtsa:
        return await nhtsa.get_recalls(make, model, year)


@server.tool()
async def get_complaints(make: str, model: str, year: int) -> ComplaintsSummary:
    """NHTSA owner complaints for a year/make/model: totals (crashes, fires, injuries, deaths),
    the most-complained-about components, and a few sample complaints with ODI numbers.
    """
    async with _nhtsa() as nhtsa:
        return await nhtsa.get_complaints(make, model, year)


@server.tool()
async def compare_model_years(
    make: str, model: str, start_year: int, end_year: int, brief: bool = False
) -> ModelYearComparison:
    """Recall and complaint counts per model year of one model (up to 12 years), to spot years to
    avoid. Spell make and model as NHTSA does ("HONDA", "Civic"). `brief`: counts only (smaller).
    """
    async with _nhtsa() as nhtsa:
        result = await nhtsa.compare_model_years(make, model, start_year, end_year)
    if brief:
        for y in result.years:
            y.top_components = y.top_components[:1]
            y.recall_campaigns = []
    return result


async def _xray(text: str) -> ListingAnalysis:
    """Rule-based red flags plus a VIN-vs-listing check. Shared by analyze_listing and read_link."""
    analysis = listing.extract(text)
    if analysis.vin:
        try:
            async with NHTSAClient() as nhtsa:
                v = await nhtsa.decode_vin(analysis.vin)
            decoded = " ".join(str(x) for x in (v.year, v.make, v.model) if x)
            claimed = (analysis.year, analysis.make)
            if not any(claimed):
                analysis.vin_check = VinCheck(vin=analysis.vin, decoded=decoded, matches=None,
                                              detail="The listing doesn't say which car it is; compare with the VIN yourself.")
            else:
                year_ok = analysis.year is None or analysis.year == v.year
                make_ok = analysis.make is None or (v.make or "").lower().startswith(analysis.make.lower().split("-")[0])
                ok = year_ok and make_ok
                analysis.vin_check = VinCheck(
                    vin=analysis.vin, decoded=decoded, matches=ok,
                    detail="VIN matches the listing." if ok else
                    f"The listing says {analysis.year or '?'} {analysis.make or '?'}, but the VIN decodes to {decoded}.",
                )
                if not ok:
                    analysis.flags.insert(0, listing.ListingFlag(
                        code="vin_mismatch", severity="high", label="VIN doesn't match listing",
                        quote=analysis.vin_check.detail,
                        why="The VIN may belong to a different car: a common trick to hide a salvage or stolen vehicle.",
                    ))
            if v.warnings:
                analysis.flags.append(listing.ListingFlag(
                    code="vin_warning", severity="medium", label="VIN looks wrong",
                    quote="; ".join(v.warnings), why="Could be a typo, or a tampered or fake VIN. Check it on the car itself.",
                ))
        except NHTSAError as e:
            analysis.vin_check = VinCheck(vin=analysis.vin, decoded=None, matches=None, detail=f"Couldn't decode: {e}")
    listing.verdict_floor(analysis)
    return analysis


@server.tool()
async def analyze_listing(text: str) -> ListingAnalysis:
    """X-ray a used-car listing (pass its text as written): price, mileage, vehicle and VIN, plus
    red flags and scam patterns with exact quotes. A VIN that doesn't match the advertised car is a
    strong scam signal. `verdict_floor` (AVOID / CAUTION / NONE) is the most lenient verdict allowed.
    """
    return await _xray(text)


@server.tool()
async def get_fuel_economy(make: str, model: str, year: int) -> FuelEconomy:
    """EPA fuel economy for a model year: city / highway / combined mpg per engine, and EPA's
    yearly fuel-cost estimate. Cite as [epa:<make>:<model>:<year>].
    """
    try:
        return await fueleconomy.get_fuel_economy(make, model, year)
    except fueleconomy.FuelEconomyError as e:
        raise ToolError(str(e)) from e


_YEARS_RE = re.compile(r"\s+(\d{4})(?:\s*(?:-|–|to)\s*(\d{4}))?\s*$")
# Makes whose model names start with the make ("MAZDA3"), so a pick without a separate make still works.
_MAKE_PREFIXES = ("MAZDA", "MINI")


def parse_pick(text: str) -> tuple[str, str, int, int]:
    """ "HONDA Civic 2014-2018" -> make, model, start and end year. Years are optional: a missing range
    becomes the last 12 years, a single year becomes that year +/- 3."""
    text = text.strip()
    m = _YEARS_RE.search(text)
    name = text[: m.start()] if m else text
    words = name.split()
    if len(words) >= 2:
        make, model = words[0], " ".join(words[1:])
    elif words and (prefix := next((p for p in _MAKE_PREFIXES if words[0].upper().startswith(p) and len(words[0]) > len(p)), None)):
        make, model = prefix, words[0]
    else:
        raise ValueError(f'expected "MAKE Model 2014-2018", got "{text}"')
    if m and m.group(2):
        start, end = sorted((int(m.group(1)), int(m.group(2))))
    elif m:
        start, end = int(m.group(1)) - 3, int(m.group(1)) + 3
    else:
        end = datetime.date.today().year - 1
        start = end - 11
    return make, model, start, min(end, start + 11)


@server.tool()
async def research_picks(picks: list[str]) -> PicksResearch:
    """Research a whole shortlist in ONE call. Picks are strings "MAKE Model start-end" with the
    recommended years, e.g. ["HONDA Civic 2014-2018", "TOYOTA Corolla 2014-2019"]. Returns per-year
    complaint and recall counts (a little wider than the range) and EPA fuel economy for the middle
    year. Cite with the ids in the result: [years:...] and [epa:...].
    """

    async def one(text: str) -> PickResearch:
        try:
            make, model, start, end = parse_pick(text)
        except ValueError as e:
            return PickResearch(make="", model=text, errors=[str(e)])
        out = PickResearch(make=make, model=model)
        # Widen the comparison a little so a bad year just outside the recommended range shows up.
        lo, hi = max(start - 2, end - 11), min(end + 2, datetime.date.today().year)
        years, fuel = await asyncio.gather(
            compare_model_years(make, model, lo, max(hi, lo), brief=True),
            fueleconomy.get_fuel_economy(make, model, (start + end) // 2),
            return_exceptions=True,
        )
        if isinstance(years, BaseException):
            out.errors.append(f"model years: {years}")
        else:
            out.years = years
        if isinstance(fuel, BaseException):
            out.errors.append(f"fuel economy: {fuel}")
        else:
            out.fuel = fuel
        return out

    return PicksResearch(picks=list(await asyncio.gather(*(one(p) for p in picks[:4]))))


_LINK_CACHE: dict[str, tuple[float, LinkReading]] = {}
_LINK_TTL_S = 600
_TEXT_LIMIT = 3500


def _focus(text: str, focus: str | None, limit: int) -> tuple[str, bool]:
    """Keep the parts of the page that matter: paragraphs mentioning the focus terms first."""
    if len(text) <= limit:
        return text, False
    if not focus:
        return text[:limit].rsplit(" ", 1)[0] + "…", True
    terms = [t for t in re.findall(r"[a-z0-9]+", focus.lower()) if len(t) > 2]
    paras = [p for p in text.split("\n") if p.strip()]
    ranked = sorted(range(len(paras)), key=lambda i: -sum(paras[i].lower().count(t) for t in terms))
    keep, size = set(), 0
    for i in ranked:
        if size + len(paras[i]) > limit:
            break
        keep.add(i)
        size += len(paras[i]) + 1
    return "\n".join(paras[i] for i in sorted(keep)), True


@server.tool()
async def read_link(url: str, focus: str | None = None) -> LinkReading:
    """Read a web page the user shared: title, trimmed text (`focus`, e.g. "price mileage VIN",
    keeps the relevant parts) and any structured vehicle data. Car listings are also X-rayed (red
    flags, VIN check), so analyze_listing isn't needed. `status` "partial": only a preview (use it,
    ask for the full text); "blocked": ask the user to paste the text. Cite as [link:<id>].
    """
    key = f"{url.strip()}|{focus or ''}"
    cached = _LINK_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _LINK_TTL_S:
        return cached[1]
    try:
        page = await attachments.from_url(url)
    except attachments.AttachmentError as e:
        raise ToolError(str(e)) from e

    link_id = hashlib.sha1(page.url.encode()).hexdigest()[:8] if page.url else "link"
    reading = LinkReading(
        url=page.url or url, site=page.site, title=page.title, status=page.status, note=page.note,
        structured=page.structured, source=Source(type="link", id=link_id, url=page.url),
    )
    if page.status in ("ok", "partial"):
        reading.text, reading.truncated = _focus(page.text, focus, _TEXT_LIMIT)
        # X-ray it if it looks like ONE car listing: structured vehicle data, or a price plus a car.
        # Many distinct prices means a search-results page, which isn't a listing to judge.
        facts = " ".join(f"{k}: {v}" for k, v in page.structured.items())
        probe = listing.extract(f"{page.title or ''}\n{page.text}")
        many_prices = len(set(re.findall(r"\$\s?\d[\d,]{2,}", page.text))) > 4
        if page.structured.get("vin") or page.structured.get("price") or (
            (probe.price or page.status == "partial") and (probe.year or probe.make) and not many_prices
        ):
            reading.listing = await _xray(f"{page.title or ''}\n{facts}\n{page.text[:6000]}")
    _LINK_CACHE[key] = (time.monotonic(), reading)
    return reading


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
