// PhoneFlow OCR — macOS Vision text recognition over a screenshot.
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
