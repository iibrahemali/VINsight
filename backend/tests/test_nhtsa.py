import pytest
from mcp import Client

from vinsight.mcp_server import server
from vinsight.nhtsa import NHTSAClient, NHTSAError

VIN = "1HGCM82633A004352"


async def test_decode_vin():
    async with NHTSAClient() as client:
        v = await client.decode_vin(VIN)
    assert (v.year, v.make, v.model, v.trim) == (2003, "HONDA", "Accord", "EX-V6")
    assert v.engine == "3.0L 6-cyl V-Shaped"
    assert v.warnings == []
    assert v.source.id == VIN


async def test_malformed_vin_fails_before_network():
    async with NHTSAClient() as client:
        with pytest.raises(NHTSAError, match="17 characters"):
            await client.decode_vin("1HGCM826")


async def test_recalls_carry_campaign_numbers():
    async with NHTSAClient() as client:
        r = await client.get_recalls("HONDA", "Accord", 2003)
    assert r.count == len(r.recalls) == 24
    takata = next(x for x in r.recalls if x.campaign_number == "19V182000")
    assert takata.source.type == "nhtsa_recall"
    assert takata.source.id == "19V182000"
    assert "AIR BAGS" in takata.component


async def test_unknown_vehicle_returns_empty_not_error():
    async with NHTSAClient() as client:
        r = await client.get_recalls("NOPE", "Nothing", 1999)
    assert r.count == 0 and r.recalls == []


async def test_complaints_aggregate_by_component():
    async with NHTSAClient() as client:
        c = await client.get_complaints("HONDA", "Accord", 2003)
    assert c.total == 300  # fixture is trimmed to 300 rows
    counts = [x.complaints for x in c.top_components]
    assert counts == sorted(counts, reverse=True)
    assert c.top_components[0].complaints > 0
    assert all(s.source.type == "nhtsa_complaint" and s.odi_number for s in c.samples)


async def test_compare_model_years():
    async with NHTSAClient() as client:
        c = await client.compare_model_years("HONDA", "Accord", 2004, 2002)  # reversed range is fine
    assert [y.year for y in c.years] == [2002, 2003, 2004]
    y2003 = c.years[1]
    assert y2003.recalls == 24 and y2003.complaints == 300 and "19V182000" in y2003.recall_campaigns
    assert c.years[0].complaints == 0  # no fixture: NHTSA has nothing on record


async def test_compare_model_years_keeps_the_12_most_recent_years_of_a_wide_range():
    async with NHTSAClient() as client:
        c = await client.compare_model_years("HONDA", "Accord", 1995, 2010)
    assert [y.year for y in c.years] == list(range(1999, 2011))
    assert c.note.startswith("Only the 12 most recent years")


async def test_mcp_server_exposes_tools_and_readable_errors():
    async with Client(server) as mcp:
        names = {t.name for t in (await mcp.list_tools()).tools}
        assert names == {"decode_vin", "get_recalls", "get_complaints", "compare_model_years", "analyze_listing", "read_link", "get_fuel_economy", "research_picks"}

        ok = await mcp.call_tool("decode_vin", {"vin": VIN})
        assert not ok.is_error and ok.structured_content["model"] == "Accord"

        bad = await mcp.call_tool("decode_vin", {"vin": "1HGCM82633A00435O"})
        assert bad.is_error
        assert "invalid characters" in bad.content[0].text
