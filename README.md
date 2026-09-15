# PhoneFlow

Draw iPhone chores on a canvas; Hermes runs them through Plow Latch and
iPhone Mirroring on a nearby Mac.

- **License:** MIT (see [LICENSE](LICENSE)).
- **Network:** LAN only. The API binds `0.0.0.0:8787` so any device on the
  home network can open the canvas. **Never publish port 8787 to the
  internet.**

## Topology

```
┌──────────────────┐  canvas / API   ┌────────────────────┐
│  suedpc.local    │◄───────────────►│  your Mac          │
│  docker compose  │  :8787 (LAN)    │  Latch             │
│  PhoneFlow agent │                 │  iPhone Mirroring  │
└──────────────────┘                 └────────────────────┘
        │                                     │
        └────────── runs phone actions ───────┘
```

- **suedpc.local** hosts the PhoneFlow agent in Docker and serves the canvas
  at `http://suedpc.local:8787`.
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
curl -sf http://suedpc.local:8787/api/health
# → {"ok": true, "latch": "up"}   latch: "down" means Latch is not open on the Mac
```

## On the Mac

1. Install Plow **Latch** and open it (leave it running).
2. Pair **iPhone Mirroring** with the iPhone and keep the mirroring window
   visible.
3. Grant macOS **Accessibility** and **Screen Recording** to Latch (and
   `cliclick` if you use it).
4. Open `http://suedpc.local:8787` in a browser.

If `/api/health` reports `latch: "down"`, stop and open Latch first.

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