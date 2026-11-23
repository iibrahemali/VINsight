"""EPA fuel economy client, offline."""

import httpx
import pytest

from vinsight.fueleconomy import FuelEconomyError, get_fuel_economy

MODELS = {"menuItem": [{"text": "Civic 4Dr", "value": "Civic 4Dr"}, {"text": "Civic 5Dr - Type R", "value": "x"},
                       {"text": "Accord", "value": "Accord"}]}
OPTIONS = {"menuItem": {"text": "Auto (variable gear ratios), 4 cyl, 2.0 L", "value": "39566"}}  # a single item is an object
VEHICLE = {"city08": "31", "highway08": "40", "comb08": "34", "fuelCost08": "1750", "displ": "2.0", "cylinders": "4",
           "trany": "Automatic (variable gear ratios)", "drive": "Front-Wheel Drive"}


def epa(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/menu/model"):
        return httpx.Response(200, json=MODELS)
    if path.endswith("/menu/options"):
        assert request.url.params["model"] == "Civic 4Dr"  # the Type R was filtered out
        return httpx.Response(200, json=OPTIONS)
    return httpx.Response(200, json=VEHICLE)


async def test_fuel_economy_for_a_model_year():
    async with httpx.AsyncClient(transport=httpx.MockTransport(epa)) as client:
        f = await get_fuel_economy("HONDA", "Civic", 2018, client=client)
    assert f.combined_mpg_range == "34-34" and f.annual_fuel_cost_range == "$1,750-$1,750"
    assert f.variants[0].city_mpg == 31 and f.variants[0].highway_mpg == 40
    assert f.source.type == "epa" and f.source.id == "honda:civic:2018"


async def test_unknown_model_is_a_clear_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(epa)) as client:
        with pytest.raises(FuelEconomyError, match="EPA has no 2018 HONDA Pilot"):
            await get_fuel_economy("HONDA", "Pilot", 2018, client=client)



def test_parse_pick():
    from vinsight.mcp_server import parse_pick

    assert parse_pick("HONDA Civic 2014-2018") == ("HONDA", "Civic", 2014, 2018)
    assert parse_pick("TOYOTA RAV4 2016–2014") == ("TOYOTA", "RAV4", 2014, 2016)
    assert parse_pick("MAZDA Mazda3 2015") == ("MAZDA", "Mazda3", 2012, 2018)
    assert parse_pick("FORD F-150 Super Duty 2010 to 2013")[1:] == ("F-150 Super Duty", 2010, 2013)
    assert parse_pick("MAZDA3 2015-2019") == ("MAZDA", "MAZDA3", 2015, 2019)


async def test_research_picks_gathers_years_and_fuel_and_keeps_partial_results(monkeypatch):
    from vinsight import fueleconomy, mcp_server
    from vinsight.models import FuelEconomy, ModelYearComparison, Source

    calls = []

    async def years(make, model, start, end, brief=False):
        calls.append((model, start, end))
        if model == "Nope":
            raise ValueError("no such model")
        return ModelYearComparison(make=make, model=model, years=[],
                                   source=Source(type="nhtsa_complaint", id=f"years:{make}:{model}:{start}-{end}".lower()))

    async def fuel(make, model, year):
        calls.append(year)
        return FuelEconomy(make=make, model=model, year=year, combined_mpg_range="30-34", annual_fuel_cost_range="$1,750-$1,900",
                           variants=[], source=Source(type="epa", id=f"{make}:{model}:{year}".lower()))

    monkeypatch.setattr(mcp_server, "compare_model_years", years)
    monkeypatch.setattr(fueleconomy, "get_fuel_economy", fuel)
    out = await mcp_server.research_picks(["HONDA Civic 2014-2018", "HONDA Nope 2014-2018", "???"])
    civic, nope, bad = out.picks
    assert ("Civic", 2012, 2020) in calls and 2016 in calls  # widened by 2 years; fuel for the middle year
    assert civic.years and civic.fuel.source.id == "honda:civic:2016"
    assert nope.years is None and nope.fuel and "no such model" in nope.errors[0]
    assert bad.errors
