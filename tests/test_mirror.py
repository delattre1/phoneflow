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


def _envelope(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj)}]}


def test_latch_driver_screenshot_via_bridge():
    # Mirrors the real Latch: a binary read answers only a byte count, so the
    # driver must stage a base64 copy and read that instead.
    png = b"\x89PNG\r\n\x1a\n"

    def handler(method, params, msg):
        name = params.get("name")
        if name == "plow_run_applescript":
            return _envelope({"exit_code": 0, "output": ""})
        if name == "plow_read_file":
            path = params["arguments"]["path"]
            if path.endswith(".b64"):
                return _envelope({"content": base64.b64encode(png).decode()})
            return _envelope({"bytes": len(png)})
        raise AssertionError(name)

    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    assert drv.screenshot() == png


def test_latch_driver_read_file_returns_text_inline():
    def handler(method, params, msg):
        if params.get("name") == "plow_read_file":
            return _envelope({"content": "hello\n"})
        raise AssertionError(params.get("name"))

    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    assert drv.read_file("/tmp/x.txt") == b"hello\n"


def _ocr_handler(lines, binary_ready=True):
    """Fake Latch that answers the screenshot, the OCR probe and the OCR run."""

    def handler(method, params, msg):
        if method == "tools/call" and params["name"] == "plow_run_applescript":
            script = params["arguments"]["script"]
            if "test -x" in script:
                out = "yes" if binary_ready else "no"
            elif "sips" in script:
                out = ""
            elif "position" in script:
                out = "iPhone Mirroring, 100, 100, 300, 600"
            elif "pf_ocr" in script:
                # The binary reports boxes normalised to the cropped window; the
                # fixture takes screen points and converts, so the tests stay
                # readable in the coordinates that actually get clicked.
                norm = [
                    {"t": l["t"], "nx": (l["x"] - 100) / 300.0, "ny": (l["y"] - 100) / 600.0}
                    for l in lines
                ]
                out = json.dumps({"w": 900, "h": 1800, "sw": 900, "sh": 1800, "lines": norm})
            else:
                out = ""
            return {"content": [{"type": "text", "text": json.dumps({"exit_code": 0, "output": out})}]}
        if method == "tools/call" and params["name"] == "plow_read_file":
            return {"bytes": b"frame"}
        if method == "tools/call" and params["name"] == "plow_write_file":
            return _envelope({"status": "completed"})
        raise AssertionError(params.get("name", method))

    return handler


def test_latch_driver_tap_label_missing_fails_element_not_found():
    handler = _ocr_handler([])

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


def test_latch_driver_tap_label_clicks_ocr_coordinates():
    # Window is at (100,100) sized 300x600, so (250,400) sits inside it.
    handler = _ocr_handler([{"t": "General", "x": 250, "y": 400}])
    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    drv.tap_label("General")
    assert ("tap_label", "General") in drv.commands
    # Must go through the CGEvent helper: System Events' `click at` posts no
    # real mouse event and the mirrored phone ignores it.
    clicks = [c for c in drv.scripts if "pf_drag" in c]
    assert clicks and "250" in clicks[-1] and "400" in clicks[-1]
    assert not [c for c in drv.scripts if "click at" in c]


def test_latch_driver_tap_point_refuses_a_target_off_the_phone():
    # OCR can no longer offer a desktop target (the crop bounds it), but a
    # coordinate reaching tap_point directly still has to be refused.
    handler = _ocr_handler([])
    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    with pytest.raises(DriverError) as e:
        drv.tap_point(900, 50)
    assert e.value.code == "tap_out_of_bounds"


def test_latch_driver_ocr_caches_on_frame_bytes():
    handler = _ocr_handler([{"t": "Wi-Fi", "x": 250, "y": 400}])
    tr = FakeTransport(handler)
    drv = LatchDriver(transport=tr, agent_token="tok")
    drv.health()
    assert drv.ocr(b"frame-a") == "Wi-Fi"
    runs = lambda: len([c for c in tr.calls if "pf_ocr" in c["body"]])
    first = runs()
    drv.ocr(b"frame-a")          # same frame: served from cache
    assert runs() == first
    drv.ocr(b"frame-b")          # new frame: re-runs on the Mac
    assert runs() == first + 1


def test_latch_driver_swipe_uses_the_trackpad_scroll_helper():
    # A pressed-mouse drag grabs videos and selects text; scrolling goes through
    # pf_scroll, which posts a real trackpad scroll gesture with phase+momentum.
    handler = _ocr_handler([])
    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    drv.swipe({"x": 0.5, "y": 0.75}, {"x": 0.5, "y": 0.25}, 400)
    sent = drv.scripts[-1]
    assert "pf_scroll" in sent
    assert "CGEventPost" not in sent


def test_latch_driver_focuses_the_window_before_tapping():
    # An unfocused mirror window consumes the first click to raise itself, so a
    # tap without a preceding focus is spent on the window, not the phone.
    drv = LatchDriver(transport=FakeTransport(_ocr_handler([])), agent_token="tok")
    drv.health()
    drv.tap_point(200, 300)
    joined = "\n".join(drv.scripts)
    assert "frontmost" in joined
    assert joined.index("frontmost") < joined.index("pf_drag")


def test_latch_driver_refuses_taps_in_the_title_bar_band():
    drv = LatchDriver(transport=FakeTransport(_ocr_handler([])), agent_token="tok")
    drv.health()
    with pytest.raises(DriverError):
        drv.tap_point(150, 110)  # window top is 100, title bar is 28pt: this is the Mac


def test_helper_paths_resolve_against_the_macs_home_not_the_containers():
    # The agent runs as root in a container; expanding "~" locally gives /root,
    # which does not exist on the owner's Mac.
    written = []

    def handler(method, params, msg):
        name = params.get("name")
        if name == "plow_run_applescript":
            script = params["arguments"]["script"]
            out = "/Users/owner" if "echo $HOME" in script else ("no" if "test -x" in script else "")
            return _envelope({"exit_code": 0, "output": out})
        if name == "plow_write_file":
            written.append(params["arguments"]["path"])
            return _envelope({"status": "completed"})
        raise AssertionError(name)

    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    drv._ensure_ocr()
    assert written and all(p.startswith("/Users/owner/") for p in written), written


def test_a_new_mac_gets_prebuilt_helpers_and_never_needs_swiftc():
    # The first tap on a fresh Mac must not depend on the Xcode Command Line
    # Tools: every helper has a checked-in binary that matches its source.
    scripts, written = [], []

    def handler(method, params, msg):
        name = params.get("name")
        if name == "plow_run_applescript":
            script = params["arguments"]["script"]
            scripts.append(script)
            if "echo $HOME" in script:
                out = "/Users/owner"
            elif "base64 -D" in script:
                out = "yes"
            elif "test -x" in script:
                out = "no"
            else:
                out = ""
            return _envelope({"exit_code": 0, "output": out})
        if name == "plow_write_file":
            written.append(params["arguments"]["path"])
            return _envelope({"status": "completed"})
        raise AssertionError(name)

    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    drv._ensure_ocr()
    assert not any("swiftc" in s for s in scripts)
    assert "/Users/owner/.phoneflow/pf_idle.b64" in written
    assert not any(p.endswith(".swift") for p in written)


def test_actions_wait_for_the_owner_and_hand_the_focus_back():
    drv = LatchDriver(transport=FakeTransport(_ocr_handler([])), agent_token="tok")
    drv.health()
    drv.tap_point(200, 300)
    sent = drv.scripts[-1]
    assert sent.index("pf_idle") < sent.index("frontmost") < sent.index("pf_drag")
    assert sent.rindex("pfPrev to true") > sent.index("pf_drag")


def test_doctor_reprobes_helpers_instead_of_trusting_the_cache():
    probes = []

    def handler(method, params, msg):
        name = params.get("name")
        if name == "plow_run_applescript":
            script = params["arguments"]["script"]
            if "test -x" in script:
                probes.append(script)
                return _envelope({"exit_code": 0, "output": "yes"})
            if "echo $HOME" in script:
                return _envelope({"exit_code": 0, "output": "/Users/owner"})
            if "perms" in script:
                return _envelope({"exit_code": 0, "output": '{"accessibility":true,"screenRecording":true}'})
            return _envelope({"exit_code": 0, "output": "iPhone Mirroring, 100, 100, 300, 600"})
        if name == "plow_write_file":
            return _envelope({"status": "completed"})
        raise AssertionError(name)

    drv = LatchDriver(transport=FakeTransport(handler), agent_token="tok")
    drv.health()
    drv._ensure_ocr()
    n = len(probes)
    assert drv.doctor()["helpers"] is True
    assert len(probes) > n
