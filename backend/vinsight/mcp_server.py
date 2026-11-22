"""VINsight tools, served over MCP so any MCP-capable agent or client can use them.

Run standalone:  vinsight-mcp            (stdio)
Inspect:         mcp dev vinsight/mcp_server.py
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from vinsight import listing
from vinsight.models import (
    ComplaintsSummary,
    ListingAnalysis,
    ModelYearComparison,
    RecallsResult,
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


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
