---
name: pf-mirror
description: Drive the owner's iPhone Mirroring window through Plow Latch. Use when a PhoneFlow run needs a screenshot, tap, swipe, type, or vault fill on the Mac.
---

Only use plow_ MCP tools. Never SSH to the Mac.

1. plow_list_skills then plow_read_skill for the Mac's own window/screenshot skill if present.
2. Find the window titled iPhone Mirroring or Espelhamento do iPhone. Missing → tell the owner to open iPhone Mirroring; error code mirror_window_missing.
3. Screenshots go to the run's frames directory. Relay host-gate text unmodified.
4. Clicks are window-local, subtracting MIRROR_TITLEBAR_PX (default 28).
5. auth.vaultUnlock: ask Latch to fill vaultItemId into the Mac auth prompt. Do not print the secret.
