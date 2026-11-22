"""Offline test setup: NHTSA traffic is served from recorded fixtures, never the network."""

import json
from pathlib import Path

import httpx
import pytest

from vinsight import nhtsa

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_for(request: httpx.Request) -> Path | None:
    path, params = request.url.path, request.url.params
    if "/DecodeVinValues/" in path:
        return FIXTURES / f"vpic_{path.rsplit('/', 1)[-1]}.json"
    if path.endswith("/recallsByVehicle") or path.endswith("/complaintsByVehicle"):
        kind = "recalls" if "recalls" in path else "complaints"
        key = f"{params['make']}_{params['model']}_{params['modelYear']}".lower().replace(" ", "-")
        return FIXTURES / f"{kind}_{key}.json"
    return None


def _handler(request: httpx.Request) -> httpx.Response:
    fixture = _fixture_for(request)
    if fixture and fixture.exists():
        return httpx.Response(200, json=json.loads(fixture.read_text()))
    if "DecodeVinValues" in request.url.path:
        return httpx.Response(200, json={"Results": [{"ErrorCode": "11", "ErrorText": "11 - Incorrect Model Year"}]})
    # Mirrors NHTSA: unknown vehicles get an empty result set.
    return httpx.Response(200, json={"Count": 0, "count": 0, "results": []})


@pytest.fixture(autouse=True)
def offline_nhtsa(monkeypatch):
    original = nhtsa.NHTSAClient.__init__

    def patched(self, transport=None):
        original(self, transport=transport or httpx.MockTransport(_handler))

    monkeypatch.setattr(nhtsa.NHTSAClient, "__init__", patched)
