// PhoneFlow idle gate — wait until the owner lets go of the mouse and keyboard.
//
// iPhone Mirroring only reacts to the Mac's real cursor and keyboard focus, so
// every PhoneFlow action borrows them for a moment. Borrowing them while the
// owner is mid-drag or mid-sentence is what makes sharing the Mac unpleasant:
// the pointer jumps away, or a few typed letters land on the phone. This waits
// for a short gap in the owner's own input before the driver takes its turn.
//
// usage: pf_idle [needSeconds] [maxWaitSeconds]
//   exits 0 as soon as no mouse/keyboard event has happened for needSeconds,
//   or after maxWaitSeconds regardless (a busy owner must not stall a run
//   forever). Prints "idle" or "timeout".
//
//        pf_idle perms
//   prints which macOS privacy grants this process tree (Latch, its parent)
//   holds, as JSON, WITHOUT triggering a prompt. The setup doctor reads it to
//   tell the owner exactly which switch is still off.
import Foundation
import CoreGraphics
import ApplicationServices

let a = CommandLine.arguments
if a.count > 1 && a[1] == "perms" {
    print("{\"accessibility\":\(AXIsProcessTrusted() && CGPreflightPostEventAccess()),"
        + "\"screenRecording\":\(CGPreflightScreenCaptureAccess())}")
    exit(0)
}
let need = a.count > 1 ? (Double(a[1]) ?? 0.7) : 0.7
let maxWait = a.count > 2 ? (Double(a[2]) ?? 10.0) : 10.0

let watched: [CGEventType] = [
    .mouseMoved, .leftMouseDown, .leftMouseDragged, .rightMouseDown,
    .keyDown, .scrollWheel,
]
func idleFor() -> Double {
    watched.map { CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) }.min() ?? need
}

let start = Date()
while Date().timeIntervalSince(start) < maxWait {
    if idleFor() >= need { print("idle"); exit(0) }
    usleep(100_000)
}
print("timeout")
