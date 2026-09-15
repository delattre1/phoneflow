---
name: pf-setup
description: First-boot checklist for PhoneFlow. Use when Latch, iPhone Mirroring, or the canvas is not ready.
---

Walk the owner through this list. Do not skip a step.

1. Latch is open on the Mac.
2. iPhone Mirroring is paired and the mirroring window is visible (`iPhone Mirroring` / `Espelhamento do iPhone`).
3. macOS Accessibility and Screen Recording are granted to Latch (and cliclick if used).
4. Latch vault has an item for the Mac login password. Never ask the owner to paste that password into chat.
5. Open the canvas: `http://suedpc.local:8787`.

If `GET http://127.0.0.1:8787/api/health` reports `latch: "down"`, stop and tell them to open Latch on the Mac.
