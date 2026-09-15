from __future__ import annotations

import os
import re

LOCK_RE = re.compile(r"Passcode|Touch ID|Enter Password|Código|Senha do Mac")
TITLES = ("iPhone Mirroring", "Espelhamento do iPhone")
MIRROR_TITLEBAR_PX = int(os.environ.get("MIRROR_TITLEBAR_PX", "28"))


def window_script() -> str:
    return f'''
tell application "System Events"
  set procs to application processes whose background only is false
  repeat with p in procs
    repeat with w in windows of p
      set t to name of w as text
      if t contains "{TITLES[0]}" or t contains "{TITLES[1]}" then
        return (t & tab & (position of w as text) & tab & (size of w as text))
      end if
    end repeat
  end repeat
end tell
error "mirror_window_missing"
'''


def click_script(x: float, y: float, titlebar_px: int = 28) -> str:
    return f'''
-- normalized {x},{y} inside mirror content; titlebar {titlebar_px}px
tell application "System Events"
  -- locator fills absolute coords; this script is a template
  click at {{{x}, {y}}} -- replaced by LatchDriver with pixel ints
end tell
'''


def screenshot_command() -> list[str]:
    return ["screencapture", "-x", "-o", "-l", "{window_id}", "{path}"]


def open_app_script(app: str) -> str:
    return f'''
tell application "System Events"
  -- Spotlight search for {app} inside iPhone Mirroring
  keystroke "{app}"
end tell
'''


class LatchDriver:
    def __init__(self, titlebar_px: int = MIRROR_TITLEBAR_PX):
        self.titlebar_px = titlebar_px
        self.commands: list[tuple] = []
        self.scripts: list[str] = []

    def run_applescript(self, src: str) -> str:
        raise NotImplementedError

    def health(self) -> bool:
        return True

    def open_app(self, app: str) -> None:
        src = open_app_script(app)
        self.commands.append(("open_app", app))
        self.scripts.append(src)

    def screenshot(self) -> bytes:
        self.commands.append(("screenshot", screenshot_command()))
        return b""

    def ocr(self, frame: bytes) -> str:
        self.commands.append(("ocr",))
        return ""

    def tap(self, x: float, y: float) -> None:
        src = click_script(x, y, titlebar_px=self.titlebar_px)
        self.commands.append(("tap", x, y))
        self.scripts.append(src)

    def tap_label(self, label: str) -> None:
        self.commands.append(("tap_label", label))

    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None:
        self.commands.append(("swipe", frm, to, duration_ms))

    def type_text(self, text: str) -> None:
        self.commands.append(("type_text", text))

    def vault_fill(self, vault_item_id: str) -> str:
        self.commands.append(("vault_fill", vault_item_id))
        return "filled"
