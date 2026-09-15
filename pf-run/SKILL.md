---
name: pf-run
description: Start a PhoneFlow workflow from chat. Use when the owner says "run <name>" or asks to execute a graph.
---

`python3 -m pf_api` is already the PhoneFlow HTTP server. Do not start another copy. Drive runs through localhost 8787.

1. Resolve `<name>` to a workflow id (`wf_…`). `GET http://127.0.0.1:8787/api/workflows` lists `{id,name}`.
2. Start the run: `POST http://127.0.0.1:8787/api/runs` with JSON `{"workflowId": "<id>"}`. Response is `{runId}`.
3. Poll `GET /api/runs/{runId}` (or subscribe to `GET /api/runs/{runId}/events` SSE).
4. Status `awaiting_confirm` → ask the owner yes/no, then `POST /api/runs/{runId}/confirm` with `{"decision": "approve"}` or `{"decision": "deny"}`.
5. `GET /api/health` latch `down` → tell the owner to open Latch on the Mac. Do not invent taps.

Never paste passwords into chat or into request bodies. Secrets stay in Latch vault (`auth.vaultUnlock` / `vaultItemId` only).
