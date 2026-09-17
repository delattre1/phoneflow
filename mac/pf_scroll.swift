// PhoneFlow scroll — a trackpad-style scroll gesture, not a mouse drag.
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
