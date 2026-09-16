"""LatchDriver — drives the iPhone Mirroring window through the Plow relay MCP.

Bridge contract (spec §10, pf-mirror/SKILL.md):
  - Config: PLOW_MCP_URL and PLOW_AGENT_TOKEN from the environment (published
    by plow-init in the container). Missing/unreachable MCP => health() False
    and every driver call raises DriverError('latch_disconnected').
  - All Mac-side actions go through plow_run_command (osascript / screencapture)
    and plow_read_file; never SSH.
  - vault_fill is intentionally NOT automated (spec §11): the owner completes
    the Mac auth prompt manually, then approves in the canvas.
  - OCR on the real path: macOS ships no ocr CLI and Hermes-side vision is out
    of scope here, so ocr() returns ''. Consequences, accepted deliberately:
      * the interpreter's lock-screen park (_lock_park) can only fire for
        drivers that provide ocr/ocr_text (tests), never on the real path;
      * tap_label cannot locate a label by text, so it fails
        element_not_found with the last captured frame attached to the error
        (exc.frame) for the owner.

Testability: pass transport=<obj with post(url, headers, body) -> (status,
headers, body)>; the fake transport keeps every test off the network.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request

from pf_api.driver import DriverError

LOCK_RE = re.compile(r"Passcode|Touch ID|Enter Password|Código|Senha do Mac")
TITLES = ("iPhone Mirroring", "Espelhamento do iPhone")
MIRROR_TITLEBAR_PX = int(os.environ.get("MIRROR_TITLEBAR_PX", "28"))
_UNSET = object()


def window_script() -> str:
    return f'''
tell application "System Events"
  set procs to application processes whose background only is false
  repeat with p in procs
    repeat with w in windows of p
      set t to name of w as text
      if t contains "{TITLES[0]}" or t contains "{TITLES[1]}" then
        return ((id of w) as text) & tab & t & tab & (position of w as text) & tab & (size of w as text)
      end if
    end repeat
  end repeat
end tell
error "mirror_window_missing"
'''


def click_script(x: float, y: float, titlebar_px: int = 28) -> str:
    return f'''
-- absolute pixel {x},{y} (content coords already exclude the {titlebar_px}px titlebar)
tell application "System Events"
  click at {{{x}, {y}}}
end tell
'''


def screenshot_command() -> list[str]:
    return ["screencapture", "-x", "-o", "-l", "{window_id}", "{path}"]


def open_app_script(app: str) -> str:
    return f'''
-- iPhone Mirroring frontmost; type the app name, then Return on the result.
tell application "System Events"
  keystroke "{app}"
  delay 0.6
  key code 36 -- Return
end tell
'''


def drag_script(x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> str:
    return f'''
use framework "CoreGraphics"
use scripting additions
on _event(kind, x, y)
  set pt to current application's CGPointMake(x, y)
  return current application's CGEventCreateMouseEvent(missing value, kind, pt, 0)
end _event
set down to _event(1, {x1}, {y1})
current application's CGEventPost(current application's kCGHIDEventTap, down)
delay {max(duration_ms / 1000.0, 0.05)}
set drag to _event(5, {x2}, {y2})
current application's CGEventPost(current application's kCGHIDEventTap, drag)
set up to _event(2, {x2}, {y2})
current application's CGEventPost(current application's kCGHIDEventTap, up)
'''


class HttpTransport:
    """Minimal streamable-HTTP MCP transport: POST JSON-RPC, keep the session id."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token

    def post(self, url, headers, body):
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")


class LatchDriver:
    def __init__(self, titlebar_px: int = MIRROR_TITLEBAR_PX, transport=_UNSET, agent_token: str | None = None):
        self.titlebar_px = titlebar_px
        self.commands: list[tuple] = []
        self.scripts: list[str] = []
        self.agent_token = agent_token or os.environ.get("PLOW_AGENT_TOKEN") or ""
        self._session_id: str | None = None
        self._req_id = 0
        self._tools: list[str] = []
        if transport is _UNSET:
            url = os.environ.get("PLOW_MCP_URL")
            token = agent_token or os.environ.get("PLOW_AGENT_TOKEN") or ""
            transport = HttpTransport(url, token) if url else None
        self._transport = transport

    # ---- MCP plumbing -------------------------------------------------

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.agent_token:
            headers["Authorization"] = f"Bearer {self.agent_token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    @staticmethod
    def _header_get(headers: dict, name: str) -> str | None:
        lname = name.lower()
        for k, v in headers.items():
            if k.lower() == lname:
                return v
        return None

    def _post(self, payload: dict) -> dict | None:
        body = json.dumps(payload)
        status, resp_headers, resp_body = self._transport.post(self._url(), self._headers(), body)
        sid = self._header_get(resp_headers, "mcp-session-id")
        if sid:
            self._session_id = sid
        ctype = (self._header_get(resp_headers, "Content-Type") or "").lower()
        if "text/event-stream" in ctype:
            return self._from_sse(resp_body, payload.get("id"))
        if not resp_body.strip():
            return None
        try:
            msg = json.loads(resp_body)
        except json.JSONDecodeError as exc:
            raise DriverError("latch_disconnected", f"non-JSON MCP response: {exc}") from exc
        if isinstance(msg, dict) and "result" not in msg and "error" not in msg and "method" in msg:
            return None  # server-push notification; not our answer
        return self._checked(msg)
    def _from_sse(self, stream: str, want_id) -> dict | None:
        data_lines: list[str] = []
        for line in stream.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line.strip() and data_lines:
                chunk = "\n".join(data_lines)
                data_lines = []
                try:
                    msg = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if want_id is None or msg.get("id") == want_id:
                    return self._checked(msg)
        if data_lines:
            try:
                msg = json.loads("\n".join(data_lines))
                if want_id is None or msg.get("id") == want_id:
                    return self._checked(msg)
            except json.JSONDecodeError:
                pass
        raise DriverError("latch_disconnected", "SSE stream ended without a JSON-RPC response")

    def _checked(self, msg: dict) -> dict:
        if "error" in msg:
            raise DriverError("latch_disconnected", f"MCP error: {msg['error']}")
        if "result" not in msg:
            raise DriverError("latch_disconnected", f"unexpected MCP message: {msg!r}")
        return msg["result"]

    def _url(self) -> str:
        return getattr(self._transport, "url", "")

    def _rpc(self, method: str, params: dict, notify: bool = False) -> dict | None:
        if self._transport is None:
            raise DriverError("latch_disconnected", "PLOW_MCP_URL is not configured")
        payload: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self._req_id += 1
            payload["id"] = self._req_id
        try:
            return self._post(payload)
        except DriverError:
            raise
        except Exception as exc:  # transport-level failure => disconnected
            raise DriverError("latch_disconnected", str(exc)) from exc

    def _ensure_session(self) -> None:
        if self._tools:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "phoneflow", "version": "0.1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        listing = self._rpc("tools/list", {}) or {}
        self._tools = [t.get("name", "") for t in listing.get("tools", [])]

    def _call(self, tool: str, arguments: dict) -> dict:
        self._ensure_session()
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments}) or {}
        if result.get("isError"):
            raise DriverError("latch_disconnected", f"{tool} failed: {result.get('content')}")
        return result

    # ---- driver surface ------------------------------------------------

    def _text(self, result: dict) -> str:
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts)

    def run_command(self, argv: list[str]) -> str:
        result = self._call("plow_run_command", {"argv": argv})
        return self._text(result)

    def run_applescript(self, src: str) -> str:
        return self.run_command(["osascript", "-e", src])

    def read_file(self, path: str) -> bytes:
        result = self._call("plow_read_file", {"path": path})
        raw = result.get("bytes")
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str) and raw:
            try:
                return base64.b64decode(raw, validate=True)
            except Exception:
                return raw.encode("utf-8")
        text = self._text(result)
        if not text:
            return b""
        try:
            return base64.b64decode(text, validate=True)
        except Exception:
            return text.encode("utf-8")

    def _window(self) -> tuple[str | None, str, tuple[float, float], tuple[float, float]]:
        out = self.run_applescript(window_script())
        parts = [p.strip() for p in out.split("\t")]
        if len(parts) >= 4:
            wid, title, pos, size = parts[0], parts[1], parts[2], parts[3]
        elif len(parts) == 3:
            wid, title, pos, size = None, parts[0], parts[1], parts[2]
        else:
            raise DriverError("mirror_window_missing", f"cannot parse window info: {out!r}")

        def _pt(text: str) -> tuple[float, float]:
            nums = re.findall(r"-?\d+(?:\.\d+)?", text)
            if len(nums) < 2:
                raise DriverError("mirror_window_missing", f"cannot parse coords: {text!r}")
            return float(nums[0]), float(nums[1])

        return wid, title, _pt(pos), _pt(size)

    def health(self) -> bool:
        try:
            self._ensure_session()
            return True
        except Exception:
            return False

    def open_app(self, app: str) -> None:
        src = open_app_script(app)
        self.commands.append(("open_app", app))
        self.scripts.append(src)
        self.run_applescript(src)

    def screenshot(self) -> bytes:
        wid, _, _, _ = self._window()
        path = "/tmp/phoneflow.png"
        argv = ["screencapture", "-x", "-o", "-l", str(wid), path] if wid else ["screencapture", "-x", path]
        self.run_command(argv)
        return self.read_file(path)

    def ocr(self, frame: bytes) -> str:
        # macOS has no ocr CLI and Hermes-side vision is out of scope; see docstring.
        return ""

    def tap(self, x: float, y: float) -> None:
        _, _, pos, size = self._window()
        px = pos[0] + x * size[0]
        py = pos[1] + self.titlebar_px + y * max(size[1] - self.titlebar_px, 1)
        src = click_script(int(px), int(py), titlebar_px=self.titlebar_px)
        self.commands.append(("tap", x, y))
        self.scripts.append(src)
        self.run_applescript(src)

    def tap_label(self, label: str) -> None:
        # No OCR on the real path (see module docstring): locate-by-text cannot
        # resolve, so fail with the last frame attached for the owner.
        self.commands.append(("tap_label", label))
        frame = self.screenshot()
        err = DriverError("element_not_found", f"label {label!r} not found: no OCR on the latch path")
        err.frame = frame
        raise err

    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None:
        _, _, pos, size = self._window()

        def _abs(p: dict) -> tuple[float, float]:
            return (pos[0] + p["x"] * size[0], pos[1] + self.titlebar_px + p["y"] * max(size[1] - self.titlebar_px, 1))

        x1, y1 = _abs(frm)
        x2, y2 = _abs(to)
        src = drag_script(int(x1), int(y1), int(x2), int(y2), duration_ms)
        self.commands.append(("swipe", frm, to, duration_ms))
        self.scripts.append(src)
        self.run_applescript(src)

    def type_text(self, text: str) -> None:
        self.commands.append(("type_text", text))
        src = f'''
tell application "System Events"
  keystroke {json.dumps(text)}
end tell
'''
        self.scripts.append(src)
        self.run_applescript(src)

    def vault_fill(self, vault_item_id: str) -> str:
        # spec §11: the fill is manual; the owner completes the Mac auth prompt
        # and then Approves in the canvas.
        raise DriverError("vault_manual", "complete the Mac auth prompt manually, then Approve")
