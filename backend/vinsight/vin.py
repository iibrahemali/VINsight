"""Offline VIN validation (ISO 3779 / 49 CFR 565). Runs before any network call."""

import re

_TRANSLIT = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
# Finds VIN-shaped tokens inside free text (listings often embed the VIN mid-sentence).
_VIN_IN_TEXT_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def normalize(vin: str) -> str:
    return re.sub(r"[\s-]", "", vin).upper()


def check_digit(vin: str) -> str:
    total = sum(_TRANSLIT[c] * w for c, w in zip(vin, _WEIGHTS))
    rem = total % 11
    return "X" if rem == 10 else str(rem)


def validate(vin: str) -> list[str]:
    """Return a list of problems; empty means the VIN is structurally valid."""
    vin = normalize(vin)
    if len(vin) != 17:
        return [f"VIN must be 17 characters, got {len(vin)}"]
    if not _VIN_RE.match(vin):
        bad = sorted({c for c in vin if c in "IOQ" or not c.isalnum()})
        return [f"VIN contains invalid characters: {', '.join(bad)} (I, O and Q are never used)"]
    expected = check_digit(vin)
    if vin[8] != expected:
        # Non-North-American VINs don't always use a check digit, so this is a warning, not proof of fraud.
        return [f"check digit mismatch: position 9 is '{vin[8]}', expected '{expected}'"]
    return []


def find_vins(text: str) -> list[str]:
    return list(dict.fromkeys(_VIN_IN_TEXT_RE.findall(text.upper())))
