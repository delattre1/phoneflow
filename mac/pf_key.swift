// PhoneFlow keyboard — types text into iPhone Mirroring with REAL key events.
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
