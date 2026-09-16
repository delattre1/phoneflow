# PhoneFlow

Draw iPhone chores on a canvas; Hermes runs them through Plow Latch and
iPhone Mirroring on a nearby Mac.

- **License:** MIT (see [LICENSE](LICENSE)).
- **Network:** LAN only. The API listens on **host port `8788`** (container
  stays 8787) so any device on the home network can open the canvas.
  **Never publish port 8788 to the internet.**

## Topology

```
┌──────────────────┐  canvas / API   ┌────────────────────┐
│  suedpc.local    │◄───────────────►│  your Mac          │
│  docker compose  │  :8788 (LAN)    │  Latch             │
│  PhoneFlow agent │                 │  iPhone Mirroring  │
└──────────────────┘                 └────────────────────┘
        │                                     │
        └────────── runs phone actions ───────┘
```

- **suedpc.local** hosts the PhoneFlow agent in Docker and serves the canvas
  at `http://suedpc.local:8788`.
- **Your Mac** runs Plow Latch and iPhone Mirroring; the agent's phone
  actions (tap, type, screenshot, app open) execute there through Latch's
  Accessibility + Screen Recording permissions.

## Install on suedpc

Prerequisites: Docker + docker compose plugin, and
[`plow-agents`](https://github.com/plow-pbc/plow-agents) from the Plow tooling.

```sh
# on suedpc.local
git clone https://github.com/plow-pbc/plow-agents ~/plow-agents
export PATH="$HOME/plow-agents/bin:$PATH"
git clone <repo> ~/phoneflow-hermes-agent
plow-agents login
plow-agents lines
plow-agents mint ln_xxx
cd ~/phoneflow-hermes-agent
docker compose up --build -d
```

`plow-agents mint` writes `./plow-credentials` next to `compose.yml`; the
compose file bind-mounts it read-only into the container so the agent can
authenticate. **First build needs network access** — the Dockerfile fetches
the pinned Agent Index client (`vendor/client.pin`) from GitHub and fails the
build on a bad fetch.

Verify:

```sh
curl -sf http://suedpc.local:8788/api/health
```

## On the Mac

1. Install Plow **Latch** and open it (leave it running).
2. Pair **iPhone Mirroring** with the iPhone and keep the mirroring window
   visible.
3. Grant macOS **Accessibility** and **Screen Recording** to Latch (and
   `cliclick` if you use it).
4. Open `http://suedpc.local:8788` in a browser.

If `/api/health` reports `latch: "down"`, stop and open Latch first.

## Screen reading (OCR)

`phone.tap` by label, `flow.if`, and every `agent.task` need to know what is on
the phone. That comes from macOS Vision, run on the Mac: `mac/pf_ocr.swift` is
compiled once to `~/.phoneflow/pf_ocr` and returns each recognised line with its
screen coordinates. The driver builds it on first use, so no manual step is
needed — the first call just costs about a minute while `swiftc` runs.

Two constraints worth knowing, because they shaped the design:

- **Latch never returns binary file content.** `plow_read_file` inlines text but
  answers only a byte count for a PNG, so the driver has the Mac write a base64
  copy and reads that instead. Frames are JPEG for the same reason — size is
  real cost on this path.
- **`screencapture` cannot run under `plow_run_command`.** That path is
  sandboxed and the capture dies with exit -1. It goes through
  `plow_run_applescript` instead, which runs outside the sandbox. Latch itself
  needs macOS **Screen Recording** permission, and must be restarted after it is
  granted.

## Goal-driven nodes (`agent.task`)

A graph node says *what* to tap. An `agent.task` node says only what you want,
and a vision model works out the taps from what is actually on screen:

```json
{"id": "n3", "type": "agent.task",
 "params": {"goal": "Open the trending tab and read the first 3 video titles.",
            "maxSteps": 18}}
```

Each step sends the model a downscaled frame plus a **numbered list** of the
recognised text; the model replies with an item number, never a coordinate.
Vision owns precision, the model owns judgement. The list only ever contains
text found *inside* the mirror window, so no choice the model can make is able
to click the owner's desktop, and `tap_point` refuses out-of-bounds points
anyway.

Configure the model through the environment (any OpenAI-compatible endpoint):

```sh
PHONEFLOW_LLM_API_KEY=...                      # required for agent.task only
PHONEFLOW_LLM_BASE_URL=https://ollama.com/v1   # default
PHONEFLOW_AGENT_MODEL=kimi-k3                  # must support vision + tools
```

Graph-only workflows run without any of these. Try `wf_youtube_trending` first —
YouTube's trending tab is stable enough to tell a real failure from a flaky one.

## Before a real chore: fill `wf_app_lookup`

The bundled workflow [`workflows/wf_app_lookup.json`](workflows/wf_app_lookup.json)
ships with `"app": "CHANGE_ME"`. Open the canvas, load the workflow, and put
one real app name in the `phone.openApp` node before running "Get my agent
verified" — as shipped, it just Spotlight-searches the literal `CHANGE_ME`
and parks on a confirm.

## Register the agent in the Agent Index
```sh
docker compose exec agent /opt/hermes/.venv/bin/python3 /opt/plow/agent-index-client.py \
  --register --agent phoneflow \
  --name "PhoneFlow" \
  --blurb "Draw iPhone actions; Hermes runs them through Latch + iPhone Mirroring." \
  --runtime "Hermes"
```

The container also self-registers on first boot (the baked-in
`agent-index` s6 service registers with `PLOW_AGENT_TOKEN` when it finds
itself unregistered, then reports usage every 5 minutes). Manual
registration above is the owner-driven path.

## Smoke test

Open the Settings playbook with iPhone Mirroring visible, run it from the
canvas, and confirm the taps land on the mirrored iPhone. Then run the
owner's real chore.