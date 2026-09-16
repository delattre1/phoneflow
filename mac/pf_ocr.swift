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
for obs in (req.results ?? []) {
    guard let top = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox
    let cx = Double(b.origin.x) + Double(b.width) / 2.0
    let cy = 1.0 - (Double(b.origin.y) + Double(b.height) / 2.0)
    let esc = top.string.data(using: .utf8).flatMap {
        String(data: try! JSONSerialization.data(withJSONObject: [String(data: $0, encoding: .utf8)!]), encoding: .utf8)
    } ?? "[\"\"]"
    let t = String(esc.dropFirst().dropLast())
    out.append("{\"t\":\(t),\"nx\":\(cx),\"ny\":\(cy)}")
}
print("{\"w\":\(Int(sw)),\"h\":\(Int(sh)),\"sw\":\(Int(scr.width)),\"sh\":\(Int(scr.height)),\"lines\":[\(out.joined(separator: ","))]}")
