---
name: pf-run
description: Run a PhoneFlow task from chat — a named workflow ("run <name>") or any plain-language phone instruction.
---

`python3 -m pf_api` is already the PhoneFlow HTTP server. Do not start another copy. Drive runs through localhost 8787.

1. Resolve `<name>` to a workflow id (`wf_…`). `GET http://127.0.0.1:8787/api/workflows` lists `{id,name}`.
2. Start the run: `POST http://127.0.0.1:8787/api/runs`.
   - Named graph: JSON `{"workflowId": "<id>"}`.
   - Plain-language task (anything else the owner asks): JSON `{"goal": "<their request>", "maxSteps": 24}`. No graph needed.
   The response is `{runId, status, result}`; `result.message` (and `result.records`) is the answer to relay back to the owner.
3. Poll `GET /api/runs/{runId}` (or subscribe to `GET /api/runs/{runId}/events` SSE).
4. Status `awaiting_confirm` → ask the owner yes/no, then `POST /api/runs/{runId}/confirm` with `{"decision": "approve"}` or `{"decision": "deny"}`.
5. `GET /api/health` latch `down` → tell the owner to open Latch on the Mac. Do not invent taps.

Never paste passwords into chat or into request bodies. Secrets stay in Latch vault (`auth.vaultUnlock` / `vaultItemId` only).
