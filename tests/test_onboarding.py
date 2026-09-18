# tests/test_onboarding.py — the doctor, owner settings, owner hints, prebuilt helpers.
import hashlib
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from pf_api import doctor, settings
from pf_api.driver import FakeDriver
from pf_api.server import make_server

ROOT = Path(__file__).resolve().parents[1]


def _boot(tmp_path):
    httpd = make_server(tmp_path, FakeDriver(), latch_ok=lambda: True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _req(port, method, path, body=None):
    c = HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    return r.status, (json.loads(raw) if raw else None)


class SickDriver:
    def doctor(self):
        return {"latch": True, "helpers": True, "accessibility": True,
                "screenRecording": False, "automation": True, "mirrorWindow": None, "detail": {}}


def test_doctor_names_the_first_missing_thing_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONEFLOW_HOME", str(tmp_path))
    monkeypatch.setenv("PHONEFLOW_LLM_API_KEY", "k")
    out = doctor.run(SickDriver())
    assert out["ready"] is False
    assert out["next"]["id"] == "screenRecording"
    assert out["next"]["pane"] == "screenRecording"
    assert "Screen" in out["next"]["fix"]


def test_doctor_asks_for_the_llm_key_first(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONEFLOW_HOME", str(tmp_path))
    monkeypatch.delenv("PHONEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert doctor.run(FakeDriver())["next"]["id"] == "llmKey"


def test_config_saves_the_key_and_never_echoes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    httpd, port = _boot(tmp_path)
    try:
        assert _req(port, "GET", "/api/config")[1]["llmApiKeySet"] is False
        status, body = _req(port, "PUT", "/api/config", {"llmApiKey": "sk-secret", "blockedApps": "Nubank, Wallet"})
        assert status == 200 and body["llmApiKeySet"] is True
        assert "sk-secret" not in json.dumps(body)
        assert body["blockedApps"] == ["nubank", "wallet"]
        assert settings.llm_api_key() == "sk-secret"
        assert (tmp_path / "config.json").stat().st_mode & 0o777 == 0o600
        assert _req(port, "GET", "/api/doctor")[1]["ready"] is True
        assert _req(port, "PUT", "/api/config", {"nope": 1})[0] == 400
    finally:
        httpd.shutdown()


def test_owner_hint_replaces_the_bundled_one_and_delete_restores_it(tmp_path):
    httpd, port = _boot(tmp_path)
    try:
        assert {"app": "x", "source": "bundled"} in _req(port, "GET", "/api/hints")[1]
        assert _req(port, "PUT", "/api/hints/x", {"text": "- my own note about X"})[0] == 200
        assert _req(port, "GET", "/api/hints/x")[1] == {
            "app": "x", "source": "owner", "text": "- my own note about X\n"}
        from pf_api.agent import _load_app_hints
        assert _load_app_hints("open x and post") == "- my own note about X"
        assert _req(port, "DELETE", "/api/hints/x")[0] == 204
        assert _req(port, "GET", "/api/hints/x")[1]["source"] == "bundled"
        assert _req(port, "PUT", "/api/hints/..%2Fevil", {"text": "x"})[0] == 400
    finally:
        httpd.shutdown()


def test_prebuilt_helpers_match_their_sources():
    # A helper source changed without `sh mac/build.sh` being re-run: the Mac
    # would silently fall back to compiling, which a new owner cannot do.
    manifest = json.loads((ROOT / "mac/bin/manifest.json").read_text())
    sources = sorted(p.stem for p in (ROOT / "mac").glob("pf_*.swift"))
    assert sorted(manifest) == sources
    for name, entry in manifest.items():
        src = (ROOT / "mac" / f"{name}.swift").read_text(encoding="utf-8")
        assert entry["src"] == hashlib.sha256(src.encode("utf-8")).hexdigest()[:16], name
        blob = (ROOT / "mac/bin" / name).read_bytes()
        assert entry["sha256"] == hashlib.sha256(blob).hexdigest(), name


def test_without_an_own_key_the_planner_uses_the_agents_plow_credits(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONEFLOW_HOME", str(tmp_path))
    for var in ("PHONEFLOW_LLM_API_KEY", "OLLAMA_API_KEY", "PHONEFLOW_LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PLOW_AGENT_TOKEN", "plow-tok")
    monkeypatch.setenv("PLOW_API_BASE", "https://api.plow.co/")
    assert settings.llm_provider() == "plow"
    assert settings.llm_api_key() == "plow-tok"
    assert settings.llm_base_url() == "https://api.plow.co/v1"
    assert doctor.run(FakeDriver())["checks"][0]["ok"] is True
    # an owner's own key takes over, endpoint included
    settings.save_config(tmp_path, {"llmApiKey": "sk-own"})
    assert settings.llm_provider() == "own"
    assert settings.llm_base_url() == "https://ollama.com/v1"


def test_model_names_resolve_to_the_endpoints_spelling():
    served = ["anthropic/claude-sonnet-5", "z-ai/glm-5.3-flash", "z-ai/glm-5.3", "qwen/qwen3.5-397b"]
    assert settings.resolve_model(None, "glm-5.3", served) == "z-ai/glm-5.3"
    assert settings.resolve_model(None, "glm-5.3-flash", served) == "z-ai/glm-5.3-flash"
    assert settings.resolve_model(None, "qwen3.5:397b", served) == "qwen/qwen3.5-397b"
    assert settings.resolve_model(None, "z-ai/glm-5.3", served) == "z-ai/glm-5.3"
    assert settings.resolve_model(None, "kimi-k3", served) == "kimi-k3"
