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

CONFIRM = {
    "id": "wf_confirm_park",
    "name": "confirm park",
    "version": 1,
    "nodes": [
        {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
        {"id": "n2", "type": "flow.confirm", "position": {"x": 0, "y": 1}, "params": {"prompt": "Continue?"}},
        {"id": "n3", "type": "phone.openApp", "position": {"x": 0, "y": 2}, "params": {"app": "Settings"}},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2"},
        {"id": "e2", "source": "n2", "target": "n3"},
    ],
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

def test_confirm_deny_on_succeeded_rejected(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS), {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_settings_general"}), {"Content-Type": "application/json"})
        run_id = json.loads(c.getresponse().read())["runId"]
        c.request("POST", f"/api/runs/{run_id}/confirm", json.dumps({"decision": "deny"}), {"Content-Type": "application/json"})
        resp = c.getresponse()
        assert resp.status in (400, 409)
        resp.read()
        c.request("GET", f"/api/runs/{run_id}")
        run = json.loads(c.getresponse().read())
        assert run["status"] == "succeeded"
    finally:
        httpd.shutdown()

def test_cancel_then_confirm_does_not_resume(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_confirm_park", json.dumps(CONFIRM), {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_confirm_park"}), {"Content-Type": "application/json"})
        run_id = json.loads(c.getresponse().read())["runId"]
        c.request("GET", f"/api/runs/{run_id}")
        assert json.loads(c.getresponse().read())["status"] == "awaiting_confirm"
        c.request("POST", f"/api/runs/{run_id}/cancel")
        assert c.getresponse().status == 200
        c.request("POST", f"/api/runs/{run_id}/confirm", json.dumps({"decision": "approve"}), {"Content-Type": "application/json"})
        resp = c.getresponse()
        assert resp.status != 200
        resp.read()
        c.request("GET", f"/api/runs/{run_id}")
        run = json.loads(c.getresponse().read())
        assert run["status"] == "cancelled"
    finally:
        httpd.shutdown()


def test_hostile_origin_rejected(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/health", headers={"Origin": "http://evil.com:8787"})
        resp = c.getresponse()
        assert resp.status == 403
        resp.read()
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS),
                  {"Content-Type": "application/json", "Origin": "http://evil.com:8787"})
        resp = c.getresponse()
        assert resp.status == 403
        resp.read()
    finally:
        httpd.shutdown()


def test_same_origin_accepted(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/health", headers={"Origin": f"http://127.0.0.1:{port}"})
        resp = c.getresponse()
        assert resp.status == 200
        resp.read()
    finally:
        httpd.shutdown()


def test_phoneflow_origin_env_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONEFLOW_ORIGIN", "https://canvas.example")
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/health", headers={"Origin": "https://canvas.example"})
        resp = c.getresponse()
        assert resp.status == 200
        resp.read()
    finally:
        httpd.shutdown()


def test_rebinding_origin_with_matching_host_rejected(tmp_path, monkeypatch):
    # DNS-rebinding shape: Origin netloc equals Host netloc, but Host is not the
    # configured PHONEFLOW_ORIGIN — must still be denied.
    monkeypatch.setenv("PHONEFLOW_ORIGIN", "https://canvas.example")
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/health", headers={"Host": "evil.com:8787", "Origin": "http://evil.com:8787"})
        resp = c.getresponse()
        assert resp.status == 403
        resp.read()
    finally:
        httpd.shutdown()


def test_events_sse_streams_existing_then_terminal_close(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_confirm_park", json.dumps(CONFIRM), {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_confirm_park"}), {"Content-Type": "application/json"})
        run_id = json.loads(c.getresponse().read())["runId"]
        c.close()

        c = HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", f"/api/runs/{run_id}/events")
        resp = c.getresponse()
        assert resp.getheader("Content-Type", "").startswith("text/event-stream")
        # read a few SSE lines; server closes after terminal status flush
        data = resp.read()
        assert b"data:" in data
        evs = [json.loads(ln[5:]) for ln in data.decode().splitlines() if ln.startswith("data:")]
        assert evs and evs[0]["nodeId"] == "n1" and evs[1]["nodeId"] == "n2"
        assert evs[1]["code"] == "awaiting_confirm"
    finally:
        httpd.shutdown()


def test_async_run_returns_at_once_and_is_pollable(tmp_path):
    import time
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS), {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_settings_general", "async": True}), {"Content-Type": "application/json"})
        resp = c.getresponse()
        started = json.loads(resp.read())
        assert resp.status == 202 and started["status"] == "running"
        status = None
        for _ in range(100):
            c.request("GET", f"/api/runs/{started['runId']}")
            status = json.loads(c.getresponse().read())["status"]
            if status != "running":
                break
            time.sleep(0.05)
        assert status == "succeeded"
    finally:
        httpd.shutdown()


def test_second_run_is_refused_while_one_holds_the_phone(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        for wf in (CONFIRM, SETTINGS):
            c.request("PUT", f"/api/workflows/{wf['id']}", json.dumps(wf), {"Content-Type": "application/json"})
            c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_confirm_park"}), {"Content-Type": "application/json"})
        parked = json.loads(c.getresponse().read())
        assert parked["status"] == "awaiting_confirm"
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_settings_general"}), {"Content-Type": "application/json"})
        resp = c.getresponse()
        body = json.loads(resp.read())
        assert resp.status == 409 and body["code"] == "busy" and body["runId"] == parked["runId"]
        # cancelling the holder frees the phone
        c.request("POST", f"/api/runs/{parked['runId']}/cancel", "{}", {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_settings_general"}), {"Content-Type": "application/json"})
        resp = c.getresponse()
        assert resp.status == 200 and json.loads(resp.read())["status"] == "succeeded"
    finally:
        httpd.shutdown()
