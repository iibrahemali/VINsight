"""VINsight tools, served over MCP so any MCP-capable agent or client can use them.

Run standalone:  vinsight-mcp            (stdio)
Inspect:         mcp dev vinsight/mcp_server.py
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import hashlib
import re
import time

from vinsight import attachments, listing
from vinsight.models import (
    ComplaintsSummary,
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
    """Decode a 17-character VIN into year, make, model, trim, engine and drivetrain using NHTSA vPIC.

    Also validates the VIN offline (length, illegal characters, check digit). `warnings` lists
    anything suspicious, such as a check-digit mismatch, which can mean a typo or a tampered VIN.
    Call this first whenever a VIN is available; use its make/model/year for every other tool.
    """
    async with _nhtsa() as nhtsa:
        return await nhtsa.decode_vin(vin)


@server.tool()
async def get_recalls(make: str, model: str, year: int) -> RecallsResult:
    """List NHTSA safety recalls for a year/make/model, each with its campaign number.

    Use the make and model exactly as decode_vin returned them. The results show recalls that
    were issued for the model, not whether a given car has had the repair done.
    """
    async with _nhtsa() as nhtsa:
        return await nhtsa.get_recalls(make, model, year)


@server.tool()
async def get_complaints(make: str, model: str, year: int) -> ComplaintsSummary:
    """Summarize owner complaints filed with NHTSA for a year/make/model.

    Returns totals (crashes, fires, injuries, deaths), the components complained about most,
    and a few sample complaint narratives with their ODI numbers. A high count on one component
    is a strong signal of a known failure point.
    """
    async with _nhtsa() as nhtsa:
        return await nhtsa.get_complaints(make, model, year)


@server.tool()
async def compare_model_years(
    make: str, model: str, start_year: int, end_year: int, brief: bool = False
) -> ModelYearComparison:
    """Compare recall and complaint counts across model years of one model (up to 12 years).

    Use it to find which years of a model to target or avoid, or to check whether a specific
    car's year is unusually problematic compared with the years around it. Make and model must
    be spelled as NHTSA does (e.g. "HONDA", "Civic"; "FORD", "F-150"). Set `brief` for just the
    per-year counts (much smaller; use it when you only need to spot bad years).
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
    """X-ray a used-car listing: extract price, mileage, year/make/model and VIN, and flag red
    flags and scam patterns (salvage/rebuilt/flood titles, deposits before viewing, shipping-only
    sellers, odd payment methods, warning lights, mileage problems, and more) with the exact quote.

    If the listing contains a VIN, it is decoded and compared with the car the listing claims to
    be: a mismatch is a strong scam signal. Returns `verdict_floor`, the strictest verdict the rules
    require (AVOID / CAUTION / NONE). Your final verdict may be stricter, never more lenient.
    Pass the listing text exactly as written.
    """
    return await _xray(text)


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
    """Open a web page the user shared and bring back what matters, in one step.

    Returns the page title, readable text (trimmed; pass `focus`, e.g. "price mileage title VIN",
    to keep the most relevant parts), and any vehicle details the page publishes in structured data
    (price, mileage, VIN, year, make, model). If the page is a car listing, it is also X-rayed for red
    flags and its VIN is checked against the listing, so you don't need to call analyze_listing.
    Some sites (Facebook Marketplace, some dealers) only show bots a preview: then `status` is
    "partial" and `text` has the title and summary. Use them (e.g. the year/make/model in the title
    is enough for recalls and complaints) and ask the user to paste the full listing. If `status` is
    "blocked", nothing was readable: ask for the text. Cite the page as [link:<id>].
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
