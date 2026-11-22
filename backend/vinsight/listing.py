"""Listing X-ray: deterministic red-flag extraction and a rule-based verdict floor.

The rules live in code, not in the prompt, so they're testable and can't be talked around: the
model explains the verdict and may make it stricter, never more lenient. Prices are extracted but
not judged: there is no fair-price data yet, and the tool says so rather than guessing.
"""

import re
from datetime import date

from vinsight import vin as vinlib
from vinsight.models import ListingAnalysis, ListingFlag, Source

__all__ = ["ListingFlag", "extract", "verdict_floor"]

MAKES = {
    "acura", "audi", "bmw", "buick", "cadillac", "chevrolet", "chevy", "chrysler", "dodge", "fiat", "ford",
    "genesis", "gmc", "honda", "hyundai", "infiniti", "jaguar", "jeep", "kia", "land rover", "lexus", "lincoln",
    "mazda", "mercedes", "mercedes-benz", "mini", "mitsubishi", "nissan", "porsche", "ram", "subaru", "tesla",
    "toyota", "volkswagen", "vw", "volvo", "scion", "saturn", "pontiac", "mercury", "suzuki", "saab", "hummer",
}
MAKE_ALIASES = {"chevy": "chevrolet", "vw": "volkswagen", "mercedes": "mercedes-benz"}

# (code, severity, label, pattern, why). Severity "high" forces AVOID, "medium" forces CAUTION.
RULES: list[tuple[str, str, str, str, str]] = [
    ("salvage_title", "high", "Salvage title", r"\bsalvage(d)?\b",
     "Declared a total loss by an insurer. Hard to insure, finance or resell."),
    ("flood", "high", "Flood damage", r"\bflood(ed)?\b|\bwater damage\b",
     "Flood damage causes electrical and corrosion problems that surface for years."),
    ("no_title", "high", "No title / title problem",
     r"\bno title\b|\bwithout (a )?title\b|\btitle (is )?(lost|missing)\b|\bbill of sale only\b|\btitle (is )?not in my name\b|\bopen title\b",
     "Without a clean title in the seller's name you may not be able to register the car."),
    ("parts_only", "high", "Parts only", r"\b(for|as) parts( only)?\b|\bparts car\b",
     "Sold as parts: not meant to be driven."),
    ("deposit_first", "high", "Deposit before viewing",
     r"\bdeposit\b.{0,40}\b(hold|before|first|secure)\b|\b(hold|secure) (it|the car) with\b",
     "Asking for money before you've seen the car is the most common used-car scam."),
    ("shipping_only", "high", "Shipping / seller away",
     r"\b(ship|shipping|deliver(y)?) (only|to you|it to you)\b|\b(deployed|overseas|out of (the )?(country|state))\b|\bmilitary\b.{0,30}\b(transfer|deployed)\b",
     "A seller who can't meet and will 'ship it' is a classic fake-listing pattern."),
    ("odd_payment", "high", "Unusual payment",
     r"\bgift cards?\b|\bwire transfer\b|\bwestern union\b|\bzelle only\b|\bcrypto(currency)?\b|\bbitcoin\b|\b(ebay|amazon|google) (motors )?(escrow|protection)\b",
     "Gift cards, wires, crypto and fake 'escrow' are untraceable: scammers ask for them."),
    ("rebuilt_title", "medium", "Rebuilt / reconstructed title",
     r"\brebuilt\b|\breconstructed\b|\brestored title\b|\bbranded title\b|\brebuild(able)?\b",
     "Was a total loss and repaired. Can be fine, but needs a careful inspection and is worth much less."),
    ("lien", "medium", "Lien on title", r"\blien\b|\bstill (owe|financed)\b|\bpayoff\b",
     "Someone else has a claim on the car until the loan is paid off. Pay the lender, not the seller."),
    ("not_running", "medium", "Not running reliably",
     r"\bwon'?t (start|run)\b|\bdoesn'?t (start|run)\b|\bnot running\b|\bruns sometimes\b|\bneeds (a )?(jump|tow)\b|\bdead battery\b",
     "Starting problems can be cheap or very expensive; you can't tell without a mechanic."),
    ("mechanic_special", "medium", "Needs work",
     r"\bmechanic'?s? special\b|\bneeds (some )?work\b|\bneeds (a )?(new )?(engine|transmission|trans)\b|\bproject car\b",
     "Budget for the repair at its worst-case cost, not the seller's estimate."),
    ("warning_light", "medium", "Warning light on",
     r"\bcheck engine( light)?\b|\bcel\b|\bengine light\b|\babs light\b|\bairbag light\b|\bsrs light\b",
     "A warning light can mean anything from a loose cap to a failing engine. Get it scanned."),
    ("transmission", "medium", "Transmission issue",
     r"\b(slipping|slips|hard shift(s|ing)?|jerk(s|y)?|delayed engagement)\b|\btransmission (issue|problem|noise)\b",
     "Transmission repairs often cost more than an older car is worth."),
    ("odometer", "medium", "Mileage not actual",
     r"\btmu\b|\btrue mileage unknown\b|\bnot actual (miles|mileage)\b|\bmileage (is )?exempt\b|\bodometer (broken|rolled|replaced)\b",
     "If the true mileage is unknown, so is most of the car's wear."),
    ("no_test_drive", "medium", "No test drive",
     r"\bno test drives?\b|\btest drives? (only )?after (payment|purchase)\b",
     "Never buy a car you haven't driven."),
    ("accident", "low", "Accident history", r"\baccident\b|\bfender bender\b|\bfront[- ]end damage\b|\bframe damage\b",
     "Ask what was damaged and who repaired it. Frame damage is serious."),
    ("as_is", "low", "Sold as-is", r"\bas[- ]is\b",
     "Normal for private sales, but it means no warranty: inspect before you pay."),
    ("cash_only", "low", "Cash only", r"\bcash only\b",
     "Common, but meet at a bank so you can verify the title and pay safely."),
    ("pressure", "low", "Pressure tactics",
     r"\bfirst (come|\$\d+)\b.{0,20}\b(first served|takes it)\b|\bmust sell (today|asap|now)\b|\bmany (people|buyers) interested\b|\bno (lowball|low ?ballers)\b",
     "Urgency is a tactic. A good car will still be a good car tomorrow."),
]
_COMPILED = [(code, sev, label, re.compile(pat, re.I | re.S), why) for code, sev, label, pat, why in RULES]

_PRICE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d{3,6})(?:\.\d{2})?"
    r"|\b(\d{1,3}(?:\.\d)?)\s?k\b(?!\s*(?:mi|miles|km))"
    r"|\b(\d{1,3}(?:,\d{3})+|\d{3,6})\s?(?:dollars|usd|obo)\b",
    re.I,
)
# The "148k" form must come first, or "148k miles" would match as 148.
_MILES_RE = re.compile(r"\b(\d{1,3}(?:\.\d)?\s?k|\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:mi\b|miles\b|mileage\b)", re.I)
_YMM_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\s+([A-Za-z][A-Za-z-]+(?:\s+(?:Rover|Benz))?)\s+([A-Za-z0-9][\w-]*)")


def _quote(text: str, m: re.Match, width: int = 70) -> str:
    start, end = max(0, m.start() - width), min(len(text), m.end() + width)
    snippet = text[start:end].replace("\n", " ").strip()
    return ("…" if start else "") + snippet + ("…" if end < len(text) else "")


def _number(raw: str) -> int | None:
    raw = raw.lower().replace(",", "").replace(" ", "")
    try:
        return int(float(raw[:-1]) * 1000) if raw.endswith("k") else int(raw)
    except ValueError:
        return None


def _negated(text: str, m: re.Match) -> bool:
    """'no accidents', 'clean title, not salvage', 'never flooded' should not raise a flag."""
    before = text[max(0, m.start() - 25):m.start()].lower()
    return bool(re.search(r"\b(no|not|never|zero|without|free of)\b[\w\s,/-]{0,15}$", before))


def extract(text: str) -> ListingAnalysis:
    flags: list[ListingFlag] = []
    for code, severity, label, pattern, why in _COMPILED:
        m = pattern.search(text)
        # "never flooded", "no accidents", "not salvage" describe the car's absence of a problem.
        if m and not _negated(text, m):
            flags.append(ListingFlag(code=code, severity=severity, label=label, quote=_quote(text, m), why=why))

    def _price(m: re.Match) -> int | None:
        return _number(m.group(1) or m.group(3) or f"{m.group(2)}k")

    price = next((p for m in _PRICE_RE.finditer(text) if (p := _price(m)) and 300 <= p <= 500_000), None)
    miles = next((n for m in _MILES_RE.finditer(text) if (n := _number(m.group(1))) is not None and n < 1_000_000), None)

    year = make = model = None
    for m in _YMM_RE.finditer(text):
        candidate = m.group(2).lower()
        if candidate in MAKES:
            year, make, model = int(m.group(1)), MAKE_ALIASES.get(candidate, candidate).title(), m.group(3)
            break

    if year and miles is not None:
        age = max(1, date.today().year - year)
        if age >= 5 and miles / age < 3_000:
            flags.append(ListingFlag(
                code="low_miles", severity="low", label="Unusually low mileage",
                quote=f"{miles:,} miles on a {year}", why="Possible, but verify with service records or a history report: odometer fraud targets this.",
            ))

    vins = vinlib.find_vins(text)
    if not vins:
        flags.append(ListingFlag(code="no_vin", severity="low", label="No VIN in listing",
                                 quote="", why="Ask for the VIN before visiting. A seller who won't share it is a red flag."))

    return ListingAnalysis(
        price=price, mileage=miles, year=year, make=make, model=model, vin=vins[0] if vins else None,
        flags=flags, source=Source(type="listing", id="listing"),
    )


SEVERITY_RANK = {"high": 2, "medium": 1, "low": 0}


def verdict_floor(analysis: ListingAnalysis) -> None:
    """Set the strictest verdict the rules require. BUY is never a rule outcome: the absence of
    red flags in text is not evidence that a car is good."""
    worst = max((SEVERITY_RANK[f.severity] for f in analysis.flags), default=-1)
    analysis.verdict_floor = "AVOID" if worst == 2 else "CAUTION" if worst == 1 else "NONE"
    analysis.floor_reasons = [f.label for f in analysis.flags if SEVERITY_RANK[f.severity] == worst and worst >= 1]
