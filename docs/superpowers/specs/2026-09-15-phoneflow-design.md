# PhoneFlow Design

**Date:** 2026-09-15
**Status:** Draft for review
**Hackathon:** [Hermes Hackathon](https://luma.com/3uftu95w) — submit by 2026-09-16, leaderboard 2026-09-23 13:00 PT

PhoneFlow is a visual workflow canvas, in the spirit of n8n, that lets a person compose iPhone actions. It is **not** n8n and does not import n8n graphs. The canvas is a page that boots next to the Hermes agent. Hermes executes the graph. Plow Latch on the owner's Mac drives the iPhone Mirroring window.

## 1. Problem

Brazilian banks and many iPhone-only apps have no usable web API. The owner already has:

- a Linux box **`sued@suedpc.local`** that runs the Hermes agent (deploy target)
- this MacBook with Plow Latch and iPhone Mirroring

They want to draw a flow (open app → tap → type → confirm) and have the agent run it on the mirrored phone.

The hackathon requires a **Hermes** agent, MIT license, Agent Index registration, and the AI Worth Using reporter. A standalone React toy does not qualify.

## 2. Goals

1. An installer (`docker compose up`) starts Hermes **and** the PhoneFlow canvas on one host.
2. The owner draws a graph, hits Run, and the iPhone Mirroring window on the Mac performs those steps.
3. One bundled playbook completes a real on-device chore end to end (proof screenshot + run log).
4. Device/Mac passwords never enter the model, the workflow JSON, or the UI. Latch vault fills them.
5. Money movement always stops on a Confirm node the owner must accept for that run.

## 3. Non-goals (v1)

- n8n, Zapier, or any third-party workflow import
- Login, accounts, OAuth, multi-user
- Auto-submitting a payment without Confirm
- Driving apps via official bank APIs (web Latch path is a later node type)
- Windows / Android mirroring
- Public internet exposure of the canvas
- Marketplace of community nodes
- Arbitrary code / JS nodes

## 4. Architecture

```
Owner browser  ──HTTP──►  PhoneFlow UI + API   (same container as Hermes)
                                │
                                │  workflow JSON on $HERMES_HOME/phoneflow/
                                ▼
                         Hermes agent (sued@suedpc.local Docker, FROM plow-hermes-agent)
                                │
                                │  MCP plow_* via Plow relay
                                ▼
                         Plow Latch (Mac)
                                │
                                │  AppleScript / screencapture / cliclick
                                ▼
                         iPhone Mirroring window ──► physical iPhone
```

The UI never clicks the phone. The graph is the program. Hermes is the interpreter. Latch is the only process that touches the Mac.

### 4.1 Topology

| Piece | Where it runs |
| --- | --- |
| Hermes gateway + PhoneFlow HTTP | **`sued@suedpc.local`** (Linux). Deploy with `ssh sued@suedpc.local`. Image `FROM` [plow-hermes-agent](https://github.com/plow-pbc/plow-hermes-agent). Checkout path on that host: `~/phoneflow-hermes-agent` |
| Plow Latch | this MacBook (`/Users/sued`, Darwin) |
| iPhone Mirroring | same Mac, paired iPhone (macOS 15+ / iOS 18+) |
| Canvas browser | this Mac opens `http://suedpc.local:8787` |

The Mac dials **out** to the Plow relay. `suedpc.local` does not need a public inbound port for Latch. It does need LAN port **8787** so the Mac can load the canvas.

Deploy is always over SSH: `ssh sued@suedpc.local`. If mDNS fails (`No route to host`), wake the PC and confirm both machines are on the same LAN before compose.

### 4.2 Canvas bind

- Compose on `suedpc.local` publishes `0.0.0.0:8787:8787` (LAN). The Mac is the browser; loopback on Linux is useless.
- No authentication in v1. README must say: LAN only — do not put port 8787 on the public internet.
- Health check from this Mac: `curl -sf http://suedpc.local:8787/api/health`

## 5. Visual language

Copy the Hermes docs chrome, swap the gold.

| Token | Hermes docs | PhoneFlow |
| --- | --- | --- |
| Sans | Inter 300–700 | same |
| Mono | JetBrains Mono 400–500 | same |
| Dark bg | `#07070d` | same |
| Surface | `#0f0f18` | same |
| Body text | `#e8e4dc` | same |
| Secondary | `#9a968e` | same |
| Accent | `#FFD700` | **`#2CD4C3` (aurora teal)** |
| Accent dark | `#E6C200` | `#26B8A9` |
| Accent muted border | `rgba(255,215,0,0.08)` | `rgba(44,212,195,0.10)` |
| Code bg | `#0a0a12` | same |
| Radius | 8px buttons | same |
| Navbar | blur 12px, 1px accent-tinted border | same, teal |
| Background | 32px radial dot grid in accent at 2% | same, teal |

Default theme is dark. Light mode is optional and uses teal `#1A7A72` on white (WCAG AA).

Layout:

- Top navbar: wordmark **PhoneFlow**, link to run log, no login, no avatar.
- Left rail: workflow list + node palette.
- Center: React Flow canvas (`@xyflow/react`).
- Right: selected-node inspector.
- Bottom: run console (status, last screenshot thumbnail, Confirm button when the interpreter is parked).

## 6. Components

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `web/` | Vite + React canvas, no auth | PhoneFlow HTTP API |
| `phoneflow-api` | FastAPI (or stdlib HTTP) inside the agent image, port 8787 | workflow files, run store, Hermes skill trigger |
| `phoneflow-run` skill | Interprets a graph, one node at a time | `iphone-mirroring` skill, Latch MCP |
| `iphone-mirroring` skill | Focus window, screenshot, tap/swipe/type in window coords | Latch `plow_run_command`, `plow_run_applescript` |
| `agent-index` s6 service | Token usage reporter | `PLOW_AGENT_TOKEN`, `AGENT_ID=phoneflow` |
| Latch vault | Stores Mac login password / passcode item | owner enrollment in Latch app |

## 7. Workflow data

Path: `$HERMES_HOME/phoneflow/workflows/{id}.json`

```json
{
  "id": "wf_settings_general",
  "name": "Open Settings → General",
  "version": 1,
  "nodes": [
    {
      "id": "n1",
      "type": "trigger.manual",
      "position": { "x": 0, "y": 0 },
      "params": {}
    },
    {
      "id": "n2",
      "type": "phone.openApp",
      "position": { "x": 0, "y": 120 },
      "params": { "app": "Settings" }
    }
  ],
  "edges": [
    { "id": "e1", "source": "n1", "target": "n2" }
  ]
}
```

Rules:

- `id` is `wf_` + `[a-z0-9_]{1,64}`.
- `type` must be in the v1 catalog below. Unknown type → run fails before start.
- `params` must match that type's schema. Extra keys rejected.
- **No secret values.** Auth nodes carry only `vaultItemId` (string). If a `phone.type` param is named `text` and looks like a password, the API still stores it as ordinary text — the inspector copy must say "use VaultUnlock for secrets".
- Positions are UI-only; the interpreter ignores them.

Runs: `$HERMES_HOME/phoneflow/runs/{runId}/`

- `run.json` — `{id, workflowId, status, currentNodeId, error, startedAt, finishedAt}`
- `events.jsonl` — one JSON object per line
- `frames/{seq}.png` — screenshots (not secrets)

`status`: `queued | running | awaiting_confirm | awaiting_vault | succeeded | failed | cancelled`

## 8. Node catalog (v1)

| Type | Params | Effect |
| --- | --- | --- |
| `trigger.manual` | none | Canvas Run button. Exactly one trigger per graph. |
| `trigger.chat` | none | iMessage/Plow chat "run {workflow name}". Same interpreter. |
| `phone.openApp` | `app: string` | Spotlight / Home search on the mirrored iPhone, open that app. |
| `phone.screenshot` | none | Capture iPhone Mirroring window. Store frame. Expose to later If/Tap. |
| `phone.tap` | `x?: number, y?: number, label?: string` | Either normalized 0–1 coords in the mirror window, or a visible label the locator must find. One of `label` or both `x,y` required. |
| `phone.swipe` | `from: {x,y}, to: {x,y}, durationMs?: number` | Normalized window coords. |
| `phone.type` | `text: string` | Keystrokes into the focused iPhone field. Not for vault secrets. |
| `phone.wait` | `ms: number` (max 30000) | Sleep. |
| `flow.if` | `match: string` (substring or `/regex/`) | True edge if the latest screenshot OCR/text contains match; else false edge. Two outgoing edges named `true` and `false`. |
| `auth.vaultUnlock` | `vaultItemId: string` | Ask Latch to fill that vault item into the **Mac** auth prompt that iPhone Mirroring shows for Face ID fallback. Model never receives the secret. Parks as `awaiting_vault` until Latch reports filled or denied. |
| `flow.confirm` | `prompt: string` | Park `awaiting_confirm`. Canvas shows the prompt + last frame + Approve/Deny. Chat path: owner must reply yes. |
| `flow.stop` | `reason?: string` | End run succeeded (if no error) or failed if `reason` starts with `fail:`. |

Edges: default single outgoing. `flow.if` requires `sourceHandle` `true` | `false`. Missing handle → fail at validation.

Cycles: allowed, max 50 node visits per run, then fail `cycle_limit`.

## 9. Execution

1. API validates the graph (one trigger, known types, If handles, Confirm present before any node whose `params` contain `"submit"` / `"pagar"` / `"pay"` **or** whose `phone.tap.label` matches `/pagar|pay|enviar|confirm(ar)?|submit/i`). If a graph can tap a pay/submit control and has no `flow.confirm` ancestor on every path to that tap, validation fails.
2. Write `runs/{id}/run.json` `queued`, then `running`.
3. Walk from the trigger in edge order.
4. Each phone node: `iphone-mirroring` skill → Latch. After every phone node, take a screenshot and append an event.
5. Locator for `phone.tap` with `label`: OCR/vision on the last frame, return window-relative coords, then click. If not found: retry 3 times with 800 ms wait, then fail the node.
6. Unexpected lock/passcode screen (detected by frame text `Passcode` / `Touch ID` / `Enter Password` / Portuguese `Código` / `Senha do Mac`): if the next node is `auth.vaultUnlock`, continue; else park `awaiting_vault` and tell the owner to add/run VaultUnlock or authenticate on the Mac.
7. Failures set `status=failed`, keep last frame, stop. No infinite retry.
8. Success: `succeeded`, last frame is the proof.

Hermes does not invent taps outside the graph. Vision is only allowed inside `phone.tap` `label` resolution and `flow.if` matching.

## 10. iPhone Mirroring driver

Skill `iphone-mirroring` documents the Latch commands. It does not shell out from Linux to the Mac except through `plow_*`.

Required Mac-side operations (implemented as Latch-approved commands / AppleScript, not a new daemon):

1. Find window whose title contains `iPhone Mirroring` (localized titles: also `Espelhamento do iPhone`). If missing: fail `mirror_window_missing`.
2. Screenshot that window to a temp PNG; `plow_read_file` brings bytes back; store under the run.
3. Click / drag / keystroke in **window-local** coordinates. Convert normalized 0–1 using the window bounds excluding the Mac titlebar. Titlebar offset is measured once per run (`MIRROR_TITLEBAR_PX`, default 28, overridable in `$HERMES_HOME/phoneflow/config.json`).
4. `phone.openApp`: swipe to Home if needed, open Spotlight on the phone (swipe down), type app name, tap the result.

TCC on the Mac (owner grants once): Accessibility, Screen Recording, Automation for System Events. Latch already surfaces host-gate failures; the skill must relay those messages to the run log, not guess.

## 11. Auth and money

iPhone Mirroring Face ID fallback is a **Mac** prompt (Touch ID or Mac login password). Banks then reuse that unlocked session.

- `auth.vaultUnlock` is the only node that may cause a password to be typed.
- Latch fills the vault item into the prompt. The fill never appears in MCP tool results, events.jsonl, or the canvas.
- `vaultItemId` is an opaque Latch id the owner pastes from Latch. PhoneFlow does not list vault contents.
- Transaction PIN / card CVV / SMS OTP: not vault-filled in v1. `flow.confirm` parks and the owner completes that step on the phone or Mac, then Approve.
- Confirm is mandatory on every path that taps a pay/submit control (see §9.1).
- Always-allow Latch rules are the owner's choice in the Latch app. PhoneFlow does not auto-click Latch approval cards.

## 12. HTTP API

Base: `http://<host>:8787`

| Method | Path | Body / result |
| --- | --- | --- |
| `GET` | `/` | SPA |
| `GET` | `/api/health` | `{ok: true, latch: "up"|"down"}` |
| `GET` | `/api/workflows` | list `{id,name}[]` |
| `GET` | `/api/workflows/{id}` | full document |
| `PUT` | `/api/workflows/{id}` | body = document, validates schema |
| `DELETE` | `/api/workflows/{id}` | 204 |
| `POST` | `/api/runs` | `{workflowId}` → `{runId}` |
| `GET` | `/api/runs/{runId}` | run.json |
| `GET` | `/api/runs/{runId}/events` | SSE of events |
| `GET` | `/api/runs/{runId}/frames/{seq}` | PNG |
| `POST` | `/api/runs/{runId}/confirm` | `{decision: "approve"|"deny"}` |
| `POST` | `/api/runs/{runId}/cancel` | empty |

No cookies, no tokens. CORS: same origin only (UI is served from this port).

`latch: "down"` when `plow_list_skills` fails. Canvas shows a banner: open Latch on the Mac.

## 13. Agent packaging (hackathon)

Repository: `phoneflow-hermes-agent` (this checkout).

Follow [life-assistant-hermes-agent](https://github.com/plow-pbc/life-assistant-hermes-agent):

- `Dockerfile` `FROM` the published plow-hermes-agent image
- `compose.yml` builds this tree, mounts `plow-credentials`, publishes 8787
- `runtime/persona.md` — PhoneFlow identity: execute graphs, do not freelance iPhone taps
- skills under `pf-run/`, `pf-mirror/`, `pf-setup/`
- s6 longrun `agent-index` copied from life-assistant, `AGENT_ID=phoneflow`
- MIT `LICENSE`
- `.gitignore` includes `plow-credentials`

Register:

```sh
python3 agent_index_client.py --register --agent phoneflow \
  --name "PhoneFlow" \
  --blurb "Draw iPhone actions; Hermes runs them through Latch + iPhone Mirroring." \
  --runtime "Hermes" \
  --repo "https://github.com/<owner>/phoneflow-hermes-agent"
```

Install path for others (Agent Index `--install-url`): README with `plow-agents login/mint`, Latch on Mac, pair iPhone Mirroring, `docker compose up`, open `:8787`.

## 14. Bundled playbook (the qualifying chore)

Ship two graphs:

1. **`wf_settings_general`** — open Settings, tap General, screenshot. No secrets. Used in CI with a mock driver.
2. **`wf_app_lookup`** — template: `openApp` (param empty until owner fills) → optional `vaultUnlock` → taps the owner records → `confirm` → `screenshot`. README instructs the owner to record **one real chore they already do** (balance check, boleto view, search in an app) and run it once before requesting verification.

The Agent Index story is that recorded run, not the Settings demo.

## 15. Errors

| Code | When | Owner sees |
| --- | --- | --- |
| `mirror_window_missing` | no iPhone Mirroring window | Open iPhone Mirroring on the Mac |
| `latch_disconnected` | MCP down | Open Latch, check relay |
| `element_not_found` | label tap failed 3x | last frame + label |
| `cycle_limit` | 50 visits | graph has a loop |
| `confirm_denied` | owner denied | run failed |
| `vault_denied` | Latch denied fill | run failed |
| `host_gate` | TCC/sandbox | Latch's host-gate text, unmodified |
| `validation` | bad graph | API 400 with path |

Events always include `nodeId`, `code`, `at`. Screenshots are kept on failure.

## 16. Testing

Automated (no iPhone):

- Workflow JSON schema accept/reject fixtures
- Interpreter: linear graph, If both branches, Confirm gate blocks pay-tap, cycle limit, vault park
- Validation: pay-tap without Confirm rejected
- API: PUT invalid extra key → 400; secrets not echoed from vault nodes

Mock: `IPhoneDriver` protocol. Tests inject `FakeDriver` that returns canned frames.

Not automated in v1: real mirroring, real Latch, real Face ID prompt.

Manual smoke before submit: Settings playbook on the owner's phone, plus the owner's one real chore.

## 17. File map

```
phoneflow-hermes-agent/
  Dockerfile
  compose.yml
  LICENSE                  # MIT
  README.md
  runtime/persona.md
  web/                     # Vite React canvas
  pf-api/                  # HTTP API served in-image
  pf-run/SKILL.md          # interpreter instructions + python
  pf-mirror/SKILL.md       # Latch driver
  pf-setup/SKILL.md        # first-boot: Latch + mirroring checklist
  image/s6-overlay/s6-rc.d/phoneflow-api/
  image/s6-overlay/s6-rc.d/agent-index/
  tests/                   # interpreter + schema
  docs/superpowers/specs/  # this file
  docs/superpowers/plans/  # implementation plan (next)
```

## 18. Constraints (copied for the plan)

- License: MIT
- Base image: plow-hermes-agent
- `AGENT_ID=phoneflow`
- Reporter: plow-pbc/agent-index-client, no disable switch
- UI fonts: Inter + JetBrains Mono
- UI accent: `#2CD4C3` on `#07070d`
- UI: no login
- Canvas port: 8787
- Secrets: Latch vault only
- Interpreter does not tap outside the graph
- Confirm required on every path to a pay/submit tap
- Deadline: Agent Index page live 2026-09-16
