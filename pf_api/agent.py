"""Goal-driven agent loop: perceive the phone, decide one action, act, repeat.

This is the counterpart to the deterministic graph in `interpreter.py`. A graph
node says *what* to tap; an `agent.task` node says only what the owner wants, and
the model works out the taps from what is actually on screen.

Why the model never picks pixels
--------------------------------
Every step hands the model two things: a downscaled screenshot, and a *numbered
list* of the text the Mac's Vision OCR found inside the mirror window, each entry
already carrying its own screen coordinates. The model answers with an item
number, never a coordinate. Vision owns precision, the model owns judgement —
which is the whole reason `tap` takes an `item` and not an `x`/`y`.

That also bounds the blast radius: `ocr_items` only returns text inside the
mirror window, so no item the model can choose is capable of clicking the
owner's desktop.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from pf_api.driver import DriverError
from pf_api.schema import PAY_TAP_RE

# Ollama Cloud speaks the OpenAI-compatible wire format, so the calls below use
# that shape rather than the Anthropic one. Swapping providers is a base-url and
# model change, not a rewrite.
# Measured on the same YouTube crop: glm-5.3-flash answers a tool call with an
# image in ~3s against ~6s for kimi-k3 and ~33s for qwen3.5, and grounds in ~10s
# against ~37s. Latency dominates a per-step loop, so the fast model is the
# default for both roles; PHONEFLOW_AGENT_MODEL still overrides for quality runs.
MODEL = os.environ.get("PHONEFLOW_AGENT_MODEL", "glm-5.3-flash")
BASE_URL = os.environ.get("PHONEFLOW_LLM_BASE_URL", "https://ollama.com/v1")
MAX_STEPS = int(os.environ.get("PHONEFLOW_AGENT_MAX_STEPS", "25"))
# The vision models on Ollama Cloud are reasoning models: their hidden reasoning
# is billed against max_tokens, and a tight budget silently returns empty content.
# Generous on purpose. The reasoning is billed against this budget and is
# emitted *before* the tool call, so a screen that takes some working out — a
# crowded app, an ambiguous layout — burns the budget mid-thought and returns a
# reply with no tool call and no content at all. Measured: a simple nav list
# resolves in ~130 tokens, a real YouTube screen exhausted 2000 without ever
# reaching the call. Cost is unaffected when the model finishes early.
MAX_TOKENS = int(os.environ.get("PHONEFLOW_AGENT_MAX_TOKENS", "8000"))
# Icon grounding. OCR cannot see a magnifier or a bell, and every run so far has
# died on exactly that. The Qwen-VL family is trained to return bounding boxes,
# so a second, cheaper pass over the window crop turns icons into list items.
# Set PHONEFLOW_GROUNDER_MODEL="" to disable.
# Split by what each role actually needs. Planning is judgement, and the fast
# model does it in ~7s against ~40s. Grounding is precision, and there the split
# is stark: measured against OCR ground truth on the same frame, qwen3.5 lands
# within 2pt while glm-5.3-flash is 76pt and kimi-k3 422pt out — against a 44pt
# tap target, which means those two simply miss the icon they aimed at.
GROUNDER_MODEL = os.environ.get("PHONEFLOW_GROUNDER_MODEL", "qwen3.5:397b")
# The grounder is a reasoning model too: its thinking is billed against this
# budget and comes before the JSON, so a tight cap returns an empty reply with
# finish_reason "length" and no icons at all — indistinguishable from a screen
# that simply has none.
GROUNDER_MAX_TOKENS = int(os.environ.get("PHONEFLOW_GROUNDER_MAX_TOKENS", "16000"))
# Which coordinate space the grounder answers in is a property of the model, not
# of the request: measured on the same crop, qwen3.5 returns a 0-1000 grid and
# glm-5.3-flash returns real pixels. Guessing wrong is not a small error — it
# scaled every box by 2.088 and put the magnifier on the status bar. So the
# space is inferred per call from OCR anchors instead of configured; this is
# only the fallback for when no anchor comes back.
GROUNDER_SPACE = os.environ.get("PHONEFLOW_GROUNDER_SPACE", "1000")
# How long a turn waits for the grounder before planning without icons. The
# grounder was 35-60s of every step, and most steps tap text that OCR already
# lists — so it runs in the background and the planner only waits when it
# asks for icons (`find_icons`). A cache hit is instant, which this covers.
GROUNDER_WAIT = float(os.environ.get("PHONEFLOW_GROUNDER_WAIT", "0.5"))
_UNSET = object()

SYSTEM = """You drive an iPhone through macOS iPhone Mirroring on the owner's behalf.

Each turn you get a screenshot of the phone and a numbered list of what was
recognised on it. Each number is also drawn on the screenshot as a small red tag
next to the element it names, so match by the tag you see, not by guessing which
text is nearest. Choose exactly one action per turn by calling one tool.

Rules:
- To tap something, call `tap` with the item number from the list. Never guess
  coordinates; if what you want is not in the list, scroll with `swipe` and look
  again.
- The list starts with the text read by OCR. Icons with no text (a magnifier,
  a bell, tab-bar icons) are not listed until you call `find_icons`, which adds
  them marked `[icon]` (e.g. `[icon] search magnifier`). Both are tappable by
  number. Prefer a text item when one does the job; call `find_icons` when the
  control you need has no label. If something you can see is still in neither,
  scroll or leave the screen — do not tap "near" it.
- After each action you see the result, so take one step at a time and check.
- Call `done` only when the goal is *visibly* met on the current screen, and
  quote the on-screen text that proves it in your summary. If you cannot point
  at that evidence in the list you were just given, the goal is not met — keep
  working or call `give_up`. Never report a result you did not actually see.
- To go back, prefer tapping a visible back control on screen — a `<`, `‹`,
  `Voltar`, `Back`, or a close `×` — over the system back gesture, since apps
  often put their own back inside the screen. Use `system_button` back only when
  no such control is visible.
- You will be told when an action left the screen unchanged. When that happens,
  do not repeat it: try a different target, scroll at a different place with
  `at_item`, or leave the screen with `system_button`. An action that already
  did nothing will be refused if you send it again.
- Never write a title, name or value you did not actually read on the current
  screen. For anything you are collecting, save it with `record` when you see it
  and report only recorded items. If you were asked to gather items and have none
  recorded, call `give_up` — inventing a plausible answer is a serious failure,
  worse than admitting you could not complete the task.
- To open an app, call `open_app` once and wait. If it does not come up full
  screen — you still see the home screen, a Spotlight search box, or a small
  floating video / mini-player — do NOT tap launch icons repeatedly. Call
  `reset_app` with the app name to force a clean launch. Two failed opens is
  the signal to reset, not to try a third icon.
- If you open the wrong thing (a video, an ad, a popup), recover deliberately:
  close it, press back, or press home and reopen the app. Do not tap the same
  "close"/"minimize"/"back" control more than twice — if it did not work, change
  approach.
- Social feeds (a home feed, a reels/shorts wall, a comments list) scroll
  endlessly and never reach a true end — do not wait for one. Collect what the
  goal asks for as you scroll and stop once you have it.
- When the goal asks for SEVERAL items from a feed or list (e.g. "the first 3
  video titles"), collect them incrementally: `record` each item as soon as it is
  visible, then scroll for the next one. Do not scroll repeatedly trying to see
  them all on one screen — a feed often shows one item at a time. Call `done` once
  you have recorded the number asked for; the recorded list is returned.
- If an app is unfamiliar, or a feature named in the goal seems missing or
  renamed, call `read_app_doc` with the app name before guessing — it returns
  notes on that app's layout, renamed features and popups.
- If an app is unfamiliar, or a feature named in the goal seems missing or
  renamed, call `read_app_doc` with the app name before guessing — it returns
  notes on that app's layout, renamed features and popups.
- App interfaces change over time, and a goal may name a feature by an old or
  approximate label. Do not hunt for an exact word that is not on screen — map
  the goal's INTENT to whatever the app calls it now and act on the closest
  current equivalent. For example, YouTube removed "Trending"/"Em alta"; its
  current equivalent is the "Hype" section. If after looking you cannot find the
  literal target, pick the nearest equivalent that serves the same intent rather
  than scrolling in circles, and say in your summary what you used.
- If two consecutive looks at the screen did not reveal the target, stop
  repeating the same scroll: change tactic (a different tab, search, or the
  closest equivalent) or report what the app actually offers.
- Call `give_up` if the goal is impossible, you are stuck in a loop, or the phone
  shows something you should not act on (a password prompt, a payment screen, a
  destructive confirmation). Do not confirm payments or purchases.
"""

def _tool(name, description, props, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


# The clock ticks on its own, so it must not count as the screen changing —
# comparing raw OCR makes every screen look different once a minute.
CLOCK_RE = re.compile(r"^\s*\d{1,2}[:.]\d{2}")

WHY = {"type": "string", "description": "Briefly, why this action."}
TOOLS = [
    _tool("tap", "Tap one item from the numbered list of recognised text.",
          {"item": {"type": "integer", "description": "Item number from the list."}, "why": WHY},
          ["item", "why"]),
    _tool("swipe",
          "Scroll. 'up' reveals what is further down the page. Give `at_item` to "
          "scroll a specific region — a row of chips, a carousel — instead of the "
          "middle of the screen; without it a row that is not centred never moves.",
          {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
           "at_item": {"type": "integer", "description": "Item number to scroll at, optional."},
           "why": WHY},
          ["direction", "why"]),
    _tool("long_press", "Press and hold an item — opens context menus.",
          {"item": {"type": "integer"}, "why": WHY}, ["item", "why"]),
    _tool("drag",
          "Drag from one item to another — reorder an icon, move a slider, "
          "drag-and-drop. Give the item numbers to drag from and to.",
          {"from_item": {"type": "integer"}, "to_item": {"type": "integer"}, "why": WHY},
          ["from_item", "to_item", "why"]),
    _tool("system_button",
          "Device-level navigation. 'back' leaves the current screen, 'home' goes "
          "to the home screen, 'app_switcher' shows open apps.",
          {"button": {"type": "string", "enum": ["back", "home", "app_switcher"]}, "why": WHY},
          ["button", "why"]),
    _tool("type_text", "Type text into the field that already has focus.",
          {"text": {"type": "string"}, "why": WHY}, ["text", "why"]),
    _tool("open_app", "Open an app by name from the phone's home screen search.",
          {"app": {"type": "string"}, "why": WHY}, ["app", "why"]),
    _tool("find_icons",
          "The thing you need is an ICON with no text (magnifier, bell, tab-bar "
          "icon, back arrow...) and it is not in the list yet. Locates the icons "
          "on the current screen and adds them to the list. Costs no step.",
          {"why": WHY}, ["why"]),
    _tool("wait", "Let the screen settle, then look again. Use this while an app "
          "is still loading or a list has not rendered yet.",
          {"why": WHY}, ["why"]),
    _tool("scroll_to",
          "Scroll a feed or list to find something, in one call, without stopping "
          "each scroll. Give `label` (text to reach) to scroll until it appears; "
          "omit it to just reveal the next screenful. It stops on its own when the "
          "target shows up or when the screen stops changing (end of the list). "
          "Use this instead of repeating `swipe` — it is faster and detects the end.",
          {"label": {"type": "string", "description": "Text to scroll until visible (optional)."},
           "direction": {"type": "string", "enum": ["up", "down"], "description": "'up' reveals what is below."},
           "why": WHY},
          ["direction", "why"]),
    _tool("record",
          "Save one item you were asked to collect (a video title, a name, a value) "
          "as soon as you see it. Use this while scrolling a feed or list: record "
          "each item as it appears instead of trying to see them all at once. Your "
          "recorded items are kept and returned even across scrolls.",
          {"text": {"type": "string", "description": "The exact item to save."}, "why": WHY},
          ["text", "why"]),
    _tool("done", "The goal is met. Report what was found or accomplished.",
          {"summary": {"type": "string"}}, ["summary"]),
    _tool("give_up", "The goal cannot be reached, or it is not safe to continue.",
          {"reason": {"type": "string"}}, ["reason"]),
]


# Normalised swipe endpoints, in mirror-window space. Swiping the content up
# (finger travels up) is what reveals what is further down the page.
SWIPES = {
    "up": ({"x": 0.5, "y": 0.75}, {"x": 0.5, "y": 0.25}),
    "down": ({"x": 0.5, "y": 0.25}, {"x": 0.5, "y": 0.75}),
    "left": ({"x": 0.75, "y": 0.5}, {"x": 0.25, "y": 0.5}),
    "right": ({"x": 0.25, "y": 0.5}, {"x": 0.75, "y": 0.5}),
}


def _blocked_apps() -> list[str]:
    """Apps the agent must never drive, from PHONEFLOW_BLOCKED_APPS (comma list).

    A safety valve for distribution: banking, wallet and password apps should be
    off-limits even if the goal names them, so an agent on someone's phone cannot
    move money or read secrets. Matching is case-insensitive substring.
    """
    raw = os.environ.get("PHONEFLOW_BLOCKED_APPS", "")
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def _app_doc(name: str) -> str:
    """The notes file for one app by name, from app_hints/ (see _load_app_hints)."""
    import glob
    key = (name or "").strip().lower()
    if not key:
        return ""
    dirs = []
    env = os.environ.get("PHONEFLOW_APP_HINTS")
    if env:
        dirs.append(env)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.path.join(here, "app_hints"))
    home = os.environ.get("PHONEFLOW_HOME")
    if home:
        dirs.append(os.path.join(home, "app_hints"))
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            base = os.path.splitext(os.path.basename(path))[0].lower()
            if base == key or base in key or key in base:
                try:
                    return open(path, encoding="utf-8").read().strip()
                except OSError:
                    pass
    return ""


def _app_doc(name: str) -> str:
    """The notes file for one app by name, from app_hints/ (see _load_app_hints)."""
    import glob
    key = (name or "").strip().lower()
    if not key:
        return ""
    dirs = []
    env = os.environ.get("PHONEFLOW_APP_HINTS")
    if env:
        dirs.append(env)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.path.join(here, "app_hints"))
    home = os.environ.get("PHONEFLOW_HOME")
    if home:
        dirs.append(os.path.join(home, "app_hints"))
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            base = os.path.splitext(os.path.basename(path))[0].lower()
            if base == key or base in key or key in base:
                try:
                    return open(path, encoding="utf-8").read().strip()
                except OSError:
                    pass
    return ""


def _load_app_hints(goal: str) -> str:
    """Return per-app guidance whose file name appears in the goal.

    Hints live as `<app>.md` under app_hints/ (bundled) or PHONEFLOW_APP_HINTS.
    They carry what a generic model cannot know — a renamed tab, a popup to
    dismiss, how a feed paginates — so the agent handles each app without any
    app-specific code. Anyone can drop a new file to support a new app.
    """
    import glob
    g = (goal or "").lower()
    dirs = []
    env = os.environ.get("PHONEFLOW_APP_HINTS")
    if env:
        dirs.append(env)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.path.join(here, "app_hints"))
    home = os.environ.get("PHONEFLOW_HOME")
    if home:
        dirs.append(os.path.join(home, "app_hints"))
    seen, out = set(), []
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            name = os.path.splitext(os.path.basename(path))[0].lower()
            if name in seen or name not in g:
                continue
            try:
                out.append(open(path, encoding="utf-8").read().strip())
                seen.add(name)
            except OSError:
                continue
    return "\n\n".join(out)


def _client():
    import openai  # imported lazily so the graph path needs no API credentials

    key = os.environ.get("PHONEFLOW_LLM_API_KEY") or os.environ.get("OLLAMA_API_KEY")
    if not key:
        raise DriverError("agent_unconfigured", "PHONEFLOW_LLM_API_KEY is not set")
    return openai.OpenAI(api_key=key, base_url=BASE_URL)


GROUND_PROMPT = (
    "This is a cropped iPhone screenshot. List every tappable ICON or button that "
    "has NO visible text label of its own (magnifier, bell, back arrow, close X, "
    "menu dots, play, heart, share, tab-bar icons, etc.). Skip anything that is "
    "readable text. Include the bottom tab-bar icons. Return ONLY a JSON array, at "
    'most 30 entries, each {"label": "<short name>", "bbox_2d": [x1, y1, x2, y2]}.'
)

ANCHOR_NOTE = (
    "\n\nAlso include, in the same array, one entry for each of these exact texts "
    "already visible on screen (label them exactly as written): "
)


class IconGrounder:
    """Turns the window crop into tappable icon items via a grounding VLM.

    Text stays with OCR: it is faster and exact. This pass exists only for what
    OCR structurally cannot return, and it fails soft — any error or unparseable
    reply means "no icons this turn", never a dead run.
    """

    def __init__(self, client, model: str = GROUNDER_MODEL):
        self.client = client
        self.model = model
        self._key = ""
        self._cache: list[dict] = []

    def find(self, crop: bytes, geom: dict, anchors: list[dict] | None = None) -> list[dict]:
        if not self.model or not crop:
            return []
        # Keyed on the screen's text, not on the image. The JPEG differs every
        # frame — the clock ticks, video plays, compression wobbles — so hashing
        # the bytes never hit and every step paid for grounding again, which is
        # the single most expensive thing in the loop. The same text means the
        # same screen, and the same icons.
        key = hashlib.sha256(
            "\u0000".join(sorted(
                a.get("t", "") for a in (anchors or [])
                if not CLOCK_RE.match(a.get("t", ""))
            )).encode("utf-8")
        ).hexdigest()
        if key and key == self._key:
            print("[grounder] cached", flush=True)
            return self._cache
        # A handful of OCR lines whose true position is already known. Asking the
        # model to box them too costs nothing and reveals which coordinate space
        # it answered in — see _pick_space.
        picked = self._anchors(anchors or [])
        prompt = GROUND_PROMPT + (ANCHOR_NOTE + ", ".join('"%s"' % a["t"] for a in picked) if picked else "")
        b64 = base64.standard_b64encode(crop).decode()
        try:
            reply = self.client.chat.completions.create(
                model=self.model,
                max_tokens=GROUNDER_MAX_TOKENS,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            text = reply.choices[0].message.content or ""
            if not text.strip():
                reason = getattr(reply.choices[0], "finish_reason", "") or "unknown"
                print(f"[grounder] empty reply (finish_reason={reason})", flush=True)
        except Exception as exc:  # grounding is an aid, not a dependency
            print(f"[grounder] failed: {exc}", flush=True)
            return []
        boxes = self._boxes(text)
        space = self._pick_space(boxes, picked, geom)
        items = self._parse_boxes(boxes, geom, space, {a["t"] for a in picked})
        self._key, self._cache = key, items
        return items

    @staticmethod
    def _anchors(items: list[dict], n: int = 4) -> list[dict]:
        """Spread a few unambiguous OCR lines down the screen."""
        good = [i for i in items
                if 3 <= len(i.get("t", "")) <= 18
                and not i["t"].startswith("[icon]")
                and not CLOCK_RE.match(i["t"])
                and sum(1 for j in items if j.get("t") == i.get("t")) == 1]
        good.sort(key=lambda i: i.get("y", 0))
        return good[:: max(1, len(good) // n)][:n] if good else []

    @staticmethod
    def _pick_space(boxes: list[dict], anchors: list[dict], geom: dict) -> tuple[float, float]:
        """Infer the divisor for the model's boxes from anchors of known position."""
        pos, size = geom.get("pos", (0, 0)), geom.get("size", (1, 1))
        pw, ph = float(geom.get("px_w") or 1), float(geom.get("px_h") or 1)
        fallback = (1000.0, 1000.0) if GROUNDER_SPACE != "pixels" else (pw, ph)
        truth = {a["t"]: (a.get("x", 0), a.get("y", 0)) for a in anchors}
        if not truth:
            return fallback
        best, best_err = fallback, None
        for cand in ((1000.0, 1000.0), (pw, ph)):
            errs = []
            for b in boxes:
                lab, box = b.get("label"), b.get("box")
                if lab not in truth or not box:
                    continue
                cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
                x = pos[0] + cx / cand[0] * size[0]
                y = pos[1] + cy / cand[1] * size[1]
                tx, ty = truth[lab]
                errs.append(((x - tx) ** 2 + (y - ty) ** 2) ** 0.5)
            if errs:
                err = sum(errs) / len(errs)
                if best_err is None or err < best_err:
                    best, best_err = cand, err
        if best_err is not None:
            print(f"[grounder] space={'1000' if best[0] == 1000.0 else 'pixels'} "
                  f"anchor error {best_err:.0f}pt", flush=True)
        return best

    @staticmethod
    def _boxes(text: str) -> list[dict]:
        """Every {label, box} the reply contains, tolerant of partial JSON."""
        m = re.search(r"\[.*\]", text or "", re.S)
        if not m:
            return []
        out = []
        for om in re.finditer(r"\{[^{}]*\}", m.group(0)):
            try:
                e = json.loads(om.group(0))
            except json.JSONDecodeError:
                continue  # one malformed entry must not cost the whole reply
            box = e.get("bbox_2d") or e.get("bbox")
            if not (isinstance(box, list) and len(box) == 4):
                continue
            try:
                out.append({"label": str(e.get("label") or "icon").strip('"'),
                            "box": [float(v) for v in box]})
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _parse_boxes(boxes: list[dict], geom: dict, space: tuple[float, float],
                     skip: set[str]) -> list[dict]:
        pos, size = geom.get("pos", (0, 0)), geom.get("size", (1, 1))
        # The crop starts at the window's top edge, so the Mac title bar and the
        # Mirroring toolbar are in it too — the grounder happily returns the red
        # close button. Anything in that band is the Mac, not the phone.
        top = pos[1] + float(geom.get("titlebar") or 28)
        out = []
        for b in boxes:
            if b["label"] in skip:
                continue  # an anchor: OCR already lists it, with better precision
            x1, y1, x2, y2 = b["box"]
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            sx = pos[0] + cx / space[0] * size[0]
            sy = pos[1] + cy / space[1] * size[1]
            if sy < top or not (pos[0] <= sx <= pos[0] + size[0]):
                continue
            out.append({"t": "[icon] " + b["label"][:40], "x": sx, "y": sy})
        return out[:30]


class AgentLoop:
    """Runs one natural-language goal against the phone.

    `client` is injectable so tests can drive the loop without the network.
    """

    def __init__(self, driver, client=None, model: str = MODEL, max_steps: int = MAX_STEPS,
                 grounder=_UNSET):
        self.driver = driver
        self.client = client or _client()
        self.model = model
        self.max_steps = max_steps
        # Default on; tests pass grounder=None to keep the loop off the network.
        self.grounder = IconGrounder(self.client) if grounder is _UNSET else grounder
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._icons = None
        self.records: list[str] = []
        self._key_counts: dict[str, int] = {}
        self.steps: list[dict] = []
        self._waits = 0  # consecutive-ish waits; waiting is not a strategy
        # Actions observed to leave the screen exactly as it was. Re-sending one
        # is refused rather than executed: repeating a no-op is the single most
        # common way one of these loops burns its whole budget.
        self._dead: set[str] = set()

    # ---- perception ----------------------------------------------------

    def _observe(self) -> tuple[bytes, list[dict], dict | None]:
        if hasattr(self.driver, "observe"):
            items, frame, geom = self.driver.observe()
            items = list(items)
        else:  # drivers without the fused call (tests, older mirrors)
            raw = self.driver.screenshot()
            items, frame, geom = list(self.driver.ocr_items(raw)), self.driver.frame_for_model(), None
        self._icons = None
        # If the driver already located icons locally (pf_icons / CoreML), those
        # are exact and free — the cloud grounder is only a fallback for drivers
        # without a local model.
        if any(str(it.get("t", "")).startswith("[icon]") for it in items):
            return frame, items, geom
        if self.grounder is not None:
            try:
                if geom is None:
                    crop, geom = self.driver.window_crop(raw)
                else:
                    crop = frame
                # Grounding is the slow part of a step and usually not needed:
                # it runs alongside planning and is only awaited on demand.
                self._icons = self._pool.submit(self.grounder.find, crop, geom, items)
                items.extend(self._await_icons(GROUNDER_WAIT))
            except Exception as exc:  # never let the aid take the run down
                print(f"[grounder] skipped: {exc}", flush=True)
        return frame, items, geom

    def _await_icons(self, timeout: float | None) -> list[dict]:
        """Icons from the pending grounding pass, or [] if it is not done yet."""
        fut = self._icons
        if fut is None:
            return []
        try:
            icons = fut.result(timeout=timeout)
        except TimeoutError:
            return []
        except Exception as exc:
            print(f"[grounder] failed: {exc}", flush=True)
            icons = []
        self._icons = None
        if icons:
            print(f"[grounder] {len(icons)} icons: "
                  + ", ".join(i["t"].replace("[icon] ", "") for i in icons), flush=True)
        return icons

    @staticmethod
    def _mark(frame: bytes, items: list[dict], geom: dict | None) -> bytes:
        """Set-of-Mark: draw each item's number on the frame where it sits.

        The planner used to get a bare screenshot and a numbered list, and had
        to pair them by reading the text in the image — which is exactly where
        it went wrong on rows of similar buttons and on icons whose only name
        was the grounder's guess. With the number printed on the element the
        pairing is visual and there is nothing left to infer. Fails soft: any
        problem returns the plain frame.
        """
        if not items or not geom:
            return frame
        try:
            import io
            from PIL import Image, ImageDraw, ImageFont
            pos, size = geom.get("pos", (0, 0)), geom.get("size", (1, 1))
            im = Image.open(io.BytesIO(frame)).convert("RGB")
            w, h = im.size
            draw = ImageDraw.Draw(im)
            fs = max(14, int(h / 45))
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", fs)
            except OSError:
                try:
                    font = ImageFont.load_default(size=fs)
                except TypeError:  # Pillow < 10 has no sized default
                    font = ImageFont.load_default()
            for n, it in enumerate(items, 1):
                # Screen points back into frame pixels: the frame is the window
                # crop, scaled, so the fraction along the window is the fraction
                # along the image.
                x = (float(it.get("x", 0)) - pos[0]) / max(size[0], 1) * w
                y = (float(it.get("y", 0)) - pos[1]) / max(size[1], 1) * h
                label = str(n)
                l, t, r, b = draw.textbbox((0, 0), label, font=font)
                tw, th = r - l, b - t
                # Tag sits just above-left of the centre so it never hides the
                # element it names, and stays inside the image at the edges.
                x0 = min(max(x - tw - 14, 0), w - tw - 4)
                y0 = min(max(y - th - 12, 0), h - th - 4)
                draw.rectangle([x0, y0, x0 + tw + 4, y0 + th + 4], fill=(220, 30, 30))
                draw.text((x0 + 2 - l, y0 + 2 - t), label, fill="white", font=font)
                draw.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(220, 30, 30), width=2)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=75)
            return out.getvalue()
        except Exception as exc:  # the marks are an aid; the frame is not optional
            print(f"[mark] skipped: {exc}", flush=True)
            return frame

    @staticmethod
    def _signature(items: list[dict]) -> tuple:
        """What the screen shows, ignoring anything that changes by itself.

        Two things are excluded. The clock, which ticks on its own. And the
        `[icon]` entries: the grounding model names and counts icons a little
        differently on every frame ("more_options" one turn, "menu_dots" the
        next), so including them makes an unchanged screen look new every time
        — which silently disarms both the stuck-screen warning and the no-op
        guard. Only OCR text is stable enough to say "nothing happened".
        """
        return tuple(sorted(
            t for t in (i.get("t", "") for i in items)
            if not CLOCK_RE.match(t) and not t.startswith("[icon]")
        ))

    @staticmethod
    def _key(name: str, args: dict, items: list[dict] | None = None) -> str:
        """Identity of an action for the no-op guard.

        Item numbers are re-dealt every frame (the icon pass returns a slightly
        different set each time), so "tap item 24" and "tap item 29" can be the
        very same magnifier. Keying by index would let that repeat forever. For
        anything aimed at a list item the key is what it resolves to — the
        label plus its position on a coarse grid — so the same target caught
        twice is caught regardless of where it landed in the list.
        """
        idx = args.get("item") if name in ("tap", "long_press") else args.get("at_item")
        if items and isinstance(idx, int) and 1 <= idx <= len(items):
            it = items[idx - 1]
            target = "%s@%d,%d" % (it.get("t", ""), round(it.get("x", 0) / 24), round(it.get("y", 0) / 24))
            extra = args.get("direction", "")
            return f"{name}:{target}:{extra}"
        return name + ":" + json.dumps(args or {}, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _listing(items: list[dict]) -> str:
        if not items:
            return "(no text recognised on screen)"
        return "\n".join(f"{i}. {it.get('t','')}" for i, it in enumerate(items, 1))

    @staticmethod
    def _nav_hint(items: list[dict]) -> str:
        """A NavigationHintDetector-style nudge: name a visible back/close control
        so the model uses it instead of guessing its way out of a screen."""
        import re as _re
        pat = _re.compile(r"^\s*(<|‹|×|✕|✖|back|voltar|cancelar|cancel|fechar|close|done|concluir)\b", _re.I)
        for i, it in enumerate(items, 1):
            t = str(it.get("t", ""))
            if pat.match(t):
                return f"A back/close control is visible: item {i} ({t!r}). Use it to leave this screen.\n\n"
        return ""

    def _turn(self, goal: str, small: bytes, items: list[dict], left: int, note: str = "") -> dict:
        b64 = base64.standard_b64encode(small).decode()
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {
                "type": "text",
                "text": (
                    f"Goal: {goal}\n\n"
                    f"{note}"
                    f"Text on screen:\n{self._listing(items)}\n\n"
                    f"Steps remaining: {left}. Choose one action."
                ),
            },
        ]
        return {"role": "user", "content": content}

    # ---- action --------------------------------------------------------

    def _act(self, name: str, args: dict, items: list[dict]) -> str:
        """Run one tool call; returns the observation text fed back to the model."""
        if name == "tap":
            idx = int(args.get("item", 0))
            if not 1 <= idx <= len(items):
                return f"No item {idx}; the list has {len(items)} entries."
            hit = items[idx - 1]
            label = hit.get("t", "")
            # The schema's pay/submit guard only covers phone.tap nodes drawn in
            # the canvas — it is a validation-time check on a label the author
            # wrote. Agent taps are chosen at runtime and never pass through it,
            # so the same rule is enforced here instead of trusting the prompt.
            if PAY_TAP_RE.search(label):
                return (
                    f"Refused: {label!r} looks like a pay/submit control, which this "
                    "agent must never press. Choose another target or call give_up."
                )
            self.driver.tap_point(hit["x"], hit["y"])
            return f"Tapped {label!r}."
        if name != "wait":
            self._waits = 0
        if name == "swipe":
            at = args.get("at_item")
            if at and 1 <= int(at) <= len(items):
                anchor = items[int(at) - 1]
                self.driver.swipe_from(anchor["x"], anchor["y"], args["direction"])
                return f"Swiped {args['direction']} at {anchor.get('t','')!r}."
            frm, to = SWIPES[args["direction"]]
            self.driver.swipe(frm, to, 300)
            return f"Swiped {args['direction']} through the middle of the screen."
        if name == "long_press":
            idx = int(args.get("item", 0))
            if not 1 <= idx <= len(items):
                return f"No item {idx}; the list has {len(items)} entries."
            hit = items[idx - 1]
            self.driver.long_press(hit["x"], hit["y"])
            return f"Long-pressed {hit.get('t','')!r}."
        if name == "system_button":
            button = args["button"]
            if button == "back":
                self.driver.back()
            else:
                self.driver.system_key(button)
            return f"Pressed {button}."
        if name == "type_text":
            self.driver.type_text(args["text"])
            return f"Typed {args['text']!r}."
        if name == "read_app_doc":
            doc = _app_doc(args.get("app", ""))
            return ("Notes for %r:\n%s" % (args.get("app", ""), doc)) if doc \
                else f"No notes on file for {args.get('app','')!r}. Proceed carefully and observe the screen."
        if name == "read_app_doc":
            doc = _app_doc(args.get("app", ""))
            return ("Notes for %r:\n%s" % (args.get("app", ""), doc)) if doc \
                else f"No notes on file for {args.get('app','')!r}. Proceed carefully and observe the screen."
        if name == "open_app":
            app = args.get("app", "")
            if any(b in app.lower() for b in _blocked_apps()):
                return f"Refused: {app!r} is a blocked app you are not allowed to open."
            self.driver.open_app(app)
            return f"Opened {app!r}."
        if name == "drag":
            fi, ti = int(args.get("from_item", 0)), int(args.get("to_item", 0))
            if not (1 <= fi <= len(items) and 1 <= ti <= len(items)):
                return f"Bad items for drag; the list has {len(items)} entries."
            a, b = items[fi - 1], items[ti - 1]
            if hasattr(self.driver, "drag"):
                self.driver.drag(a["x"], a["y"], b["x"], b["y"])
                return f"Dragged {a.get('t','')!r} onto {b.get('t','')!r}."
            return "This driver cannot drag."
        if name == "reset_app":
            app = args.get("app", "")
            if any(b in app.lower() for b in _blocked_apps()):
                return f"Refused: {app!r} is a blocked app."
            if hasattr(self.driver, "reset_app"):
                self.driver.reset_app(app)
            else:
                self.driver.system_key("home")
                if app:
                    self.driver.open_app(app)
            return f"Reset and reopened {app!r}."
        if name == "wait":
            self._waits += 1
            if self._waits > 3:
                return (
                    "You have already waited several times and the screen is not "
                    "changing. Waiting again will not help — act on what is on "
                    "screen now, or call give_up."
                )
            time.sleep(2.0)
            return "Waited; look at the new screen."
        return f"Unknown action {name!r}."

    # ---- loop ----------------------------------------------------------

    def run(self, goal: str) -> dict:
        """Drive the phone until the goal is met, refused, or the budget runs out."""
        messages: list[dict] = [{"role": "system", "content": SYSTEM}]
        hints = _load_app_hints(goal)
        if hints:
            messages.append({"role": "system",
                             "content": "App-specific notes for this task:\n" + hints})
        idle = 0  # consecutive replies that chose no action
        prev_sig: tuple | None = None
        last_key: str | None = None
        for step in range(self.max_steps):
            small, items, geom = self._observe()
            small = self._mark(small, items, geom)
            sig = self._signature(items)
            # Saying this out loud is what stops the loop dead: the model cannot
            # see that its last action achieved nothing, and left to guess it
            # simply sends the same one again.
            note = ""
            if last_key and prev_sig is not None and sig == prev_sig:
                self._dead.add(last_key)
                note = "The screen did not change after your last action. Try something else.\n\n"
            if self.max_steps - step <= 3:
                note += ("You are almost out of steps. If you have gathered what was "
                         "asked (check your recorded items), call done using ONLY those. "
                         "If you have not, call give_up honestly — never invent a title, "
                         "name or value you did not read on screen.\n\n")
            prev_sig = sig
            note += self._nav_hint(items)
            self._strip_old_images(messages)
            messages.append(self._turn(goal, small, items, self.max_steps - step, note))
            reply = self.client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                tools=TOOLS,
                messages=messages,
            )
            choice = reply.choices[0].message
            calls = choice.tool_calls or []
            messages.append(self._assistant(choice, calls))

            if not calls:
                # Why there is no action matters, and an empty reply hides it:
                # a truncated turn (finish_reason "length") means the reasoning
                # ate the token budget before the tool call was emitted, which
                # looks identical to a model that simply chose nothing. Naming
                # it here is what turns a silent stall into a fixable report.
                reason = getattr(reply.choices[0], "finish_reason", "") or "unknown"
                detail = (choice.content or "").strip() or f"no action (finish_reason={reason})"
                print(f"[agent] step {step + 1}/{self.max_steps} NO-ACTION {detail}", flush=True)
                idle += 1
                if idle >= 3:
                    return self._finish("stalled", detail, step)
                time.sleep(2.0)
                continue
            idle = 0

            call = calls[0]
            name = call.function.name
            raw = call.function.arguments
            # Arguments arrive as a JSON string on this wire format; a model that
            # emits malformed JSON is a stall, not a crash.
            try:
                args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            except json.JSONDecodeError:
                return self._finish("stalled", f"unparseable arguments for {name}: {raw!r}", step)
            self.steps.append({"step": step + 1, "action": name, "args": args})
            # Printed as it happens: the run record is only written when the node
            # finishes, so without this a long loop is a black box while it runs.
            print(f"[agent] step {step + 1}/{self.max_steps} {name} {args}", flush=True)

            if name == "scroll_to":
                label = str(args.get("label", "") or "").strip().lower()
                direction = args.get("direction", "up")
                sigs = set()
                outcome = None
                for _ in range(8):
                    texts = [str(it.get("t", "")) for it in items]
                    if label and any(label in t.lower() for t in texts):
                        outcome = f"'{args.get('label')}' is on screen now."
                        break
                    sig = self._signature(items)
                    if sig in sigs:
                        outcome = ("Reached the end — scrolling no longer changes the "
                                   "screen." if label == "" or not label
                                   else f"Reached the end without finding '{args.get('label')}'.")
                        break
                    sigs.add(sig)
                    try:
                        self.driver.swipe_from(0.5, 0.6, direction, 0.6) if hasattr(self.driver, "swipe_from") \
                            else self.driver.swipe(SWIPES[direction]["from"], SWIPES[direction]["to"], 320)
                    except Exception as exc:
                        outcome = f"Could not scroll: {exc}"
                        break
                    small, items, geom = self._observe()
                if outcome is None:
                    outcome = "Scrolled several times; more may remain."
                # Show the planner the screen it scrolled to.
                self._strip_old_images(messages)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": outcome})
                marked = self._mark(small, items, geom)
                b64 = base64.standard_b64encode(marked).decode()
                messages.append({"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                    {"type": "text", "text": "Text on screen:\n" + self._listing(items)
                     + "\n\nChoose one action."}]})
                prev_sig = self._signature(items)
                continue
            if name == "open_app":
                app = str(args.get("app", ""))
                self._open_attempts = getattr(self, "_open_attempts", {})
                self._open_attempts[app] = self._open_attempts.get(app, 0) + 1
                if self._open_attempts[app] >= 2 and hasattr(self.driver, "reset_app"):
                    print(f"[agent] step {step + 1}/{self.max_steps} auto reset_app {app!r} (launch not sticking)", flush=True)
                    try:
                        self.driver.reset_app(app)
                    except Exception as exc:
                        self.driver.open_app(app)
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": f"open_app kept failing, so I force-quit and reopened {app!r}. "
                                                "It should now be full screen — look and continue."})
                    continue
            if name == "record":
                text = str(args.get("text", "")).strip()
                if text and text not in self.records:
                    self.records.append(text)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": f"Recorded {len(self.records)} so far: "
                                            + " | ".join(self.records)})
                continue
            if name == "done":
                return self._finish("succeeded", args.get("summary", ""), step)
            if name == "give_up":
                return self._finish("given_up", args.get("reason", ""), step)

            if name == "find_icons":
                icons = [i for i in self._await_icons(None) if i not in items]
                items.extend(icons)
                # The same frame, now with the icons' numbers drawn on it too.
                content = [{"type": "text", "text": ("Icons added to the list:\n" + self._listing(items)
                                                     if icons else "No icons were found on this screen.")}]
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content[0]["text"]})
                if icons:
                    self._strip_old_images(messages)
                    marked = self._mark(small, items, geom)
                    b64 = base64.standard_b64encode(marked).decode()
                    messages.append({"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                        {"type": "text", "text": "Same screen with the icons numbered. Choose one action."}]})
                    reply = self.client.chat.completions.create(
                        model=self.model, max_tokens=MAX_TOKENS, tools=TOOLS, messages=messages)
                    choice = reply.choices[0].message
                    calls = choice.tool_calls or []
                    messages.append(self._assistant(choice, calls))
                    if not calls:
                        continue
                    call = calls[0]
                    name = call.function.name
                    raw = call.function.arguments
                    try:
                        args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                    except json.JSONDecodeError:
                        return self._finish("stalled", f"unparseable arguments for {name}: {raw!r}", step)
                    self.steps.append({"step": step + 1, "action": name, "args": args})
                    print(f"[agent] step {step + 1}/{self.max_steps} {name} {args}", flush=True)
                    if name == "done":
                        return self._finish("succeeded", args.get("summary", ""), step)
                    if name == "give_up":
                        return self._finish("given_up", args.get("reason", ""), step)
                else:
                    continue

            # `wait` is exempt: waiting twice on a genuinely loading screen is
            # correct, and its own budget already stops it becoming a strategy.
            key = self._key(name, args, items) if name not in ("wait", "find_icons") else ""
            if key:
                self._key_counts[key] = self._key_counts.get(key, 0) + 1
            if key and self._key_counts.get(key, 0) >= 3 and key not in self._dead:
                observation = (
                    "Refused: you have tried this same target three times with no "
                    "progress — you are in a loop. Do something different: press the "
                    "home button and reopen the app, choose a clearly different item, "
                    "or give_up honestly. Do not repeat this."
                )
                self._dead.add(key)
                last_key = None
                messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
                continue
            if key and key in self._dead:
                # Refusing costs one cheap turn; executing it again costs a turn
                # *and* leaves the model with the same screen and no new
                # information, which is how a budget disappears into a loop.
                observation = (
                    "Refused: you already did exactly this and the screen did not "
                    "change. Pick a different action."
                )
                last_key = None
            else:
                try:
                    observation = self._act(name, args, items)
                    last_key = key
                except DriverError as exc:
                    # A driver refusal is information for the model, not a crash:
                    # it gets one more turn to choose differently.
                    observation = f"That failed ({exc.code}): {exc}"
                    last_key = None
            messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
        return self._finish("out_of_steps", f"budget of {self.max_steps} steps exhausted", self.max_steps)

    @staticmethod
    def _assistant(choice, calls) -> dict:
        if not calls:
            return {"role": "assistant", "content": choice.content or ""}
        return {
            "role": "assistant",
            "content": choice.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ],
        }

    @staticmethod
    def _strip_old_images(messages: list[dict]) -> None:
        """Keep only the newest screenshot in the conversation.

        Every turn appends a fresh frame, so by step ten the request carries ten
        images of screens that no longer exist — slower each step, and the stale
        ones actively mislead. The text of each turn stays, so the model still
        has the history of what it saw and did.
        """
        for m in messages:
            content = m.get("content")
            if not isinstance(content, list):
                continue
            kept = [c for c in content if c.get("type") != "image_url"]
            if len(kept) != len(content):
                m["content"] = kept or [{"type": "text", "text": "(earlier screen)"}]

    def _finish(self, status: str, message: str, step: int) -> dict:
        out = {"status": status, "message": message, "steps": self.steps, "used": step + 1}
        if self.records:
            out["records"] = list(self.records)
            out["message"] = (message + "\n\nColetado:\n- " + "\n- ".join(self.records)).strip()
        return out
