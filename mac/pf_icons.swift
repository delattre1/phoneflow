// PhoneFlow icon detection — local CoreML object detector over a screenshot.
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
