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
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

from pf_api.driver import DriverError

def _mac_dir() -> str:
    """Where the Mac-side helper sources (and prebuilt binaries) live.

    mac/ sits beside the pf_mirror package, in the repo and in the image alike.
    It is the ONLY copy: the sources used to be pasted into this module too, and
    the two drifted (pf_observe.sh lost its icon pass in one of them).
    """
    env = os.environ.get("PHONEFLOW_MAC_DIR")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "mac")


def _mac_source(name: str) -> str:
    with open(os.path.join(_mac_dir(), name), encoding="utf-8") as fh:
        return fh.read()


OCR_SWIFT = _mac_source("pf_ocr.swift")
DRAG_SWIFT = _mac_source("pf_drag.swift")
KEY_SWIFT = _mac_source("pf_key.swift")
SCROLL_SWIFT = _mac_source("pf_scroll.swift")
IDLE_SWIFT = _mac_source("pf_idle.swift")
ICONS_SWIFT = _mac_source("pf_icons.swift")
OBSERVE_SCRIPT = _mac_source("pf_observe.sh")

LOCK_RE = re.compile(r"Passcode|Touch ID|Enter Password|Código|Senha do Mac")
TITLES = ("iPhone Mirroring", "Espelhamento do iPhone")
MIRROR_TITLEBAR_PX = int(os.environ.get("MIRROR_TITLEBAR_PX", "28"))
FRAME_PATH = os.environ.get("PHONEFLOW_FRAME_PATH", "/tmp/phoneflow.png")
# Mac-side Vision OCR helper (mac/pf_ocr.swift), installed by _ensure_ocr().
OCR_DIR = os.environ.get("PHONEFLOW_OCR_DIR", "$HOME/.phoneflow")
OCR_BIN = OCR_DIR + "/pf_ocr"
OBSERVE_SH = OCR_DIR + "/pf_observe.sh"
DRAG_BIN = OCR_DIR + "/pf_drag"
ICONS_BIN = OCR_DIR + "/pf_icons"
SCROLL_BIN = OCR_DIR + "/pf_scroll"
KEY_BIN = OCR_DIR + "/pf_key"
IDLE_BIN = OCR_DIR + "/pf_idle"
# How long the owner must have left mouse and keyboard alone before an action
# borrows them, and the longest an action will wait for that gap.
IDLE_NEED_S = os.environ.get("PHONEFLOW_IDLE_NEED", "0.7")
IDLE_MAX_S = os.environ.get("PHONEFLOW_IDLE_MAX", "10")
# Owner-supplied CoreML icon detector (OmniParser icon_detect or similar).
ICON_MODEL = OCR_DIR + "/icon_detect.mlpackage"
_UNSET = object()


def window_script() -> str:
    # Per-title query using `get {name, position, size}` — the sandboxed
    # osascript path refuses `(id of w)` (-1723), so the window id comes from
    # the CGWindowID lookup in screenshot() instead when needed.
    return (
        'tell application "System Events" to get {name, position, size} of '
        'window 1 of (application process "iPhone Mirroring")'
    )






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
    # Typing alone does nothing: the iPhone has to be on the home screen with
    # Spotlight open first. iPhone Mirroring maps Cmd-1 to Home and Cmd-3 to
    # Spotlight (key codes 18 and 20), so the sequence is Home, Spotlight, type,
    # Return. Without the first two the keystrokes land on whatever the phone
    # was already showing and the app never opens.
    return f'''
tell application "System Events"
  key code 18 using command down -- Cmd-1: Home Screen
  delay 1.6
  key code 20 using command down -- Cmd-3: Spotlight
  delay 1.6
  keystroke "{app}"
  delay 2.0
  key code 36 -- Return
  delay 2.5 -- let the app finish launching before the next frame
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
        self._ocr_ready = False
        self._ocr_key: str = ""
        self._ocr_lines_cache: list[dict] = []
        self._scale: float = 0.0
        self._crop_geom: dict = {}
        self._home: str = ""
        self._observe_ready = False
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

    @staticmethod
    def _text_of(result: dict) -> str:
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts)

    def _text(self, result: dict) -> str:
        return self._text_of(result)

    def run_command(self, argv: list[str]) -> str:
        result = self._call("plow_run_command", {"argv": argv})
        return self._text(result)

    def run_applescript(self, src: str) -> str:
        return self.run_command(["osascript", "-e", src])

    @staticmethod
    def _envelope(result: dict) -> dict:
        """Parse a plow_run_applescript / plow_get_result payload.

        The tool answers a JSON envelope ({status, exit_code, output, handle}),
        but a bare-text answer is treated as finished output rather than
        crashing the whole action on a JSONDecodeError.
        """
        text = LatchDriver._text_of(result)
        if not text.strip():
            return {}
        try:
            inner = json.loads(text)
        except json.JSONDecodeError:
            return {"output": text}
        return inner if isinstance(inner, dict) else {"output": text}

    def _applescript(self, src: str) -> str:
        """Run AppleScript through Latch's dedicated plow_run_applescript tool.

        The generic plow_run_command→osascript path runs inside Latch's seatbelt
        sandbox and answers -600 "Application isn't running" for System Events
        queries; the dedicated tool does not. Returns the script's output text,
        raising DriverError(host_gate) on a nonzero exit. A pending result (the
        owner was shown an approval card) is polled via plow_get_result.
        """
        result = self._call("plow_run_applescript", {"app": "System Events", "script": src, "wait_ms": 15000})
        inner = self._envelope(result)
        if inner.get("status") == "pending":
            handle = inner.get("handle") or ""
            for _ in range(30):
                time.sleep(2)
                poll = self._call("plow_get_result", {"handle": handle})
                inner = self._envelope(poll)
                if inner.get("status") != "pending":
                    break
        if inner.get("exit_code") not in (0, None):
            raise DriverError("host_gate", inner.get("output", "applescript failed"))
        return inner.get("output", "")

    def _read(self, path: str) -> dict:
        return self._envelope(self._call("plow_read_file", {"path": path}))

    def read_file(self, path: str) -> bytes:
        """Read a file from the Mac, binary included.

        plow_read_file inlines `content` for a text file but answers only a byte
        count for a binary one — it never hands back raw bytes. So a binary read
        is staged: the Mac base64-encodes the file next to itself, and that text
        copy is what crosses the wire. Without this every frame arrived as the
        JSON summary instead of an image.
        """
        env = self._read(path)
        content = env.get("content")
        if isinstance(content, str):
            return content.encode("utf-8")
        if not env.get("bytes"):
            return b""
        staged = path + ".b64"
        self._applescript('do shell script "base64 -i %s -o %s"' % (path, staged))
        text = self._read(staged).get("content") or ""
        try:
            return base64.b64decode(text, validate=False)
        except Exception as exc:
            raise DriverError("read_failed", f"could not decode {path}: {exc}") from exc

    def _window(self) -> tuple[str | None, str, tuple[float, float], tuple[float, float]]:
        out = self._applescript(window_script())
        nums = re.findall(r"-?\d+(?:\.\d+)?", out)
        if "iPhone Mirroring" not in out and "Espelhamento" not in out or len(nums) < 4:
            raise DriverError("mirror_window_missing", f"no mirror window: {out!r}")
        # format: "iPhone Mirroring, 842, 314, 316, 696"
        title = TITLES[0] if TITLES[0] in out else TITLES[1]
        px, py, w, h = (float(n) for n in nums[:4])
        return None, title, (px, py), (w, h)

    def health(self) -> bool:
        try:
            self._ensure_session()
            return True
        except Exception:
            return False

    SETTINGS_PANES = {
        "screenRecording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    }

    def open_settings(self, pane: str) -> None:
        """Open one Privacy pane on the owner's Mac, so they only have to flip
        the switch instead of finding it."""
        if pane not in self.SETTINGS_PANES:
            raise DriverError("unknown_pane", f"no such settings pane: {pane!r}")
        self._applescript('do shell script "open \'%s\'"' % self.SETTINGS_PANES[pane])

    def doctor(self) -> dict:
        """What is and is not ready on the Mac, probed rather than assumed.

        Answers {check: True | False | None} plus "detail" strings; None means
        "could not be checked because an earlier thing is missing". Nothing here
        raises: a doctor that crashes on a sick patient is no use.
        """
        out: dict = {"latch": self.health(), "helpers": None, "accessibility": None,
                     "screenRecording": None, "automation": None, "mirrorWindow": None,
                     "detail": {}}
        if not out["latch"]:
            return out
        try:
            self._ensure_ocr()
            out["helpers"] = True
        except Exception as exc:  # noqa: BLE001
            out["helpers"] = False
            out["detail"]["helpers"] = str(exc)[:300]
        if out["helpers"]:
            try:
                perms = json.loads(self._applescript('do shell script "%s perms"' % IDLE_BIN))
                out["accessibility"] = bool(perms.get("accessibility"))
                out["screenRecording"] = bool(perms.get("screenRecording"))
            except Exception as exc:  # noqa: BLE001
                out["detail"]["permissions"] = str(exc)[:300]
        try:
            self._window()
            out["automation"] = True
            out["mirrorWindow"] = True
        except DriverError as exc:
            text = str(exc)
            if exc.code == "mirror_window_missing":
                out["automation"] = True
                out["mirrorWindow"] = False
            elif re.search(r"-1743|not authori[sz]ed|Not authori[sz]ed", text):
                out["automation"] = False
            elif re.search(r"-1719|-25211|assistive", text):
                out["automation"] = True
                out["accessibility"] = False
            else:
                out["detail"]["mirrorWindow"] = text[:300]
        except Exception as exc:  # noqa: BLE001
            out["detail"]["mirrorWindow"] = str(exc)[:300]
        return out

    def _act(self, src: str) -> None:
        """Run one input action on the mirror, politely.

        iPhone Mirroring only reacts to events that travel through the Mac's
        real cursor and keyboard focus (posting straight to its process was
        tried, for clicks, drags and scrolls, focused and not: the phone never
        saw any of them). So the owner's pointer and focus ARE borrowed, and the
        job here is to borrow them for as short a time as possible:

          1. wait until the owner has been idle for a moment (pf_idle), so the
             pointer is not yanked out of their hand mid-gesture and their
             keystrokes are not typed into the phone;
          2. remember which app was in front, bring the mirror forward — an
             unfocused mirror window eats the first click to raise itself, and
             `keystroke` goes to whatever is frontmost;
          3. do the action;
          4. give the focus back (the click helpers already put the pointer
             back where it was).

        One AppleScript, so the whole borrow is one Latch round trip.
        """
        restore = os.environ.get("PHONEFLOW_RESTORE_FOCUS", "1") != "0"
        lines = [
            'do shell script "test -x %s && %s %s %s || true"' % (IDLE_BIN, IDLE_BIN, IDLE_NEED_S, IDLE_MAX_S),
            'tell application "System Events" to set pfPrev to name of first application process whose frontmost is true',
            'tell application "System Events" to set frontmost of application process "iPhone Mirroring" to true',
            "delay 0.15",
            src.strip("\n"),
        ]
        if restore:
            lines += [
                'if pfPrev is not "iPhone Mirroring" then',
                "  try",
                '    tell application "System Events" to set frontmost of application process pfPrev to true',
                "  end try",
                "end if",
            ]
        full = "\n".join(lines)
        if self.scripts and self.scripts[-1] == src:
            self.scripts[-1] = full
        else:
            self.scripts.append(full)
        self._applescript(full)

    def open_app(self, app: str) -> None:
        src = open_app_script(app)
        self.commands.append(("open_app", app))
        self.scripts.append(src)
        self._ensure_ocr()  # builds pf_key, used to type the app name
        self._act(src)

    def screenshot(self) -> bytes:
        # plow_run_command runs inside Latch's seatbelt sandbox, where
        # screencapture never reaches the window server: it dies with exit -1
        # and writes no file, so the follow-up read fails ENOENT. The dedicated
        # AppleScript tool runs outside that sandbox, so the capture is shelled
        # out from there and macOS attributes the Screen Recording grant to
        # Latch itself.
        #
        # No window id is available on this path, so the capture is whole-screen;
        # the canvas crops nothing — the frame shows the mirrored iPhone wherever
        # the window sits on the display.
        path = FRAME_PATH
        self._applescript('do shell script "screencapture -x %s"' % path)
        return self.read_file(path)

    def _mac_home(self) -> str:
        """The Mac's home directory, asked of the Mac.

        The agent runs in a container as root, so expanding "~" locally yields
        /root — a path that does not exist on the owner's machine. Helper paths
        are written through Latch, so they must be resolved there.
        """
        if not self._home:
            self._home = self._applescript('do shell script "echo $HOME"').strip() or "/tmp"
        return self._home

    def _on_mac(self, path: str) -> str:
        return path.replace("$HOME", self._mac_home())

    def _ensure_ocr(self) -> None:
        """Compile the Vision OCR helper on the Mac if it is not there yet.

        Costs ~60s once per Mac; afterwards the binary is reused, so this is a
        no-op on every later call. Raises DriverError(ocr_unavailable) rather
        than host_gate so a missing toolchain reads differently from a denial.
        """
        if self._ocr_ready:
            return
        if not self._observe_ready:
            self._call("plow_write_file",
                       {"path": self._on_mac(OBSERVE_SH), "content": OBSERVE_SCRIPT})
            self._applescript('do shell script "mkdir -p %s && chmod +x %s"' % (OCR_DIR, OBSERVE_SH))
            self._observe_ready = True
        for binary, source, code in (
            (OCR_BIN, OCR_DIR + "/pf_ocr.swift", OCR_SWIFT),
            (DRAG_BIN, OCR_DIR + "/pf_drag.swift", DRAG_SWIFT),
            (ICONS_BIN, OCR_DIR + "/pf_icons.swift", ICONS_SWIFT),
            (SCROLL_BIN, OCR_DIR + "/pf_scroll.swift", SCROLL_SWIFT),
            (KEY_BIN, OCR_DIR + "/pf_key.swift", KEY_SWIFT),
            (IDLE_BIN, OCR_DIR + "/pf_idle.swift", IDLE_SWIFT),
        ):
            # Keyed on the source, not on the binary's existence: a Mac that
            # built an earlier pf_ocr would otherwise keep it forever, and a
            # fix to the helper would never reach the phone.
            stamp = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
            probe = self._applescript(
                'do shell script "test -x %s && test \\"$(cat %s.sha 2>/dev/null)\\" = %s && echo yes || echo no"'
                % (binary, binary, stamp)
            ).strip()
            if probe == "yes":
                continue
            if self._install_prebuilt(binary, stamp):
                continue
            self._call("plow_write_file", {"path": self._on_mac(source), "content": code})
            try:
                self._applescript(
                    'do shell script "mkdir -p %s && xcrun swiftc -O -o %s %s && echo %s > %s.sha"'
                    % (OCR_DIR, binary, source, stamp, binary)
                )
            except DriverError as exc:
                raise DriverError("ocr_unavailable", f"could not build {binary}: {exc}") from exc
        self._ocr_ready = True

    def _install_prebuilt(self, binary: str, stamp: str) -> bool:
        """Ship the checked-in binary for this helper to the Mac, if one matches.

        mac/bin/ holds universal builds of every helper (mac/build.sh), so a new
        Mac needs neither the Xcode Command Line Tools nor a minute of swiftc
        before its first tap. A prebuilt binary is used only when the manifest
        says it was built from exactly the source this driver carries; anything
        else — no manifest, a stale entry, a failed copy, a checksum mismatch —
        answers False and the caller compiles from source as before.

        plow_write_file carries text, so the binary crosses as base64 and the
        Mac decodes it and checks its sha256 before marking it installed.
        """
        name = os.path.basename(binary)
        try:
            with open(os.path.join(_mac_dir(), "bin", "manifest.json"), encoding="utf-8") as fh:
                entry = json.load(fh).get(name) or {}
            if entry.get("src") != stamp or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
                return False
            with open(os.path.join(_mac_dir(), "bin", name), "rb") as fh:
                blob = fh.read()
        except (OSError, ValueError):
            return False
        if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            return False
        staged = binary + ".b64"
        try:
            self._applescript('do shell script "mkdir -p %s"' % OCR_DIR)
            self._call("plow_write_file",
                       {"path": self._on_mac(staged), "content": base64.b64encode(blob).decode("ascii")})
            out = self._applescript(
                'do shell script "base64 -D -i %s -o %s && rm -f %s && '
                'test \\"$(shasum -a 256 %s | cut -d\' \' -f1)\\" = %s && '
                'chmod +x %s && echo %s > %s.sha && echo yes || echo no"'
                % (staged, binary, staged, binary, entry["sha256"], binary, stamp, binary)
            ).strip()
        except DriverError:
            return False
        return out == "yes"

    OCR_UPSCALE = 3

    def _ocr_lines(self, frame: bytes) -> list[dict]:
        """Text inside the mirror window, as [{"t","x","y"}] in screen points.

        The window is only a few hundred points wide, so the whole iPhone is
        rendered small and the tab-bar labels come out around 8pt — Vision reads
        the video titles fine and misses the navigation entirely, which leaves an
        agent unable to move between tabs. So the window is cropped out of the
        capture and upscaled before recognition, and the normalised boxes that
        come back are mapped onto the window's own coordinates.
        """
        key = hashlib.sha256(frame).hexdigest() if frame else ""
        if key and key == self._ocr_key:
            return self._ocr_lines_cache
        self._ensure_ocr()
        _, _, pos, size = self._window()
        scale = self._pixel_scale()
        crop = FRAME_PATH.replace(".png", "_win.png")
        big = max(size) * scale * self.OCR_UPSCALE
        self._applescript(
            'do shell script "sips -c %d %d --cropOffset %d %d %s --out %s >/dev/null '
            '&& sips -Z %d %s >/dev/null"'
            % (int(size[1] * scale), int(size[0] * scale),
               int(pos[1] * scale), int(pos[0] * scale), FRAME_PATH, crop,
               int(big), crop)
        )
        out = self._applescript('do shell script "%s %s"' % (OCR_BIN, crop))
        try:
            data = json.loads(out) or {}
        except json.JSONDecodeError:
            data = {}
        lines = []
        for l in data.get("lines") or []:
            lines.append({
                "t": l.get("t", ""),
                # Normalised against the crop, and the crop *is* the window, so
                # the fraction maps straight onto the window's own rectangle.
                "x": pos[0] + float(l.get("nx", 0)) * size[0],
                "y": pos[1] + float(l.get("ny", 0)) * size[1],
            })
        self._ocr_key, self._ocr_lines_cache = key, lines
        self._crop_geom = {
            "pos": pos, "size": size, "titlebar": self.titlebar_px,
            "px_w": int(data.get("w") or 0), "px_h": int(data.get("h") or 0),
        }
        return lines

    def observe(self) -> tuple[list[dict], bytes, dict]:
        """Capture, crop, OCR and encode in a single round trip.

        Doing this piecemeal cost about fourteen calls through the Plow relay
        per step — capture, read, base64, window, crop, OCR, JPEG, read... each
        with its own approval — and that, not the models, was most of the wall
        clock in the agent loop. Returns (items in screen points, JPEG of the
        window crop, geometry for mapping grounder boxes back).
        """
        self._ensure_ocr()
        _, _, pos, size = self._window()
        scale = self._pixel_scale()
        out = self._applescript(
            'do shell script "%s %d %d %d %d %s"'
            % (OBSERVE_SH, int(pos[0]), int(pos[1]), int(size[0]), int(size[1]), scale)
        )
        try:
            data = json.loads(out)
            ocr = data.get("ocr") or {}
            frame = base64.b64decode(data.get("frame") or "", validate=False)
        except Exception as exc:
            raise DriverError("observe_failed", f"could not parse observation: {exc}") from exc
        items = []
        for l in ocr.get("lines") or []:
            items.append({
                "t": l.get("t", ""),
                "x": pos[0] + float(l.get("nx", 0)) * size[0],
                "y": pos[1] + float(l.get("ny", 0)) * size[1],
            })
        # Local icon detections (pf_icons) map exactly like OCR: the crop is the
        # window, so a box centre normalised to the crop lands on the window
        # rectangle. Boxes in the titlebar band are Mac chrome, not the phone.
        top_frac = self.titlebar_px / max(size[1], 1)
        icons = []
        for b in (data.get("icons") or {}).get("boxes") or []:
            ny = float(b.get("ny", 0))
            if ny < top_frac:
                continue
            icons.append({
                "t": "[icon]",
                "x": pos[0] + float(b.get("nx", 0)) * size[0],
                "y": pos[1] + ny * size[1],
            })
        # Snap short text labels onto the icon directly above them. Vision reads
        # the label under an app icon or a tab-bar glyph, but the tappable target
        # is the icon — tapping the text launches nothing. If a detected icon
        # sits just above a short label and shares its column, move the label's
        # tap point up onto the icon, the way TapPointCalculator offsets labels.
        col = size[0] * 0.10
        near = size[1] * 0.14
        for it in items:
            t = it.get("t", "")
            if len(t) > 18 or "[icon]" in t:
                continue
            best, bestdy = None, near
            for ic in icons:
                dy = it["y"] - ic["y"]  # icon above label => positive
                if 0 < dy < bestdy and abs(ic["x"] - it["x"]) < col:
                    best, bestdy = ic, dy
            if best is not None:
                it["x"], it["y"] = best["x"], best["y"]
        items.extend(icons)
        geom = {"pos": pos, "size": size, "titlebar": self.titlebar_px,
                "px_w": int(ocr.get("w") or 0), "px_h": int(ocr.get("h") or 0)}
        return items, frame, geom

    def window_crop(self, frame: bytes) -> tuple[bytes, dict]:
        """The mirror window alone, upscaled, as JPEG — plus how to map it back.

        This is the same crop the OCR pass reads, handed to a vision model that
        can locate *icons*, which OCR by definition cannot. The geometry lets the
        caller turn a box in crop pixels into a screen point:
        x = pos.x + cx / px_w * size.w, and likewise for y.
        """
        self._ocr_lines(frame)  # ensures the crop exists and geometry is fresh
        crop = FRAME_PATH.replace(".png", "_win.png")
        jpg = FRAME_PATH.replace(".png", "_win.jpg")
        self._applescript(
            'do shell script "sips -s format jpeg -s formatOptions 70 %s --out %s >/dev/null"'
            % (crop, jpg)
        )
        return self.read_file(jpg), dict(self._crop_geom)

    def _pixel_scale(self) -> float:
        """Capture pixels per screen point (1 on a plain display, 2 on Retina)."""
        if self._scale:
            return self._scale
        out = self._applescript('do shell script "%s %s"' % (OCR_BIN, FRAME_PATH))
        try:
            d = json.loads(out)
            self._scale = float(d["w"]) / float(d["sw"]) if d.get("sw") else 1.0
        except Exception:
            self._scale = 1.0
        return self._scale

    def ocr(self, frame: bytes) -> str:
        return "\n".join(l.get("t", "") for l in self._ocr_lines(frame))

    def ocr_items(self, frame: bytes) -> list[dict]:
        """Recognised text inside the mirror window, with screen coordinates.

        Bounded by construction — _ocr_lines only ever looks at the cropped
        window — so nothing an agent can choose from here sits on the desktop.
        """
        return self._ocr_lines(frame)

    def _bounds_check(self, x: float, y: float) -> None:
        _, _, pos, size = self._window()
        # The band above the phone is the Mac's title bar and the Mirroring
        # toolbar: a click there closes or resizes the window, never the phone.
        top = pos[1] + self.titlebar_px
        if not (pos[0] <= x <= pos[0] + size[0] and top <= y <= pos[1] + size[1]):
            raise DriverError("tap_out_of_bounds", f"({x},{y}) is outside the phone area")

    def tap_point(self, x: float, y: float) -> None:
        """Click one absolute screen point, refusing anything off the phone."""
        self._bounds_check(x, y)
        # Two things were swallowing taps. `click at` posts no real mouse event,
        # so it never reached the phone at all; and once a real CGEvent click was
        # used, an unfocused mirror window ate the first click to raise itself.
        # Focus first, then click, or every tap is spent on the window instead of
        # the phone.
        self._ensure_ocr()
        src = 'do shell script "%s %d %d"' % (DRAG_BIN, int(x), int(y))
        self.commands.append(("tap_point", x, y))
        self.scripts.append(src)
        self._act(src)

    def frame_for_model(self, max_px: int = 900) -> bytes:
        """A downscaled copy of the last capture, for sending to a vision model.

        The raw capture is several megabytes of Retina PNG; sending that every
        step would dominate both latency and token cost, so the resize happens
        on the Mac and only the small copy crosses the wire.
        """
        # JPEG, not PNG: the frame travels as base64 text (see read_file), and a
        # PNG screenshot costs ~10x the bytes for no benefit to a vision model.
        small = FRAME_PATH.replace(".png", "_small.jpg")
        self._applescript(
            'do shell script "sips -s format jpeg -s formatOptions 70 -Z %d %s --out %s >/dev/null"'
            % (max_px, FRAME_PATH, small)
        )
        return self.read_file(small)

    def tap(self, x: float, y: float) -> None:
        _, _, pos, size = self._window()
        px = pos[0] + x * size[0]
        py = pos[1] + self.titlebar_px + y * max(size[1] - self.titlebar_px, 1)
        self.commands.append(("tap", x, y))
        self.tap_point(px, py)

    def long_press(self, x: float, y: float, hold_ms: int = 800) -> None:
        """Press and hold — how iOS opens context menus."""
        self._bounds_check(x, y)
        self._ensure_ocr()
        src = 'do shell script "%s %d %d %d"' % (DRAG_BIN, int(x), int(y), hold_ms)
        self.commands.append(("long_press", x, y))
        self.scripts.append(src)
        self._act(src)

    def swipe_from(self, x: float, y: float, direction: str, distance: float = 0.5) -> None:
        """Swipe starting at one point on screen rather than through the centre.

        A centre-anchored swipe cannot scroll anything that is not in the middle
        of the screen — a row of chips under the header, a horizontal carousel —
        because the gesture simply never touches it. Anchoring the start at a
        recognised item is what makes those regions reachable.
        """
        _, _, pos, size = self._window()
        span = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
        dx = span[0] * size[0] * distance
        dy = span[1] * size[1] * distance
        self.commands.append(("swipe_from", x, y, direction))
        self._ensure_ocr()
        # Trackpad-style scroll, not a mouse drag: the drag grabs videos and
        # selects text; a scroll-wheel gesture with phase + momentum flicks the
        # feed the way a finger does. dx/dy is the finger movement; content
        # follows it, and pf_scroll amplifies to real content distance.
        src = 'do shell script "%s %d %d %d %d 220"' % (SCROLL_BIN, int(x), int(y), int(dx), int(dy))
        self.scripts.append(src)
        self._act(src)

    def system_key(self, button: str) -> None:
        """Home / App Switcher / Spotlight, via iPhone Mirroring's own shortcuts.

        Cmd-1, Cmd-2 and Cmd-3 (key codes 18/19/20). `back` is not one of them —
        iOS has no back button, so it is the left-edge swipe below.
        """
        codes = {"home": 18, "app_switcher": 19, "spotlight": 20}
        if button not in codes:
            raise DriverError("unknown_button", f"no such system button: {button!r}")
        self.commands.append(("system_key", button))
        src = 'tell application "System Events" to key code %d using command down' % codes[button]
        self.scripts.append(src)
        self._act(src)

    def reset_app(self, app: str) -> None:
        """Force-quit the foreground app and reopen it — the way out of a stuck
        screen (a video opened by mistake, a wedged view). Opens the app
        switcher, swipes the centred card up to close it, then relaunches.
        """
        self.commands.append(("reset_app", app))
        self.system_key("app_switcher")
        time.sleep(0.6)
        _, _, pos, size = self._window()
        cx = int(pos[0] + size[0] * 0.5)
        top = pos[1] + self.titlebar_px
        y1 = int(top + (size[1] - self.titlebar_px) * 0.65)
        y2 = int(top + (size[1] - self.titlebar_px) * 0.10)
        src = 'do shell script "%s %d %d %d %d 400"' % (DRAG_BIN, cx, y1, cx, y2)
        self.scripts.append(src)
        self._act(src)
        time.sleep(0.4)
        self.system_key("home")
        time.sleep(0.3)
        self.open_app(app)

    def back(self) -> None:
        """The iOS back gesture: a swipe in from the left edge.

        There is no back shortcut in iPhone Mirroring, and without this the agent
        has no way out of a screen it opened — it has to improvise, which is how
        a run gets stuck.
        """
        _, _, pos, size = self._window()
        y = pos[1] + size[1] * 0.5
        self.commands.append(("back",))
        self._ensure_ocr()
        src = 'do shell script "%s %d %d %d %d 300"' % (
            DRAG_BIN, int(pos[0] + 3), int(y), int(pos[0] + size[0] * 0.6), int(y))
        self.scripts.append(src)
        self._act(src)

    def tap_label(self, label: str) -> None:
        self.commands.append(("tap_label", label))
        frame = self.screenshot()
        hits = [l for l in self._ocr_lines(frame) if label.lower() in (l.get("t") or "").lower()]
        if not hits:
            err = DriverError("element_not_found", f"label {label!r} not on screen")
            err.frame = frame
            raise err
        # The capture is whole-screen, so a match on the desktop behind the
        # mirror would otherwise send a click outside the phone entirely.
        _, _, pos, size = self._window()
        inside = [
            l for l in hits
            if pos[0] <= l.get("x", -1) <= pos[0] + size[0]
            and pos[1] <= l.get("y", -1) <= pos[1] + size[1]
        ]
        if not inside:
            err = DriverError("element_not_found", f"label {label!r} found only outside the mirror window")
            err.frame = frame
            raise err
        self.tap_point(inside[0]["x"], inside[0]["y"])

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 600) -> None:
        """A sustained press-and-move between two screen points — for reordering
        icons, dragging a slider, or drag-and-drop. Unlike a scroll this keeps
        the button down, so it uses the mouse-drag helper, not pf_scroll.
        """
        self.commands.append(("drag", x1, y1, x2, y2))
        self._ensure_ocr()
        src = 'do shell script "%s %d %d %d %d %d"' % (
            DRAG_BIN, int(x1), int(y1), int(x2), int(y2), max(duration_ms, 200))
        self.scripts.append(src)
        self._act(src)

    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None:
        _, _, pos, size = self._window()

        def _abs(p: dict) -> tuple[float, float]:
            return (pos[0] + p["x"] * size[0], pos[1] + self.titlebar_px + p["y"] * max(size[1] - self.titlebar_px, 1))

        x1, y1 = _abs(frm)
        x2, y2 = _abs(to)
        self.commands.append(("swipe", frm, to, duration_ms))
        self._ensure_ocr()
        # Trackpad scroll from the midpoint; the finger delta is (to - from).
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        src = 'do shell script "%s %d %d %d %d %d"' % (
            SCROLL_BIN, int(mx), int(my), int(x2 - x1), int(y2 - y1), max(duration_ms, 200))
        self.scripts.append(src)
        self._act(src)

    def type_text(self, text: str) -> None:
        self.commands.append(("type_text", text))
        src = f'''
tell application "System Events"
  keystroke {json.dumps(text)}
end tell
'''
        self.scripts.append(src)
        self._act(src)
    def vault_fill(self, vault_item_id: str) -> str:
        # spec §11: the fill is manual; the owner completes the Mac auth prompt
        # and then Approves in the canvas.
        raise DriverError("vault_manual", "complete the Mac auth prompt manually, then Approve")
