"""Thin async client for NHTSA's free public APIs (vPIC, recalls, complaints)."""

import asyncio
from collections import Counter, defaultdict

import httpx

from vinsight import vin as vinlib
from vinsight.config import settings
from vinsight.models import (
    ComplaintSample,
    ComplaintsSummary,
    ComponentCount,
    ModelYearComparison,
    Recall,
    RecallsResult,
    Source,
    Vehicle,
    YearStats,
)


class NHTSAError(RuntimeError):
    pass


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in ("", "Not Applicable", "0") else value


def _shorten(text: str | None, limit: int) -> str | None:
    """Trim long NHTSA prose at a word boundary. Tool results are resent on every step of a turn,
    so their size drives both latency and token cost; the full text is one click away on nhtsa.gov."""
    text = (text or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _defect(summary: str | None) -> str:
    """NHTSA summaries open with "<Maker> is recalling certain <long list of models>." and only
    then describe the defect. Drop that opening sentence when it's the boilerplate."""
    text = (summary or "").strip()
    first, sep, rest = text.partition(". ")
    if sep and " recalling " in first and rest:
        return rest
    return text


def _engine(r: dict) -> str | None:
    parts = []
    if disp := _clean(r.get("DisplacementL")):
        parts.append(f"{float(disp):.1f}L")
    if cyl := _clean(r.get("EngineCylinders")):
        parts.append(f"{cyl}-cyl")
    if config := _clean(r.get("EngineConfiguration")):
        parts.append(config)
    if hybrid := _clean(r.get("ElectrificationLevel")):
        parts.append(hybrid)
    return " ".join(parts) or None


class NHTSAClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(timeout=settings.http_timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "NHTSAClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            # The recalls/complaints APIs answer 400 for an unknown make/model/year.
            if e.response.status_code in (400, 404):
                return {}
            raise NHTSAError(f"NHTSA returned HTTP {e.response.status_code} for {url}") from e
        except httpx.HTTPError as e:
            raise NHTSAError(f"could not reach NHTSA ({type(e).__name__})") from e

    async def decode_vin(self, vin: str) -> Vehicle:
        vin = vinlib.normalize(vin)
        warnings = vinlib.validate(vin)
        # A bad check digit is only a warning (some imports skip it); a malformed VIN can't be decoded.
        if warnings and not warnings[0].startswith("check digit"):
            raise NHTSAError(warnings[0])

        data = await self._get(f"{settings.nhtsa_vpic_url}/DecodeVinValues/{vin}", {"format": "json"})
        results = data.get("Results") or []
        if not results:
            raise NHTSAError("vPIC returned no decode result")
        r = results[0]

        # vPIC ErrorCode "0" is a clean decode; anything else is a partial decode with caveats.
        error_codes = {c.strip() for c in (r.get("ErrorCode") or "0").split(",")}
        if error_codes - {"0"} and (text := _clean(r.get("ErrorText"))):
            warnings.append(f"vPIC: {text}")

        year = _clean(r.get("ModelYear"))
        return Vehicle(
            vin=vin,
            year=int(year) if year else None,
            make=_clean(r.get("Make")),
            model=_clean(r.get("Model")),
            trim=_clean(r.get("Trim")) or _clean(r.get("Series")),
            body_class=_clean(r.get("BodyClass")),
            engine=_engine(r),
            drive_type=_clean(r.get("DriveType")),
            transmission=_clean(r.get("TransmissionStyle")),
            fuel_type=_clean(r.get("FuelTypePrimary")),
            plant_country=_clean(r.get("PlantCountry")),
            warnings=list(dict.fromkeys(warnings)),
            source=Source(
                type="nhtsa_vpic",
                id=vin,
                url=f"https://vpic.nhtsa.dot.gov/decoder/Decoder?VIN={vin}",
            ),
        )

    async def get_recalls(self, make: str, model: str, year: int) -> RecallsResult:
        data = await self._get(
            f"{settings.nhtsa_api_url}/recalls/recallsByVehicle",
            {"make": make, "model": model, "modelYear": year},
        )
        recalls = [
            Recall(
                campaign_number=r["NHTSACampaignNumber"],
                report_date=r.get("ReportReceivedDate"),
                component=r.get("Component") or "UNKNOWN",
                summary=_shorten(_defect(r.get("Summary")), 180) or "",
                consequence=_shorten(_clean(r.get("Consequence")), 120),
                park_it=bool(r.get("parkIt")),
                park_outside=bool(r.get("parkOutSide")),
                # No url: it's derivable from the campaign number, and 24 copies of it add up.
                source=Source(type="nhtsa_recall", id=r["NHTSACampaignNumber"]),
            )
            for r in data.get("results") or []
        ]
        return RecallsResult(make=make, model=model, year=year, count=len(recalls), recalls=recalls)

    async def get_complaints(
        self, make: str, model: str, year: int, top_n: int = 6, samples_per_component: int = 1
    ) -> ComplaintsSummary:
        data = await self._get(
            f"{settings.nhtsa_api_url}/complaints/complaintsByVehicle",
            {"make": make, "model": model, "modelYear": year},
        )
        rows = data.get("results") or []

        # One complaint can name several components; count it under each.
        stats: dict[str, Counter] = defaultdict(Counter)
        by_component: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            for comp in {c.strip() for c in (row.get("components") or "UNKNOWN").split(",") if c.strip()}:
                s = stats[comp]
                s["complaints"] += 1
                s["crashes"] += bool(row.get("crash"))
                s["fires"] += bool(row.get("fire"))
                s["injuries"] += row.get("numberOfInjuries") or 0
                s["deaths"] += row.get("numberOfDeaths") or 0
                by_component[comp].append(row)

        top = sorted(stats.items(), key=lambda kv: kv[1]["complaints"], reverse=True)[:top_n]
        samples = []
        for comp, _ in top:
            # Prefer the most severe narratives as samples: crashes and fires first, then the longest.
            ranked = sorted(
                by_component[comp],
                key=lambda r: (bool(r.get("crash")) or bool(r.get("fire")), len(r.get("summary") or "")),
                reverse=True,
            )
            for row in ranked[:samples_per_component]:
                odi = str(row["odiNumber"])
                samples.append(
                    ComplaintSample(
                        odi_number=odi,
                        date_filed=row.get("dateComplaintFiled"),
                        components=row.get("components") or "",
                        summary=_shorten(row.get("summary"), 400) or "",
                        crash=bool(row.get("crash")),
                        fire=bool(row.get("fire")),
                        source=Source(type="nhtsa_complaint", id=odi),
                    )
                )

        return ComplaintsSummary(
            make=make,
            model=model,
            year=year,
            total=len(rows),
            crashes=sum(bool(r.get("crash")) for r in rows),
            fires=sum(bool(r.get("fire")) for r in rows),
            injuries=sum(r.get("numberOfInjuries") or 0 for r in rows),
            deaths=sum(r.get("numberOfDeaths") or 0 for r in rows),
            top_components=[ComponentCount(component=c, **dict(s)) for c, s in top],
            samples=samples,
            source=Source(
                type="nhtsa_complaint",
                id=f"complaints:{make}:{model}:{year}".lower(),
                url=f"https://www.nhtsa.gov/vehicle/{year}/{make}/{model}".replace(" ", "%20"),
            ),
        )

    async def compare_model_years(self, make: str, model: str, start_year: int, end_year: int) -> ModelYearComparison:
        if end_year < start_year:
            start_year, end_year = end_year, start_year
        # Cap the work (2 NHTSA calls per year): a wider range keeps its most recent 12 years.
        clamped = end_year - start_year > 11
        if clamped:
            start_year = end_year - 11
        limit = asyncio.Semaphore(4)  # be polite to NHTSA

        async def one(year: int) -> YearStats:
            async with limit:
                recalls, complaints = await asyncio.gather(
                    self.get_recalls(make, model, year), self.get_complaints(make, model, year, top_n=3, samples_per_component=0)
                )
            return YearStats(
                year=year,
                recalls=recalls.count,
                complaints=complaints.total,
                crashes=complaints.crashes,
                fires=complaints.fires,
                injuries=complaints.injuries,
                deaths=complaints.deaths,
                top_components=[c.component for c in complaints.top_components],
                recall_campaigns=[r.campaign_number for r in recalls.recalls],
            )

        years = await asyncio.gather(*(one(y) for y in range(start_year, end_year + 1)))
        comparison = ModelYearComparison(
            make=make,
            model=model,
            years=list(years),
            source=Source(
                type="nhtsa_complaint",
                id=f"years:{make}:{model}:{start_year}-{end_year}".lower(),
                url=f"https://www.nhtsa.gov/vehicle/{end_year}/{make}/{model}".replace(" ", "%20"),
            ),
        )
        if clamped:
            comparison.note = f"Only the 12 most recent years asked for ({start_year}-{end_year}) were compared. " + comparison.note
        return comparison
