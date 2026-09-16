import base64
import json

import pytest

from pf_api.driver import DriverError
from pf_mirror.scripts.mirror import LOCK_RE, LatchDriver, click_script, window_script


def _rpc_result(result, req_id=1, headers=None):
    if isinstance(result, dict) and isinstance(result.get("bytes"), bytes):
        result = {**result, "bytes": base64.b64encode(result["bytes"]).decode()}
    return 200, dict(headers or {}), json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


class FakeTransport:
    """Records POSTs and answers scripted JSON-RPC responses."""

    def __init__(self, handler=None):
        self.calls = []
        self.headers = {}
        self.handler = handler or (lambda method, params, body: {"tools": []})

    def post(self, url, headers, body):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        msg = json.loads(body)
        if "id" not in msg:  # notification: real servers answer 202 empty
            return 202, dict(self.headers), ""
        if "result" in msg or "error" in msg:
            return 202, dict(self.headers), ""
        if msg.get("method") == "initialize":
            self.headers["mcp-session-id"] = "sess-1"
            return _rpc_result({"serverInfo": {"name": "plow"}}, msg["id"], self.headers)
        if msg.get("method") == "tools/list":
            try:
                result = self.handler("tools/list", msg.get("params") or {}, msg)
            except Exception:
                result = {"tools": [{"name": "plow_run_command"}, {"name": "plow_read_file"}]}
            return _rpc_result(result, msg["id"], self.headers)
        result = self.handler(msg.get("method"), msg.get("params") or {}, msg)
        return _rpc_result(result, msg.get("id"), self.headers)



def test_window_script_targets_mirror_process():
    src = window_script()
    assert "iPhone Mirroring" in src
    assert "System Events" in src


def test_click_script_uses_titlebar_offset():
    src = click_script(0.5, 0.5, titlebar_px=28)
    assert "28" in src
    assert "0.5" in src


def test_lock_re_matches_pt_and_en():
    assert LOCK_RE.search("Enter Password")
    assert LOCK_RE.search("Senha do Mac")
    assert LOCK_RE.search("Código")
    assert not LOCK_RE.search("General")


def test_latch_driver_open_app_records_script_without_osascript():
    drv = LatchDriver(transport=FakeTransport())
    drv.open_app("Settings")
    assert ("open_app", "Settings") in drv.commands
    assert any("Settings" in src for src in drv.scripts)


def test_latch_driver_initialize_reuses_session_id():
    tr = FakeTransport()
    drv = LatchDriver(transport=tr, agent_token="tok")
    assert drv.health() is True
    assert tr.headers.get("mcp-session-id") == "sess-1"
    init_calls = [c for c in tr.calls if json.loads(c["body"]).get("method") == "initialize"]
    assert len(init_calls) == 1
    assert init_calls[0]["headers"].get("Authorization") == "Bearer tok"
    later = [c for c in tr.calls if json.loads(c["body"]).get("method") != "initialize"]
    assert later and all(c["headers"].get("mcp-session-id") == "sess-1" for c in later)


def test_latch_driver_tools_call_happy_path():
    def handler(method, params, msg):
        if method == "tools/list":
            return {"tools": [{"name": "plow_run_command"}]}
        if method == "tools/call":
            assert params["name"] == "plow_run_command"
            return {"content": [{"type": "text", "text": "ok"}]}
        raise AssertionError(method)

    tr = FakeTransport(handler)
    drv = LatchDriver(transport=tr, agent_token="tok")
    drv.health()
    out = drv.run_applescript('tell application "System Events" to key code 123')
    assert out == "ok"
    call = [c for c in tr.calls if json.loads(c["body"]).get("method") == "tools/call"]
    assert call and call[0]["headers"].get("Authorization") == "Bearer tok"


def test_latch_driver_disconnected_on_connection_error():
    class DeadTransport:
        def post(self, url, headers, body):
            raise OSError("connection refused")

    drv = LatchDriver(transport=DeadTransport())
    assert drv.health() is False
    with pytest.raises(DriverError) as e:
        drv.open_app("Settings")
    assert e.value.code == "latch_disconnected"


def test_latch_driver_unconfigured_raises_disconnected():
    drv = LatchDriver(transport=None)
    assert drv.health() is False
    with pytest.raises(DriverError) as e:
        drv.screenshot()
    assert e.value.code == "latch_disconnected"


def test_latch_driver_screenshot_via_bridge():
    def handler(method, params, msg):
        if method == "tools/call" and params["name"] == "plow_run_command":
            return {"content": [{"type": "text", "text": "iPhone Mirroring\t0,0\t400,600"}]}
        if method == "tools/call" and params["name"] == "plow_read_file":
            return {"bytes": b"\x89PNG\r\n\x1a\n"}
        raise AssertionError(method)

    tr = FakeTransport(handler)
    drv = LatchDriver(transport=tr, agent_token="tok")
    drv.health()
    assert drv.screenshot() == b"\x89PNG\r\n\x1a\n"


def test_latch_driver_tap_label_missing_fails_element_not_found():
    def handler(method, params, msg):
        if method == "tools/call" and params["name"] == "plow_run_command":
            return {"content": [{"type": "text", "text": "iPhone Mirroring\t0,0\t400,600"}]}
        if method == "tools/call" and params["name"] == "plow_read_file":
            return {"bytes": b"frame"}
        raise AssertionError(method)

    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    with pytest.raises(DriverError) as e:
        drv.tap_label("NoSuchLabel")
    assert e.value.code == "element_not_found"


def test_latch_driver_vault_fill_is_manual():
    drv = LatchDriver(transport=FakeTransport())
    with pytest.raises(DriverError) as e:
        drv.vault_fill("item_1")
    assert e.value.code == "vault_manual"


def test_latch_driver_does_not_set_ocr_text():
    drv = LatchDriver(transport=FakeTransport())
    assert not hasattr(drv, "ocr_text")
