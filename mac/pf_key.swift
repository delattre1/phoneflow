// PhoneFlow keyboard — types text into iPhone Mirroring with REAL key codes.
//
// iPhone Mirroring forwards hardware key events to the phone, keyed by the
// virtual key code (that is why Cmd-1/Cmd-3 work). A CGEvent that carries only
// a Unicode string with virtualKey 0 is NOT forwarded — Spotlight stays empty.
// So each character is sent as its US-ANSI virtual key code, with the Shift
// flag for uppercase and shifted symbols, exactly like a physical keyboard.
// The mirror window must be frontmost (the driver focuses it first).
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

// US-ANSI virtual key codes.
let base: [Character: CGKeyCode] = [
    "a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,
    "w":13,"e":14,"r":15,"y":16,"t":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,
    "=":24,"9":25,"7":26,"-":27,"8":28,"0":29,"]":30,"o":31,"u":32,"[":33,"i":34,
    "p":35,"l":37,"j":38,"'":39,"k":40,";":41,"\\":42,",":43,"/":44,"n":45,"m":46,
    ".":47,"`":50," ":49,
]
// Characters typed with Shift held.
let shifted: [Character: CGKeyCode] = [
    "!":18,"@":19,"#":20,"$":21,"%":23,"^":22,"&":26,"*":28,"(":25,")":29,
    "_":27,"+":24,"{":33,"}":30,"|":42,":":41,"\"":39,"<":43,">":47,"?":44,"~":50,
]

func press(_ code: CGKeyCode, shift: Bool) {
    // Set flags explicitly on every event: a bare event inherits the source's
    // tracked modifier state, so shift from a previous uppercase char leaks into
    // the next ones ("TikTok" -> "TikTOK"). Empty flags clears it.
    let flags: CGEventFlags = shift ? .maskShift : []
    let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)
    down?.flags = flags
    down?.post(tap: .cghidEventTap)
    usleep(4_000)
    let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)
    up?.flags = flags
    up?.post(tap: .cghidEventTap)
    usleep(12_000)
}

for ch in text {
    if let code = base[ch] {
        press(code, shift: false)
    } else if let low = Character(ch.lowercased()) as Character?, let code = base[low], ch.isUppercase {
        press(code, shift: true)                 // uppercase letter
    } else if let code = shifted[ch] {
        press(code, shift: true)                 // shifted symbol
    } else {
        // Fallback: unicode event (works for the few chars with no US key code).
        var u = Array(String(ch).utf16)
        if let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true) {
            down.keyboardSetUnicodeString(stringLength: u.count, unicodeString: &u); down.post(tap: .cghidEventTap)
        }
        usleep(4_000)
        if let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false) {
            up.keyboardSetUnicodeString(stringLength: u.count, unicodeString: &u); up.post(tap: .cghidEventTap)
        }
        usleep(12_000)
    }
}
print("OK")
