#!/bin/sh
# Build the Mac helpers into mac/bin/ as universal binaries, plus a manifest.
#
# A new Mac used to need the Xcode Command Line Tools and about a minute of
# swiftc before the first tap. With these checked in, the driver ships the
# binary through Latch instead and only compiles when no prebuilt one matches
# the source. Run this after changing any mac/*.swift and commit the result:
#
#   sh mac/build.sh
#
# manifest.json maps each helper to the hash of the SOURCE it was built from
# (the same 16-hex stamp the driver keys on) and the sha256 of the binary, which
# the Mac checks after decoding. tests/test_prebuilt.py fails when a source
# changed and this was not re-run.
set -eu
cd "$(dirname "$0")"
mkdir -p bin
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
printf '{' > "$TMP/manifest"
first=1
for src in pf_*.swift; do
  name=${src%.swift}
  xcrun swiftc -O -target arm64-apple-macos14 -o "$TMP/$name.arm64" "$src"
  xcrun swiftc -O -target x86_64-apple-macos14 -o "$TMP/$name.x86_64" "$src"
  lipo -create -output "bin/$name" "$TMP/$name.arm64" "$TMP/$name.x86_64"
  strip -x "bin/$name"
  codesign --force --sign - "bin/$name"
  stamp=$(shasum -a 256 "$src" | cut -c1-16)
  sum=$(shasum -a 256 "bin/$name" | cut -d' ' -f1)
  [ $first = 1 ] || printf ',' >> "$TMP/manifest"
  first=0
  printf '\n  "%s": {"src": "%s", "sha256": "%s"}' "$name" "$stamp" "$sum" >> "$TMP/manifest"
done
printf '\n}\n' >> "$TMP/manifest"
mv "$TMP/manifest" bin/manifest.json
ls -l bin
