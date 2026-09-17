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

OCR_SWIFT = r"""// PhoneFlow OCR — macOS Vision text recognition over a screenshot.
//
// Prints one JSON object on stdout:
//   {"w":<image px>,"h":<image px>,"lines":[{"t":"text","nx":0..1,"ny":0..1}]}
//
// nx/ny are the CENTRE of each recognised line, normalised to the image with a
// TOP-LEFT origin (Vision's own boxes are bottom-left, so the flip happens
// here). Normalised rather than absolute because the caller crops and upscales
// the mirror window before OCR — only it knows where that crop sits on screen.
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: pf_ocr <image>\n".data(using: .utf8)!)
    exit(2)
}

let sw = Double(cg.width), sh = Double(cg.height)
// Screen size in points as well: the caller crops in pixels but clicks in
// points, and the ratio between the two is the display's backing scale.
let scr = NSScreen.main?.frame ?? NSRect(x: 0, y: 0, width: sw, height: sh)

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
req.recognitionLanguages = ["pt-BR", "en-US"]

try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])

var out: [String] = []
func emit(_ text: String, _ box: CGRect) {
    let cx = Double(box.origin.x) + Double(box.width) / 2.0
    let cy = 1.0 - (Double(box.origin.y) + Double(box.height) / 2.0)
    let esc = text.data(using: .utf8).flatMap {
        String(data: try! JSONSerialization.data(withJSONObject: [String(data: $0, encoding: .utf8)!]), encoding: .utf8)
    } ?? "[\"\"]"
    let t = String(esc.dropFirst().dropLast())
    out.append("{\"t\":\(t),\"nx\":\(cx),\"ny\":\(cy)}")
}
// Vision returns one observation per LINE, so a tab bar ("Home  Shorts  You")
// or a pair of buttons ("Cancel   OK") came back as one item whose centre sat
// between the targets — the agent asked for "Shorts" and tapped the gap. Each
// line is split at gaps clearly wider than a space (a segment is a run of words
// whose spacing is normal), so every button on a row gets its own centre.
for obs in (req.results ?? []) {
    guard let top = obs.topCandidates(1).first else { continue }
    let str = top.string
    var words: [(String, CGRect)] = []
    var i = str.startIndex
    while i < str.endIndex {
        while i < str.endIndex, str[i].isWhitespace { i = str.index(after: i) }
        if i >= str.endIndex { break }
        var j = i
        while j < str.endIndex, !str[j].isWhitespace { j = str.index(after: j) }
        if let r = try? top.boundingBox(for: i..<j) {
            words.append((String(str[i..<j]), r.boundingBox))
        }
        i = j
    }
    if words.count < 2 { emit(str, obs.boundingBox); continue }
    // A typical space is about a third of the line height; anything past a
    // full line height is a gap between separate controls.
    let gapLimit = Double(obs.boundingBox.height) * 1.0
    var seg: [(String, CGRect)] = [words[0]]
    func flush() {
        let text = seg.map { $0.0 }.joined(separator: " ")
        let x0 = seg.map { Double($0.1.minX) }.min()!, x1 = seg.map { Double($0.1.maxX) }.max()!
        let y0 = seg.map { Double($0.1.minY) }.min()!, y1 = seg.map { Double($0.1.maxY) }.max()!
        emit(text, CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0))
    }
    for w in words.dropFirst() {
        let gap = Double(w.1.minX) - Double(seg.last!.1.maxX)
        if gap > gapLimit { flush(); seg = [w] } else { seg.append(w) }
    }
    flush()
}
print("{\"w\":\(Int(sw)),\"h\":\(Int(sh)),\"sw\":\(Int(scr.width)),\"sh\":\(Int(scr.height)),\"lines\":[\(out.joined(separator: ","))]}")
"""

DRAG_SWIFT = r"""// PhoneFlow drag — a real mouse drag via CoreGraphics.
//
// AppleScript-ObjC cannot pass a CGEventRef back into CGEventPost (it fails to
// coerce to {__CGEvent=}), so the drag cannot live in an inline script the way
// `click at` does. This binary is the equivalent that actually works.
//
// usage: pf_drag <x> <y>                          -> click
//        pf_drag <x> <y> <holdMs>                 -> long press
//        pf_drag <x1> <y1> <x2> <y2> <durationMs> -> drag
import Foundation
import CoreGraphics

let a = CommandLine.arguments
guard a.count >= 3, let x1 = Double(a[1]), let y1 = Double(a[2]) else {
    FileHandle.standardError.write("usage: pf_drag x y | x1 y1 x2 y2 durationMs\n".data(using: .utf8)!)
    exit(2)
}
let isClick = a.count < 6
let holdMs = a.count == 4 ? (Double(a[3]) ?? 0.0) : 0.0
let x2 = isClick ? x1 : (Double(a[3]) ?? x1)
let y2 = isClick ? y1 : (Double(a[4]) ?? y1)
let ms = isClick ? 0.0 : (Double(a[5]) ?? 300.0)

let src = CGEventSource(stateID: .hidSystemState)

// Where the owner's pointer was before we hijack it, so we can put it back.
let origin = CGEvent(source: nil)?.location ?? CGPoint(x: x1, y: y1)

func post(_ type: CGEventType, _ x: Double, _ y: Double) {
    CGEvent(mouseEventSource: src, mouseType: type,
            mouseCursorPosition: CGPoint(x: x, y: y), mouseButton: .left)?
        .post(tap: .cghidEventTap)
}

// A click still needs the cursor moved there first: System Events' `click at`
// posts no real mouse event, so iPhone Mirroring ignored it entirely — moving
// then pressing is what the mirrored phone actually reacts to.
post(.mouseMoved, x1, y1)
usleep(useconds_t(80_000))

if isClick {
    post(.leftMouseDown, x1, y1)
    // A long press is the same gesture held: iOS opens context menus on
    // duration, so the only difference from a tap is how long the button is down.
    usleep(useconds_t(max(holdMs, 60.0) * 1000))
    post(.leftMouseUp, x1, y1)
    // Return the pointer to where the owner left it (see `origin`), so the agent
    // does not leave the cursor sitting on the phone window between actions.
    usleep(useconds_t(20_000))
    CGEvent(mouseEventSource: src, mouseType: .mouseMoved,
            mouseCursorPosition: origin, mouseButton: .left)?.post(tap: .cghidEventTap)
    print("OK")
    exit(0)
}

// iOS reads a swipe from the movement between press and release, so the path is
// interpolated: a single jump from start to end registers as a tap, not a swipe.
let steps = 24
let perStep = max(ms, 120.0) / Double(steps)
post(.leftMouseDown, x1, y1)
usleep(useconds_t(30_000))
for i in 1...steps {
    let t = Double(i) / Double(steps)
    post(.leftMouseDragged, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
    usleep(useconds_t(perStep * 1000))
}
post(.leftMouseUp, x2, y2)
usleep(useconds_t(20_000))
CGEvent(mouseEventSource: src, mouseType: .mouseMoved,
        mouseCursorPosition: origin, mouseButton: .left)?.post(tap: .cghidEventTap)
print("OK")
"""

KEY_SWIFT = r"""// PhoneFlow keyboard — types text into iPhone Mirroring with REAL key events.
//
// System Events `keystroke "..."` posts synthetic character events that iPhone
// Mirroring drops (its Spotlight field stays empty), the same way it ignored a
// synthetic mouse click. Raw `key code` events do reach it — that is how Cmd-1
// and Cmd-3 work — so text is typed here as real CGEvent key events carrying a
// Unicode string, which the mirror forwards to the phone like a hardware
// keyboard. The window must already be frontmost (the driver focuses it first).
//
// usage: pf_key <text>
import Foundation
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: pf_key <text>\n".data(using: .utf8)!)
    exit(2)
}
let text = args[1]
let src = CGEventSource(stateID: .hidSystemState)

for ch in text.utf16 {
    var unichar = ch
    if let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true) {
        down.keyboardSetUnicodeString(stringLength: 1, unicodeString: &unichar)
        down.post(tap: .cghidEventTap)
    }
    usleep(4_000)
    if let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false) {
        up.keyboardSetUnicodeString(stringLength: 1, unicodeString: &unichar)
        up.post(tap: .cghidEventTap)
    }
    usleep(12_000) // a hair between characters so the field keeps up
}
print("OK")
"""

SCROLL_SWIFT = r"""// PhoneFlow scroll — a trackpad-style scroll gesture, not a mouse drag.
//
// iPhone Mirroring reacts to real trackpad scroll semantics, not to a pressed
// mouse dragged across the window (that grabs videos, selects text, or does
// nothing). This posts continuous scroll-wheel events carrying the CGEvent
// gesture phase fields — MayBegin to wake the handler, Began/Changed while the
// "finger" moves, Ended on lift, then a decaying momentum tail so feeds and
// carousels flick to the next page. Modelled on Mirroir's CGEventInput.swift.
//
// usage: pf_scroll <midX> <midY> <fingerDX> <fingerDY> [durationMs]
//   fingerDX/DY is the finger movement in points; content follows the finger.
import Foundation
import CoreGraphics

let a = CommandLine.arguments
guard a.count >= 5,
      let mx = Double(a[1]), let my = Double(a[2]),
      let dx = Double(a[3]), let dy = Double(a[4]) else {
    FileHandle.standardError.write("usage: pf_scroll midX midY fingerDX fingerDY [durationMs]\n".data(using: .utf8)!)
    exit(2)
}
let durationMs = max(min(a.count > 5 ? (Int(a[5]) ?? 260) : 260, 2000), 80)

let amplification = 3.0
let flickThreshold = 500.0
let decay = 0.94
let momentumMax = 90
let frameMs = 16
let minDrag = 5
let warpSettleUs: UInt32 = 100_000
let frameUs: UInt32 = 16_000

let mid = CGPoint(x: mx, y: my)
let scrollPhase = CGEventField(rawValue: 99)!
let momentumPhase = CGEventField(rawValue: 123)!
let isContinuous = CGEventField(rawValue: 88)!
let pDeltaY = CGEventField(rawValue: 96)!
let pDeltaX = CGEventField(rawValue: 97)!

func makeScroll(_ w1: Int32, _ w2: Int32) -> CGEvent? {
    guard let s = CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
                          wheelCount: 2, wheel1: w1, wheel2: w2, wheel3: 0) else { return nil }
    s.location = mid
    s.setIntegerValueField(isContinuous, value: 1)
    s.setIntegerValueField(pDeltaY, value: Int64(w1))
    s.setIntegerValueField(pDeltaX, value: Int64(w2))
    return s
}
func emit(_ w1: Int32, _ w2: Int32, phase: Int64, momentum: Int64) {
    guard let s = makeScroll(w1, w2) else { return }
    s.setIntegerValueField(scrollPhase, value: phase)
    s.setIntegerValueField(momentumPhase, value: momentum)
    s.post(tap: .cghidEventTap)
}

// Establish the cursor inside the window and wake the scroll subsystem.
CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: mid, mouseButton: .left)?
    .post(tap: .cghidEventTap)
usleep(50_000)
emit(0, 0, phase: 128, momentum: 0)   // MayBegin
usleep(warpSettleUs)

// Content follows the finger: wheel sign matches finger delta.
let totalW1 = dy * amplification
let totalW2 = dx * amplification
let seconds = Double(durationMs) / 1000.0
let velocity = (dx * dx + dy * dy).squareRoot() / max(seconds, 0.001)
let dragSteps = max(minDrag, durationMs / frameMs)
let stepDelayUs = UInt32(durationMs) * 1000 / UInt32(dragSteps)

func splitEven(_ total: Double, _ n: Int) -> [Int32] {
    var out = [Int32](); var acc = 0.0; var placed: Int64 = 0
    for i in 1...n {
        acc = total * Double(i) / Double(n)
        let target = Int64(acc.rounded())
        out.append(Int32(target - placed)); placed = target
    }
    return out
}

if velocity >= flickThreshold {
    // Flick: ~30% in the drag phase, ~70% in a decaying momentum tail.
    let dragW1 = splitEven(totalW1 * 0.3, dragSteps)
    let dragW2 = splitEven(totalW2 * 0.3, dragSteps)
    for i in 0..<dragSteps {
        emit(dragW1[i], dragW2[i], phase: i == 0 ? 1 : 2, momentum: 0)
        usleep(stepDelayUs)
    }
    emit(0, 0, phase: 4, momentum: 0)   // Ended
    usleep(frameUs)
    // Momentum: geometric decay carrying the remaining 70%.
    var remW1 = totalW1 * 0.7, remW2 = totalW2 * 0.7
    // Peak per-frame velocity ~ remaining * (1-decay)
    var fW1 = remW1 * (1 - decay), fW2 = remW2 * (1 - decay)
    for i in 0..<momentumMax {
        if abs(remW1) < 1 && abs(remW2) < 1 { break }
        let w1 = Int32(fW1.rounded()), w2 = Int32(fW2.rounded())
        emit(w1, w2, phase: 0, momentum: i == 0 ? 1 : 2)
        remW1 -= fW1; remW2 -= fW2; fW1 *= decay; fW2 *= decay
        usleep(frameUs)
    }
    emit(0, 0, phase: 0, momentum: 3)   // momentum End
} else {
    // Deliberate drag-scroll: even frames, no momentum.
    let w1 = splitEven(totalW1, dragSteps), w2 = splitEven(totalW2, dragSteps)
    for i in 0..<dragSteps {
        emit(w1[i], w2[i], phase: i == 0 ? 1 : 2, momentum: 0)
        usleep(stepDelayUs)
    }
    emit(0, 0, phase: 4, momentum: 0)   // Ended
}
print("OK")
"""

ICONS_SWIFT = r"""// PhoneFlow icon detection — local CoreML object detector over a screenshot.
//
// OCR (pf_ocr) reads text; this finds the tappable ICONS that carry no text —
// a magnifier, a bell, tab-bar glyphs — which text recognition structurally
// cannot return. It runs the OmniParser `icon_detect` YOLO (or any Vision
// object-detection .mlpackage/.mlmodelc) entirely on the Mac, so no frame ever
// leaves the machine and a step costs tens of milliseconds instead of a cloud
// round trip. The model is supplied by the owner (see PHONEFLOW_ICON_MODEL);
// when it is absent the driver simply skips icon detection.
//
// Prints one JSON object on stdout, coordinates normalised to the image with a
// TOP-LEFT origin so it maps exactly like pf_ocr's boxes:
//   {"w":<px>,"h":<px>,"boxes":[{"nx":cx,"ny":cy,"nw":w,"nh":h,"conf":c}]}
import Foundation
import Vision
import CoreML
import AppKit

let args = CommandLine.arguments
guard args.count > 2 else {
    FileHandle.standardError.write("usage: pf_icons <image> <model.mlpackage|.mlmodelc> [conf]\n".data(using: .utf8)!)
    exit(2)
}
let imagePath = args[1], modelPath = args[2]
let conf = args.count > 3 ? (Double(args[3]) ?? 0.20) : 0.20

guard let img = NSImage(contentsOfFile: imagePath),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot read image\n".data(using: .utf8)!); exit(2)
}
let W = Double(cg.width), H = Double(cg.height)

// Compiling a .mlpackage takes ~1.5s, so it is done once and the compiled
// .mlmodelc is cached beside the model; every later call loads that directly
// and the whole run is back to tens of milliseconds. A .mlmodelc is used as-is.
let url = URL(fileURLWithPath: modelPath)
func compiledURL() -> URL? {
    if modelPath.hasSuffix(".mlmodelc") { return url }
    let cache = url.deletingPathExtension().appendingPathExtension("mlmodelc")
    if FileManager.default.fileExists(atPath: cache.path) { return cache }
    guard let tmp = try? MLModel.compileModel(at: url) else { return nil }
    try? FileManager.default.removeItem(at: cache)
    try? FileManager.default.copyItem(at: tmp, to: cache)
    return FileManager.default.fileExists(atPath: cache.path) ? cache : tmp
}
guard let cURL = compiledURL(),
      let mlmodel = try? MLModel(contentsOf: cURL),
      let vnModel = try? VNCoreMLModel(for: mlmodel) else {
    FileHandle.standardError.write("cannot load model at \(modelPath)\n".data(using: .utf8)!); exit(3)
}

let req = VNCoreMLRequest(model: vnModel)
// Letterbox to match YOLO training; Vision reports boxes back in image space.
req.imageCropAndScaleOption = .scaleFit

try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])

var out: [String] = []
for obs in (req.results as? [VNRecognizedObjectObservation] ?? []) {
    guard Double(obs.confidence) >= conf else { continue }
    let b = obs.boundingBox // normalised, bottom-left origin
    let cx = Double(b.origin.x) + Double(b.width) / 2.0
    let cy = 1.0 - (Double(b.origin.y) + Double(b.height) / 2.0) // flip to top-left
    out.append(String(format: "{\"nx\":%.5f,\"ny\":%.5f,\"nw\":%.5f,\"nh\":%.5f,\"conf\":%.3f}",
                      cx, cy, Double(b.width), Double(b.height), Double(obs.confidence)))
}
print("{\"w\":\(Int(W)),\"h\":\(Int(H)),\"boxes\":[\(out.joined(separator: ","))]}")
"""

OBSERVE_SCRIPT = r"""#!/bin/sh
# PhoneFlow observation — one round trip instead of fourteen.
#
# Capturing a frame used to cost ~14 separate calls through the Plow relay
# (capture, read, base64, window, crop, OCR, JPEG, read, base64...), each with
# its own approval and latency, which dominated every step of the agent loop.
# This does the whole thing on the Mac and prints one JSON object:
#   {"ocr": <pf_ocr output>, "frame": "<base64 jpeg of the window crop>"}
#
# usage: pf_observe.sh <x> <y> <w> <h> <scale> [upscale] [jpegmax]
set -e
X=$1; Y=$2; W=$3; H=$4; S=$5; UP=${6:-3}; JMAX=${7:-900}
D="${TMPDIR:-/tmp}"; F="$D/pf_full.png"; C="$D/pf_win.png"; J="$D/pf_win.jpg"
screencapture -x "$F"
# Crop in pixels (the capture is Retina, the window is in points), then upscale:
# at 316x696pt the tab-bar labels are ~8pt and Vision misses them entirely.
sips -c "$(echo "$H*$S" | bc | cut -d. -f1)" "$(echo "$W*$S" | bc | cut -d. -f1)" \
     --cropOffset "$(echo "$Y*$S" | bc | cut -d. -f1)" "$(echo "$X*$S" | bc | cut -d. -f1)" \
     "$F" --out "$C" >/dev/null
# -Z sets the LONGEST side, which for a phone window is the height. Passing the
# width here silently shrank the crop instead of enlarging it, undoing the
# upscale that lets Vision read the ~8pt tab-bar labels.
LONG=$(echo "$H*$S*$UP" | bc | cut -d. -f1)
sips -Z "$LONG" "$C" >/dev/null
OCR=$("$HOME/.phoneflow/pf_ocr" "$C")
# Local icon detection over the same crop, only if the owner supplied a model
# (PHONEFLOW_ICON_MODEL); absent, icons is null and the driver skips them.
ICON_MODEL="$HOME/.phoneflow/icon_detect.mlpackage"
ICONS=null
if [ -e "$ICON_MODEL" ] && [ -x "$HOME/.phoneflow/pf_icons" ]; then
  ICONS=$("$HOME/.phoneflow/pf_icons" "$C" "$ICON_MODEL" 2>/dev/null || echo null)
fi
sips -s format jpeg -s formatOptions 70 -Z "$JMAX" "$C" --out "$J" >/dev/null
printf '{"ocr":%s,"icons":%s,"frame":"%s"}' "$OCR" "$ICONS" "$(base64 -i "$J")"
"""

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
    key_bin = KEY_BIN
    # Typing alone does nothing: the iPhone has to be on the home screen with
    # Spotlight open first. iPhone Mirroring maps Cmd-1 to Home and Cmd-3 to
    # Spotlight (key codes 18 and 20), so the sequence is Home, Spotlight, type,
    # Return. Without the first two the keystrokes land on whatever the phone
    # was already showing and the app never opens.
    return f'''
tell application "iPhone Mirroring" to activate
delay 0.4
tell application "System Events"
  set frontmost of application process "iPhone Mirroring" to true
  delay 0.3
  key code 18 using command down -- Cmd-1: Home Screen
  delay 1.6
  key code 20 using command down -- Cmd-3: Spotlight
  delay 1.8
  do shell script "{key_bin} " & quoted form of "{app}"
  delay 2.2
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

    def _focus(self) -> None:
        """Bring the mirror window frontmost before sending keystrokes.

        `keystroke` goes to whatever app is frontmost on the Mac, not to a
        window we name — so without this an open_app/type_text would be typed
        into whatever the owner happened to have in front.
        """
        src = (
            'tell application "iPhone Mirroring" to activate\n'
            'delay 0.25\n'
            'tell application "System Events" to set frontmost of '
            'application process "iPhone Mirroring" to true'
        )
        self.scripts.append(src)
        self._applescript(src)

    def open_app(self, app: str) -> None:
        src = open_app_script(app)
        self.commands.append(("open_app", app))
        self.scripts.append(src)
        self._ensure_ocr()  # builds pf_key, used to type the app name
        self._focus()
        self._applescript(src)

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
            self._call("plow_write_file", {"path": self._on_mac(source), "content": code})
            try:
                self._applescript(
                    'do shell script "mkdir -p %s && xcrun swiftc -O -o %s %s && echo %s > %s.sha"'
                    % (OCR_DIR, binary, source, stamp, binary)
                )
            except DriverError as exc:
                raise DriverError("ocr_unavailable", f"could not build {binary}: {exc}") from exc
        self._ocr_ready = True

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
        self._focus()
        src = 'do shell script "%s %d %d"' % (DRAG_BIN, int(x), int(y))
        self.commands.append(("tap_point", x, y))
        self.scripts.append(src)
        self._applescript(src)

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
        self._focus()
        src = 'do shell script "%s %d %d %d"' % (DRAG_BIN, int(x), int(y), hold_ms)
        self.commands.append(("long_press", x, y))
        self.scripts.append(src)
        self._applescript(src)

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
        self._focus()
        # Trackpad-style scroll, not a mouse drag: the drag grabs videos and
        # selects text; a scroll-wheel gesture with phase + momentum flicks the
        # feed the way a finger does. dx/dy is the finger movement; content
        # follows it, and pf_scroll amplifies to real content distance.
        src = 'do shell script "%s %d %d %d %d 220"' % (SCROLL_BIN, int(x), int(y), int(dx), int(dy))
        self.scripts.append(src)
        self._applescript(src)

    def system_key(self, button: str) -> None:
        """Home / App Switcher / Spotlight, via iPhone Mirroring's own shortcuts.

        Cmd-1, Cmd-2 and Cmd-3 (key codes 18/19/20). `back` is not one of them —
        iOS has no back button, so it is the left-edge swipe below.
        """
        codes = {"home": 18, "app_switcher": 19, "spotlight": 20}
        if button not in codes:
            raise DriverError("unknown_button", f"no such system button: {button!r}")
        self.commands.append(("system_key", button))
        self._focus()
        src = 'tell application "System Events" to key code %d using command down' % codes[button]
        self.scripts.append(src)
        self._applescript(src)

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
        self._focus()
        src = 'do shell script "%s %d %d %d %d 400"' % (DRAG_BIN, cx, y1, cx, y2)
        self.scripts.append(src)
        self._applescript(src)
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
        self._focus()
        src = 'do shell script "%s %d %d %d %d 300"' % (
            DRAG_BIN, int(pos[0] + 3), int(y), int(pos[0] + size[0] * 0.6), int(y))
        self.scripts.append(src)
        self._applescript(src)

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
        self._focus()
        src = 'do shell script "%s %d %d %d %d %d"' % (
            DRAG_BIN, int(x1), int(y1), int(x2), int(y2), max(duration_ms, 200))
        self.scripts.append(src)
        self._applescript(src)

    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None:
        _, _, pos, size = self._window()

        def _abs(p: dict) -> tuple[float, float]:
            return (pos[0] + p["x"] * size[0], pos[1] + self.titlebar_px + p["y"] * max(size[1] - self.titlebar_px, 1))

        x1, y1 = _abs(frm)
        x2, y2 = _abs(to)
        self.commands.append(("swipe", frm, to, duration_ms))
        self._focus()  # an unfocused window eats the press, same as taps
        self._ensure_ocr()
        # Trackpad scroll from the midpoint; the finger delta is (to - from).
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        src = 'do shell script "%s %d %d %d %d %d"' % (
            SCROLL_BIN, int(mx), int(my), int(x2 - x1), int(y2 - y1), max(duration_ms, 200))
        self.scripts.append(src)
        self._applescript(src)

    def type_text(self, text: str) -> None:
        self.commands.append(("type_text", text))
        self._ensure_ocr()
        self._focus()
        # Real CGEvent key events (pf_key), not System Events keystroke, which
        # the mirror drops — the field would stay empty. See pf_key.swift.
        src = 'do shell script "%s " & quoted form of %s' % (KEY_BIN, json.dumps(text))
        self.scripts.append(src)
        self._applescript(src)

    def vault_fill(self, vault_item_id: str) -> str:
        # spec §11: the fill is manual; the owner completes the Mac auth prompt
        # and then Approves in the canvas.
        raise DriverError("vault_manual", "complete the Mac auth prompt manually, then Approve")
