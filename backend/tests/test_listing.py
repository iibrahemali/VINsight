"""Listing X-ray: extraction, red flags, negation, VIN cross-check and the verdict floor."""

import pytest
from mcp import Client

from vinsight.listing import extract, verdict_floor
from vinsight.mcp_server import server


def scan(text):
    a = extract(text)
    verdict_floor(a)
    return a


def codes(a):
    return {f.code for f in a.flags}


def test_extracts_core_fields():
    a = scan("2003 Honda Accord EX V6 - $3,200. 148k miles. VIN 1HGCM82633A004352.")
    assert (a.year, a.make, a.model, a.price, a.mileage, a.vin) == (2003, "Honda", "Accord", 3200, 148_000, "1HGCM82633A004352")


@pytest.mark.parametrize(
    ("text", "price", "miles"),
    [
        ("2016 Chevy Malibu 7.5k obo 120k mi", 7500, 120_000),
        ("2010 Ford Focus, 1500 dollars, 210,000 miles", 1500, 210_000),
    ],
)
def test_price_and_mileage_formats(text, price, miles):
    a = scan(text)
    assert (a.price, a.mileage) == (price, miles)


def test_alias_makes():
    assert scan("2016 Chevy Malibu LT").make == "Chevrolet"


@pytest.mark.parametrize(
    ("text", "code", "floor"),
    [
        ("Salvage title, runs fine.", "salvage_title", "AVOID"),
        ("I'm deployed overseas, I'll ship it to you.", "shipping_only", "AVOID"),
        ("Pay with gift cards only.", "odd_payment", "AVOID"),
        ("No title, bill of sale only.", "no_title", "AVOID"),
        ("Rebuilt title after a minor accident.", "rebuilt_title", "CAUTION"),
        ("Check engine light is on.", "warning_light", "CAUTION"),
        ("TMU, odometer replaced.", "odometer", "CAUTION"),
    ],
)
def test_red_flags_set_the_floor(text, code, floor):
    a = scan(text)
    assert code in codes(a) and a.verdict_floor == floor


def test_negated_problems_are_not_flagged():
    a = scan("2012 Mazda 3, clean title, no accidents, never flooded, not salvage. VIN 1HGCM82633A004352")
    assert codes(a) == set() and a.verdict_floor == "NONE"


def test_quotes_point_at_the_listing_text():
    a = scan("Great car. Rebuilt title from a 2019 accident. Priced to sell.")
    flag = next(f for f in a.flags if f.code == "rebuilt_title")
    assert "Rebuilt title" in flag.quote


def test_low_flags_never_raise_the_floor():
    a = scan("2015 Honda Civic, sold as-is, cash only. VIN 1HGCM82633A004352")
    assert {"as_is", "cash_only"} <= codes(a) and a.verdict_floor == "NONE"


def test_buy_is_never_a_rule_outcome():
    assert scan("Perfect car, one owner, full records.").verdict_floor in ("NONE", "CAUTION", "AVOID")


async def test_vin_that_belongs_to_another_car_is_a_scam_flag():
    text = "2015 Toyota Camry, $4,500. VIN 1HGCM82633A004352"
    async with Client(server) as mcp:
        r = (await mcp.call_tool("analyze_listing", {"text": text})).structured_content
    assert r["vin_check"]["matches"] is False and "2003 HONDA Accord" in r["vin_check"]["detail"]
    assert r["flags"][0]["code"] == "vin_mismatch" and r["verdict_floor"] == "AVOID"


async def test_matching_vin_passes():
    async with Client(server) as mcp:
        r = (await mcp.call_tool("analyze_listing", {"text": "2003 Honda Accord EX. VIN 1HGCM82633A004352"})).structured_content
    assert r["vin_check"]["matches"] is True and r["verdict_floor"] == "NONE"


def test_notetaker_merge_keeps_earlier_answers():
    from vinsight.models import BuyerProfile
    from vinsight.notetaker import merge

    p = merge(BuyerProfile(), {"budget": "about $12k", "must_haves": ["AWD"]})
    p = merge(p, {"climate": "snow", "must_haves": ["Bluetooth", "AWD"], "budget": None, "nonsense": 1})
    assert p.budget == "about $12k" and p.climate == "snow" and p.must_haves == ["AWD", "Bluetooth"]
    p = merge(p, {"shortlist": [{"make": "Honda", "model": "Civic", "years": "2018-2020"}]})
    p = merge(p, {"shortlist": [{"make": "Subaru", "model": "Impreza", "years": "2018-2020"}, {"model": "no make"}]})
    assert [(e.make, e.model) for e in p.shortlist] == [("Subaru", "Impreza")]


def test_spike_detection_flags_outlier_years():
    from vinsight.models import YearStats
    from vinsight.shortlist_check import spikes

    def y(year, complaints):
        return YearStats(year=year, recalls=0, complaints=complaints, crashes=0, fires=0, injuries=0, deaths=0,
                         top_components=[], recall_campaigns=[])

    assert spikes([y(2015, 381), y(2016, 1083), y(2017, 609), y(2018, 596)]) == [2016]
    assert spikes([y(2015, 40), y(2016, 90), y(2017, 35)]) == []  # under the minimum count


def test_spoken_years_become_digits():
    from vinsight.shortlist_check import _window, spoken_years_to_digits

    assert spoken_years_to_digits("a twenty twenty one Honda Fit") == "a 2021 Honda Fit"
    assert spoken_years_to_digits("twenty eighteen to twenty twenty") == "2018 to 2020"
    assert spoken_years_to_digits("two thousand nineteen") == "2019"
    assert _window("twenty eighteen to twenty twenty") == (2016, 2021)
