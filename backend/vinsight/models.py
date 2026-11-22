"""Tool output schemas. Every fact the agent may cite carries a `Source`."""

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["nhtsa_vpic", "nhtsa_recall", "nhtsa_complaint", "listing", "corpus", "estimate"]
Verdict = Literal["AVOID", "CAUTION", "NONE"]


class Source(BaseModel):
    type: SourceType
    id: str = Field(description="Stable identifier the agent cites, e.g. a recall campaign number.")
    url: str | None = None


class Vehicle(BaseModel):
    vin: str
    year: int | None
    make: str | None
    model: str | None
    trim: str | None = None
    body_class: str | None = None
    engine: str | None = None
    drive_type: str | None = None
    transmission: str | None = None
    fuel_type: str | None = None
    plant_country: str | None = None
    warnings: list[str] = Field(default_factory=list)
    source: Source


class Recall(BaseModel):
    campaign_number: str
    report_date: str | None
    component: str
    summary: str
    consequence: str | None
    park_it: bool = Field(description="NHTSA 'park it' advisory: do not drive until repaired.")
    park_outside: bool = Field(description="Fire risk: park outside until repaired.")
    source: Source


class RecallsResult(BaseModel):
    make: str
    model: str
    year: int
    count: int
    recalls: list[Recall]
    note: str = (
        "Summaries are shortened; the full text is at nhtsa.gov/recalls. Safety recall repairs are "
        "free at an authorized dealer. These recalls apply to the year/make/model. NHTSA's public API does not say whether a recall "
        "is still open on a specific VIN; check nhtsa.gov/recalls with the VIN or ask the seller for "
        "dealer service records."
    )


class ComplaintSample(BaseModel):
    odi_number: str
    date_filed: str | None
    components: str
    summary: str
    crash: bool
    fire: bool
    source: Source


class ComponentCount(BaseModel):
    component: str
    complaints: int
    crashes: int
    fires: int
    injuries: int
    deaths: int


class ComplaintsSummary(BaseModel):
    make: str
    model: str
    year: int
    total: int
    crashes: int
    fires: int
    injuries: int
    deaths: int
    top_components: list[ComponentCount]
    samples: list[ComplaintSample]
    source: Source


class YearStats(BaseModel):
    year: int
    recalls: int
    complaints: int
    crashes: int
    fires: int
    injuries: int
    deaths: int
    top_components: list[str]
    recall_campaigns: list[str]


class ModelYearComparison(BaseModel):
    make: str
    model: str
    years: list[YearStats]
    note: str = (
        "Raw counts. Popular models and older model years collect more complaints simply because "
        "more of them are on the road and for longer, so compare neighbouring years of the same "
        "model rather than different models. A year with far more complaints than the years "
        "around it is a red flag."
    )
    source: Source


class ListingFlag(BaseModel):
    code: str
    severity: Literal["high", "medium", "low"]
    label: str
    quote: str = Field(description="The listing text that triggered the flag.")
    why: str


class VinCheck(BaseModel):
    """The listing's claimed vehicle against what the VIN actually decodes to."""

    vin: str
    decoded: str | None
    matches: bool | None = Field(description="None when the listing doesn't say which car it is, or decoding failed.")
    detail: str


class ListingAnalysis(BaseModel):
    price: int | None = Field(description="Asking price in dollars, if found. Not judged: no fair-price data yet.")
    mileage: int | None
    year: int | None
    make: str | None
    model: str | None
    vin: str | None
    flags: list[ListingFlag]
    vin_check: VinCheck | None = None
    verdict_floor: Verdict = Field(
        "NONE", description="Strictest verdict the rules require. The final verdict may be stricter, never more lenient."
    )
    floor_reasons: list[str] = []
    note: str = (
        "Rule-based scan of the listing text. Absence of flags is not proof the car is good. Price is "
        "extracted but not judged (no fair-price data yet)."
    )
    source: Source


class ShortlistEntry(BaseModel):
    make: str
    model: str
    years: str = Field("", description='Recommended model years, e.g. "2018-2020"')
    why: str = ""
    # Filled in by the app (not the model) from NHTSA after the advisor names the model.
    checked: bool = False
    year_stats: list[YearStats] = []
    avoid: list[int] = Field([], description="Years in the window with a clear complaint spike")
    data_note: str | None = None


class BuyerProfile(BaseModel):
    """What the note-taker has learned about the buyer during an advisor call."""

    budget: str | None = Field(None, description='All-in budget or monthly limit, in the buyer\'s words, e.g. "about $12k cash"')
    use: str | None = Field(None, description="What the car is for: commute, family, road trips, work...")
    commute: str | None = Field(None, description='Daily driving, e.g. "40 miles a day, mostly highway"')
    passengers: str | None = None
    cargo: str | None = None
    climate: str | None = Field(None, description="Snow, heat, hills: anything that affects AWD or cooling")
    fuel: str | None = Field(None, description="Gas, hybrid, EV, or no preference")
    must_haves: list[str] = []
    dealbreakers: list[str] = []
    experience: str | None = Field(None, description="First car? Comfortable with manual? Mechanically handy?")
    timeline: str | None = None
    shortlist: list[ShortlistEntry] = []
