"""EPA fuel economy (fueleconomy.gov): free, no key. Used for running-cost comparisons."""

import asyncio

import httpx

from vinsight.config import settings
from vinsight.models import FuelEconomy, FuelVariant, Source

BASE = "https://www.fueleconomy.gov/ws/rest/vehicle"
_HEADERS = {"Accept": "application/json"}


class FuelEconomyError(RuntimeError):
    pass


def _items(data: dict) -> list[dict]:
    """The API returns one menu item as an object and several as a list."""
    items = (data or {}).get("menuItem") or []
    return items if isinstance(items, list) else [items]


async def get_fuel_economy(make: str, model: str, year: int, client: httpx.AsyncClient | None = None) -> FuelEconomy:
    own = client is None
    client = client or httpx.AsyncClient(timeout=settings.http_timeout_s, headers=_HEADERS)
    try:
        resp = await client.get(f"{BASE}/menu/model", params={"year": year, "make": make.title()})
        resp.raise_for_status()
        # EPA splits models by body ("Civic 4Dr", "Civic 5Dr"); keep the ones that start with the name
        # asked for, minus performance and electrified trims that would skew the range.
        menu = [m["text"] for m in _items(resp.json())]
        wanted = model.lower().replace("-", " ")
        names = [n for n in menu if n.lower().replace("-", " ").startswith(wanted)]
        if not names and wanted.startswith(make.lower()):
            # NHTSA says "MAZDA3", EPA says "3 4-Door".
            short = wanted[len(make):].strip()
            names = [n for n in menu if short and n.lower().split()[0] == short]
        names = [n for n in names if not any(k in f" {n.lower()} " for k in (" type r", " si ", "hybrid", " ev ", "fcv", "plug"))] or names
        if not names:
            raise FuelEconomyError(f"EPA has no {year} {make} {model}")

        limit = asyncio.Semaphore(4)

        async def options(name: str) -> list[tuple[str, str]]:
            async with limit:
                r = await client.get(f"{BASE}/menu/options", params={"year": year, "make": make.title(), "model": name})
            r.raise_for_status()
            return [(name, o["value"]) for o in _items(r.json())][:3]

        async def vehicle(name: str, vid: str) -> FuelVariant:
            async with limit:
                r = await client.get(f"{BASE}/{vid}")
            r.raise_for_status()
            v = r.json()
            return FuelVariant(
                model=name,
                engine=f"{v.get('displ')} L {v.get('cylinders')}-cyl, {v.get('trany')}",
                city_mpg=int(v.get("city08") or 0),
                highway_mpg=int(v.get("highway08") or 0),
                combined_mpg=int(v.get("comb08") or 0),
                annual_fuel_cost=int(v.get("fuelCost08") or 0) or None,
                drive=v.get("drive"),
            )

        pairs = [p for group in await asyncio.gather(*(options(n) for n in names[:3])) for p in group]
        # Four versions are enough to show the range and keep the result small for the model.
        variants = list(await asyncio.gather(*(vehicle(n, vid) for n, vid in pairs[:4])))
    except httpx.HTTPError as e:
        raise FuelEconomyError(f"couldn't reach fueleconomy.gov ({type(e).__name__})") from e
    finally:
        if own:
            await client.aclose()

    combined = [v.combined_mpg for v in variants if v.combined_mpg]
    costs = [v.annual_fuel_cost for v in variants if v.annual_fuel_cost]
    return FuelEconomy(
        make=make,
        model=model,
        year=year,
        combined_mpg_range=f"{min(combined)}-{max(combined)}" if combined else "unknown",
        annual_fuel_cost_range=f"${min(costs):,}-${max(costs):,}" if costs else "unknown",
        variants=variants,
        source=Source(
            type="epa",
            id=f"{make}:{model}:{year}".lower(),
            url=f"https://www.fueleconomy.gov/feg/PowerSearch.do?action=noform&path=1&year1={year}&year2={year}"
            f"&make={make.title()}&baseModel={model.title()}",
        ),
    )
