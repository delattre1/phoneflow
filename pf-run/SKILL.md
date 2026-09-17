---
name: pf-run
description: Run a PhoneFlow task from chat — a named workflow ("run <name>") or any plain-language phone instruction.
---

`python3 -m pf_api` is already the PhoneFlow HTTP server. Do not start another copy. Drive runs through localhost 8787.

A phone task takes MINUTES. Never wait for it inside one HTTP call: start it in the background, then poll.

1. Resolve `<name>` to a workflow id (`wf_…`). `GET http://127.0.0.1:8787/api/workflows` lists `{id,name}`.
2. Start the run in the background: `POST http://127.0.0.1:8787/api/runs` with `"async": true`. It answers at once with `{"runId": "...", "status": "running"}`.
   - Named graph: JSON `{"workflowId": "<id>", "async": true}`.
   - Plain-language task (anything else the owner asks): JSON `{"goal": "<their request, word for word>", "maxSteps": 30, "async": true}`. No graph needed.
3. Tell the owner the run has started. Then poll `GET /api/runs/{runId}` every 15–20 seconds (`sleep 15` between calls, each call is short) until `status` is no longer `running`. Keep polling for up to 15 minutes. `result.message` (and `result.records`) is the answer to relay back to the owner.
4. Status `awaiting_confirm` → ask the owner yes/no, then `POST /api/runs/{runId}/confirm` with `{"decision": "approve"}` or `{"decision": "deny"}`.
5. `GET /api/health` latch `down` → tell the owner to open Latch on the Mac. Do not invent taps.

ONE RUN AT A TIME — this matters, a second run can post, send or buy twice:
- A timeout, a dropped connection or an error on YOUR side does NOT mean the run failed. The run keeps going on the phone. Poll its `runId`; never start it again.
- `409 {"code": "busy", "runId": ...}` means a run already holds the phone. Poll THAT `runId` and report its result. Do not retry the POST.
- Only start a new run after the previous one reached `succeeded`, `failed` or `cancelled`, and only if the owner asks again. To stop a run: `POST /api/runs/{runId}/cancel`.
- Report `succeeded` as success even if it took long. Do not call it a failure because of time.

Never paste passwords into chat or into request bodies. Secrets stay in Latch vault (`auth.vaultUnlock` / `vaultItemId` only).
