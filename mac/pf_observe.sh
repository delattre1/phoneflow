#!/bin/sh
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
