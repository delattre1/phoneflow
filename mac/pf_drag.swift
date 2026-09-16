// PhoneFlow drag — a real mouse drag via CoreGraphics.
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
print("OK")
