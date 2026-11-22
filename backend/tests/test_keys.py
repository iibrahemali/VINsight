"""Bring-your-own keys: visitor keys override the server's, and changing them keeps the conversation."""

from starlette.requests import Request

from vinsight.keys import Keys
from vinsight.llm import ConversationRouter, Step


def request(headers: dict) -> Request:
    return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


def test_visitor_key_overrides_server_key(monkeypatch):
    monkeypatch.setattr("vinsight.keys.settings.groq_api_key", "server-groq")
    monkeypatch.setattr("vinsight.keys.settings.gemini_api_key", "server-gemini")
    req = request({"X-Groq-Key": "mine"})
    keys = Keys.from_request(req)
    assert keys.groq == "mine" and keys.gemini == "server-gemini"
    assert keys.source("groq", req) == "yours" and keys.source("gemini", req) == "server"


def test_blank_header_falls_back_to_server(monkeypatch):
    monkeypatch.setattr("vinsight.keys.settings.groq_api_key", "server-groq")
    assert Keys.from_request(request({"X-Groq-Key": "  "})).groq == "server-groq"


class Stub:
    def __init__(self, key):
        self.key, self.received = key, []

    def add_user(self, text):
        self.received.append(("user", text))

    def add_model_turn(self, step):
        self.received.append(("model", step))

    def add_tool_results(self, results):
        self.received.append(("tools", results))


def test_rebind_rebuilds_active_provider_with_history():
    made = []

    def factory(key):
        def make():
            made.append(Stub(key))
            return made[-1]
        return make

    router = ConversationRouter({"groq": factory("old")}, active="groq")
    router.add_user("hi")
    router._log.append(("model", Step(texts=["hello"])))
    router.rebind({"groq": factory("new")})
    assert made[-1].key == "new" and [k for k, _ in made[-1].received] == ["user", "model"]


def test_fix_wav_rewrites_placeholder_sizes():
    import struct

    from vinsight.voice import fix_wav

    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 24000, 48000, 2, 16)
    samples = b"\x00\x01" * 100
    broken = b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + fmt + b"data" + b"\xff\xff\xff\xff" + samples
    fixed = fix_wav(broken)
    assert int.from_bytes(fixed[4:8], "little") == len(fixed) - 8
    data_pos = fixed.index(b"data")
    assert int.from_bytes(fixed[data_pos + 4:data_pos + 8], "little") == len(samples)
