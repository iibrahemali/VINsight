"""User-provided evidence: links, PDFs and text files, turned into text the agent can read.

Link fetching is best-effort. Marketplace and Craigslist block scrapers, so a blocked fetch comes
back as a card that says so, and the user can paste the listing text instead.
"""

import asyncio
import io
import ipaddress
import json
import re
import socket
import uuid
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from vinsight import vin as vinlib

MAX_FETCH_BYTES = 5_000_000
MAX_TEXT_CHARS = 12_000  # per attachment, as stored
PROMPT_CHARS = 6_000  # per attachment, as sent to the model (Groq's free tier is ~8K tokens/minute)
# An honest bot UA: sites that allow bots can, and the ones that block bots are told the truth.
# Wikimedia and others reject bot UAs without a contact URL.
USER_AGENT = "VINsightBot/0.1 (+https://github.com/iibrahemali/VINsight; used-car research assistant; reads one page a user shared)"


class AttachmentError(ValueError):
    pass


@dataclass
class Attachment:
    id: str
    kind: Literal["link", "pdf", "text"]
    name: str
    text: str = ""
    url: str | None = None
    title: str | None = None
    site: str | None = None
    description: str | None = None
    image: str | None = None
    pages: int | None = None
    # "partial": the site only gave bots a preview (title, summary, image), not the full page.
    status: Literal["ok", "partial", "blocked", "empty"] = "ok"
    note: str | None = None
    vins: list[str] = field(default_factory=list)
    structured: dict = field(default_factory=dict)  # vehicle details from the page's schema.org data

    def card(self) -> dict:
        """What the UI shows: everything but the full text."""
        return {
            "id": self.id, "kind": self.kind, "name": self.name, "url": self.url, "title": self.title,
            "site": self.site, "description": self.description, "image": self.image, "pages": self.pages,
            "status": self.status, "note": self.note, "chars": len(self.text), "vins": self.vins,
            "excerpt": self.text[:220],
        }

    def for_prompt(self) -> str:
        head = f'<document id="{self.id}" kind="{self.kind}" name="{self.name}"'
        if self.url:
            head += f' url="{self.url}"'
        body = self.text[:PROMPT_CHARS] + ("\n[…truncated]" if len(self.text) > PROMPT_CHARS else "")
        if self.status == "partial":
            body = f"[Preview only: {self.note}]\n{body}"
        elif self.status != "ok":
            body = f"[Could not read this {self.kind}: {self.note}]"
        return f"{head}>\n{body}\n</document>"


class AttachmentStore:
    """In memory, like sessions: this is a single-player tool."""

    def __init__(self):
        self._items: dict[str, Attachment] = {}

    def add(self, item: Attachment) -> Attachment:
        item.vins = vinlib.find_vins(f"{item.title or ''}\n{item.text}")[:3]
        self._items[item.id] = item
        return item

    def get_many(self, ids: list[str]) -> list[Attachment]:
        return [self._items[i] for i in ids if i in self._items]


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def from_pdf(data: bytes, name: str, url: str | None = None) -> Attachment:
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        pages = len(reader.pages)
    except Exception as e:
        raise AttachmentError(f"could not read PDF: {e}") from e
    text = _clean_text(text)
    item = Attachment(id=_new_id(), kind="pdf", name=name, text=text, url=url, pages=pages,
                      title=(reader.metadata.title if reader.metadata and reader.metadata.title else None))
    if not text:
        item.status, item.note = "empty", "no selectable text (it may be a scanned image)"
    return item


def from_text(data: bytes, name: str) -> Attachment:
    text = data.decode("utf-8", errors="replace")
    if name.lower().endswith((".html", ".htm")):
        return _from_html(text, name=name, url=None)
    return Attachment(id=_new_id(), kind="text", name=name, text=_clean_text(text))


def from_upload(data: bytes, filename: str, content_type: str | None) -> Attachment:
    if len(data) > MAX_FETCH_BYTES:
        raise AttachmentError("file is larger than 5 MB")
    lower = filename.lower()
    if lower.endswith(".pdf") or content_type == "application/pdf":
        return from_pdf(data, filename)
    if lower.endswith((".txt", ".md", ".csv", ".html", ".htm", ".json")) or (content_type or "").startswith("text/"):
        return from_text(data, filename)
    raise AttachmentError("supported files: PDF, TXT, MD, CSV, HTML")


_VEHICLE_TYPES = {"car", "vehicle", "motorvehicle", "product", "individualproduct", "offer", "busorcoach"}


def _ld_objects(value):
    """Flatten JSON-LD into its objects (handles lists and @graph)."""
    if isinstance(value, list):
        for v in value:
            yield from _ld_objects(v)
    elif isinstance(value, dict):
        yield value
        yield from _ld_objects(value.get("@graph", []))


def _name(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value not in (None, "") else None


def json_ld_vehicle(soup: BeautifulSoup) -> dict:
    """Vehicle facts from schema.org JSON-LD, which many dealer and listing sites embed for search
    engines. More reliable than scraping the visible text when it's there."""
    found: dict = {}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _ld_objects(data):
            types = obj.get("@type", [])
            types = {t.lower() for t in (types if isinstance(types, list) else [types]) if isinstance(t, str)}
            if not types & _VEHICLE_TYPES:
                continue
            offer = obj.get("offers") or (obj if "offer" in types else {})
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            mileage = obj.get("mileageFromOdometer")
            fields = {
                "name": _name(obj.get("name")),
                "make": _name(obj.get("brand") or obj.get("manufacturer")),
                "model": _name(obj.get("model")),
                "year": _name(obj.get("vehicleModelDate") or obj.get("modelDate") or obj.get("productionDate")),
                "vin": _name(obj.get("vehicleIdentificationNumber")),
                "mileage": _name(mileage),
                "mileage_unit": _name(mileage.get("unitCode")) if isinstance(mileage, dict) else None,
                "price": _name(offer.get("price")) if isinstance(offer, dict) else None,
                "currency": _name(offer.get("priceCurrency")) if isinstance(offer, dict) else None,
                "condition": _name(obj.get("itemCondition")),
                "body": _name(obj.get("bodyType")),
                "engine": _name(obj.get("vehicleEngine")),
                "transmission": _name(obj.get("vehicleTransmission")),
                "color": _name(obj.get("color")),
                "seller": _name(offer.get("seller")) if isinstance(offer, dict) else None,
            }
            for key, value in fields.items():
                if value and key not in found:
                    found[key] = value.rsplit("/", 1)[-1] if key == "condition" else value
    return found


def _from_html(html: str, name: str, url: str | None) -> Attachment:
    soup = BeautifulSoup(html, "html.parser")

    def meta(*keys: str) -> str | None:
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    title = meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    description = meta("og:description", "description", "twitter:description")
    image = meta("og:image", "twitter:image")
    site = meta("og:site_name")
    structured = json_ld_vehicle(soup)  # before scripts are stripped below
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"]):
        tag.decompose()
    text = _clean_text(soup.get_text("\n"))
    if url and image:
        image = urljoin(url, image)
    return Attachment(id=_new_id(), kind="link" if url else "text", name=name, url=url, title=title,
                      description=description, image=image, site=site, text=text, structured=structured)


async def _check_public(url: str) -> None:
    """Refuse URLs that resolve to private, loopback or link-local addresses (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AttachmentError("only http(s) links are supported")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise AttachmentError(f"can't resolve {parsed.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise AttachmentError("that address isn't on the public internet")


async def from_url(url: str, client: httpx.AsyncClient | None = None) -> Attachment:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    site = urlparse(url).hostname or url
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT})
    try:
        current = url
        for _ in range(5):  # follow redirects by hand so every hop passes the SSRF check
            await _check_public(current)
            async with client.stream("GET", current, follow_redirects=False) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code in (401, 403, 429, 451) or resp.status_code >= 500:
                    return _blocked(url, site, f"{site} refused automated access (HTTP {resp.status_code})")
                if resp.status_code >= 400:
                    return _blocked(url, site, f"{site} returned HTTP {resp.status_code}")
                data = bytearray()
                async for chunk in resp.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_FETCH_BYTES:
                        return _blocked(url, site, "page is larger than 5 MB")
                ctype = resp.headers.get("content-type", "").lower()
                break
        else:
            return _blocked(url, site, "too many redirects")
    except httpx.HTTPError as e:
        return _blocked(url, site, f"couldn't reach {site} ({type(e).__name__})")
    finally:
        if own_client:
            await client.aclose()

    if "pdf" in ctype or current.lower().endswith(".pdf"):
        item = from_pdf(bytes(data), name=urlparse(current).path.rsplit("/", 1)[-1] or site, url=url)
        item.kind, item.site = "link", site
        return item
    item = _from_html(bytes(data).decode("utf-8", errors="replace"), name=site, url=url)
    item.site = item.site or site
    if len(item.text) < 200:
        # JavaScript-only pages and bot walls (Facebook Marketplace, many dealer sites) still serve a
        # link preview: title, summary, image, sometimes structured data. Keep that rather than
        # throwing it away: "2010 BMW 328i" alone is enough to look up recalls.
        preview = preview_text(item)
        if preview:
            item.text = preview
            item.status = "partial"
            item.note = f"{site} only shows bots a preview (title and summary). Paste the full listing text for a complete check."
        else:
            item.status = "blocked" if len(item.text) < 40 else "empty"
            item.note = f"{site} didn't return readable text to a bot. Paste the listing text instead."
    return item


def preview_text(item: Attachment) -> str:
    """The link-preview parts of a page, as text: title, summary and any structured vehicle data."""
    parts = [item.title, item.description, *(f"{k}: {v}" for k, v in item.structured.items()), item.text]
    seen: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if p and not any(p in s for s in seen):
            seen.append(p)
    text = "\n".join(seen)
    return text if (item.title or item.description or item.structured) else ""


def _blocked(url: str, site: str, note: str) -> Attachment:
    return Attachment(id=_new_id(), kind="link", name=site, url=url, site=site, status="blocked", note=note)
