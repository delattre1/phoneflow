# tests/test_api.py
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from pf_api.driver import FakeDriver
from pf_api.server import make_server

SETTINGS = {
    "id": "wf_settings_general",
    "name": "Open Settings → General",
    "version": 1,
    "nodes": [
        {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
        {"id": "n2", "type": "phone.openApp", "position": {"x": 0, "y": 1}, "params": {"app": "Settings"}},
    ],
    "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
}

def _boot(tmp_path: Path, latch=True):
    drv = FakeDriver(ocr_text="Settings")
    httpd = make_server(tmp_path, drv, latch_ok=lambda: latch)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    return httpd, port, drv

def test_health(tmp_path):
    httpd, port, _ = _boot(tmp_path, latch=False)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/health")
        body = json.loads(c.getresponse().read())
        assert body == {"ok": True, "latch": "down"}
    finally:
        httpd.shutdown()

def test_put_get_workflow(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS), {"Content-Type": "application/json"})
        assert c.getresponse().status == 200
        c.request("GET", "/api/workflows/wf_settings_general")
        got = json.loads(c.getresponse().read())
        assert got["name"] == SETTINGS["name"]
    finally:
        httpd.shutdown()

def test_put_extra_key_400(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        bad = dict(SETTINGS)
        bad["nodes"] = [dict(SETTINGS["nodes"][0]), dict(SETTINGS["nodes"][1])]
        bad["nodes"][1] = dict(bad["nodes"][1])
        bad["nodes"][1]["params"] = {"app": "Settings", "token": "abc"}
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(bad), {"Content-Type": "application/json"})
        resp = c.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read())
        assert body["code"] == "validation"
    finally:
        httpd.shutdown()

def test_run_and_get(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS), {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_settings_general"}), {"Content-Type": "application/json"})
        run_id = json.loads(c.getresponse().read())["runId"]
        c.request("GET", f"/api/runs/{run_id}")
        run = json.loads(c.getresponse().read())
        assert run["status"] == "succeeded"
        assert "vault" not in json.dumps(run).lower() or "vaultItemId" in json.dumps(run)
    finally:
        httpd.shutdown()
