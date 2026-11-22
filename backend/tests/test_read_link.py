"""read_link: one tool call that reads a page, extracts vehicle data and X-rays listings."""

import pytest
from mcp import Client

from vinsight import attachments, mcp_server
from vinsight.mcp_server import server

DEALER = """<html><head><title>2003 Honda Accord EX | Main St Motors</title>
<script type="application/ld+json">{"@type":"Car","name":"2003 Honda Accord EX","brand":{"name":"Honda"},
"model":"Accord","vehicleModelDate":"2003","vehicleIdentificationNumber":"1HGCM82633A004352",
"mileageFromOdometer":{"value":"148000","unitCode":"SMI"},"offers":{"price":"3200","priceCurrency":"USD"}}</script>
</head><body><p>One owner, clean title, new tires. Financing available.</p></body></html>"""

SCAM = """<html><head><title>2015 Toyota Camry - $4,500 (sfbay)</title></head><body>
<p>2015 Toyota Camry SE, only 20,000 miles. Deployed overseas, I will ship it to you.</p>
<p>Pay a deposit to hold it. VIN 1HGCM82633A004352</p></body></html>"""


@pytest.fixture(autouse=True)
def fake_web(monkeypatch):
    mcp_server._LINK_CACHE.clear()
    calls = []

    async def from_url(url, client=None):
        calls.append(url)
        if "blocked" in url:
            return attachments._blocked(url, "www.facebook.com", "www.facebook.com refused automated access (HTTP 403)")
        html = DEALER if "dealer" in url else SCAM
        page = attachments._from_html(html, name="example.com", url=url)
        page.site = "example.com"
        return page

    monkeypatch.setattr(attachments, "from_url", from_url)
    return calls


async def read(url, **kw):
    async with Client(server) as mcp:
        r = await mcp.call_tool("read_link", {"url": url, **kw})
    assert not r.is_error, r.content
    return r.structured_content


async def test_dealer_page_structured_data_and_clean_xray():
    r = await read("https://example.com/dealer/accord")
    assert r["structured"]["vin"] == "1HGCM82633A004352" and r["structured"]["price"] == "3200"
    assert r["listing"]["vin_check"]["matches"] is True
    assert r["source"]["type"] == "link" and len(r["source"]["id"]) == 8


async def test_scam_listing_is_xrayed_in_the_same_call():
    r = await read("https://sfbay.example.com/cto/camry")
    codes = {f["code"] for f in r["listing"]["flags"]}
    assert {"vin_mismatch", "shipping_only", "deposit_first"} <= codes
    assert r["listing"]["verdict_floor"] == "AVOID"


async def test_blocked_site_tells_the_agent_what_to_do():
    r = await read("https://www.facebook.com/marketplace/item/blocked")
    assert r["status"] == "blocked" and "refused" in r["note"] and r["listing"] is None


async def test_repeat_reads_are_cached(fake_web):
    await read("https://example.com/dealer/accord")
    await read("https://example.com/dealer/accord")
    assert len(fake_web) == 1


def test_focus_keeps_the_relevant_paragraphs():
    text = "\n".join(["Lorem ipsum filler " * 20] * 10 + ["Price: $3,200. Mileage 148k. Clean title."])
    out, truncated = mcp_server._focus(text, "price mileage title", 200)
    assert truncated and "Price: $3,200" in out


async def test_search_results_pages_are_not_xrayed(monkeypatch):
    rows = "".join(f"<p>20{10 + i} Honda Civic ${5000 + i * 700:,}</p>" for i in range(8))
    html = f"<html><head><title>cars & trucks for sale</title></head><body>{rows}</body></html>"

    async def from_url(url, client=None):
        return attachments._from_html(html, name="x", url=url)

    monkeypatch.setattr(attachments, "from_url", from_url)
    r = await read("https://sfbay.example.com/search/cta")
    assert r["status"] == "ok" and r["listing"] is None


def test_preview_only_pages_keep_title_and_summary():
    html = """<html><head><meta property="og:title" content="2010 BMW 328i Premium - $6,500">
    <meta property="og:description" content="Runs great, 118k miles, clean title. Pickup in Oakland.">
    <meta property="og:image" content="https://example.com/car.jpg"></head><body><div id="root"></div></body></html>"""
    page = attachments._from_html(html, name="www.facebook.com", url="https://www.facebook.com/marketplace/item/1")
    assert len(page.text) < 200  # what a bot sees of a JavaScript app
    text = attachments.preview_text(page)
    assert "2010 BMW 328i" in text and "118k miles" in text


async def test_read_link_uses_previews_and_xrays_them(monkeypatch):
    html = """<html><head><meta property="og:title" content="2010 BMW 328i Premium - $6,500">
    <meta property="og:description" content="Rebuilt title, 118k miles. Cash only."></head><body></body></html>"""

    async def from_url(url, client=None):
        page = attachments._from_html(html, name="www.facebook.com", url=url)
        page.text, page.status, page.note = attachments.preview_text(page), "partial", "preview only"
        return page

    monkeypatch.setattr(attachments, "from_url", from_url)
    r = await read("https://www.facebook.com/marketplace/item/1")
    assert r["status"] == "partial" and "BMW 328i" in r["text"]
    assert r["listing"]["make"] == "BMW" and "rebuilt_title" in {f["code"] for f in r["listing"]["flags"]}
