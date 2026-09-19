# PhoneFlow

**Text it. It runs your iPhone.**

PhoneFlow is a Hermes agent that controls a real iPhone from iMessage. You text
a plain-language task, the agent drives the phone through Plow Latch and iPhone
Mirroring on your Mac, and the answer comes back in the same thread. Any app
works, with no per-task setup and nothing installed on the phone.

> "Open TikTok, check the For You page, and give me the vibe of the top 3 videos."

- **Demo video:** https://youtu.be/WYJslTJd-8g
- **Agent Index page:** https://aiworthusing.com/agent-index/phoneflow-agent
- **License:** MIT (see [LICENSE](LICENSE)).
- **Guia em português:** [docs/INSTALL.pt-BR.md](docs/INSTALL.pt-BR.md) ·
  English long form: [docs/INSTALL.md](docs/INSTALL.md).

## How it fits together

```
┌──────────────────────┐   LAN, :8788    ┌──────────────────────────┐
│  Agent host (Docker) │◄───────────────►│  Your Mac (Apple Silicon)│
│  PhoneFlow + Hermes  │                 │  Plow Latch              │
│  the planner brain   │                 │  iPhone Mirroring        │
└──────────────────────┘                 └──────────┬───────────────┘
                                                     │ locked iPhone,
                                                     ▼ mirror window open
                                              ┌────────────┐
                                              │  iPhone    │
                                              └────────────┘
```

- The **agent host** runs PhoneFlow in Docker and holds the planner. It can be
  a Linux box or the Mac itself.
- **Your Mac** runs Plow **Latch** and **iPhone Mirroring**. Every screenshot,
  tap, scroll and keystroke happens there.
- The **iPhone** is driven while **locked**, through iPhone Mirroring.

**Security:** LAN only. The API listens on host port `8788` (the container
stays on 8787). **Never publish port 8788 to the internet.**

The agent's persona tells it never to act on banking, wallet or password apps.
That is an instruction, not a hard block. For an enforced refusal, list the app
names in `PHONEFLOW_BLOCKED_APPS`, which is empty by default.

## Requirements

| Where | What |
|---|---|
| Agent host | Docker with the compose plugin, and the [`plow-agents`](https://github.com/plow-pbc/plow-agents) tooling |
| Mac | macOS 15 or later, Plow **Latch**, **iPhone Mirroring** paired with the iPhone. Xcode Command Line Tools only as a fallback |
| iPhone | Any iPhone that supports iPhone Mirroring, signed in to the same Apple Account as the Mac |
| Accounts | A Plow account with a line, and an API key for an OpenAI-compatible LLM endpoint |

## Install, step by step

### Step 1. Prepare the Mac

1. **Nothing to compile.** The Mac helpers ship prebuilt, so the Xcode Command
   Line Tools are optional. They are only a fallback (`xcode-select --install`)
   if a prebuilt helper cannot be used.
2. **Install Plow Latch**, open it, sign in, and leave it running. Latch is the
   bridge the agent uses to run actions on your Mac.
3. **Pair iPhone Mirroring.** Open the iPhone Mirroring app, pair it with your
   iPhone, and leave the mirror window visible on the phone's **home screen**.
   The window is titled `iPhone Mirroring` (or `Espelhamento do iPhone`).
4. **Keep the iPhone locked.** iPhone Mirroring only drives a locked phone. If
   you unlock the phone, the mirror disconnects.

### Step 2. Grant macOS permissions to Latch

Open **System Settings → Privacy & Security** and grant these to **Latch**:

| Permission | Why PhoneFlow needs it |
|---|---|
| **Screen Recording** | Screenshots of the mirror window. Without it every frame is blank and the agent is blind. |
| **Accessibility** | Real mouse clicks, drags, trackpad scrolls and key presses sent to the mirror window. |
| **Automation → System Events** | Finding the mirror window, bringing it to the front, and pressing Home, App Switcher and Spotlight shortcuts. macOS asks for this the first time; answer **OK**. |

Then **quit and reopen Latch**. A Screen Recording grant is only picked up after
a restart. If you use `cliclick`, grant it Accessibility too.

The Swift helpers run as children of Latch, so they inherit these grants. You
do not need to add them one by one.

### Step 3. Start the agent host

```sh
# on the agent host
git clone https://github.com/plow-pbc/plow-agents ~/plow-agents
export PATH="$HOME/plow-agents/bin:$PATH"

git clone https://github.com/visued/phoneflow ~/phoneflow
cd ~/phoneflow

plow-agents login          # authenticate to Plow
plow-agents lines          # list your lines
plow-agents mint ln_xxx    # writes ./plow-credentials next to compose.yml

# optional — empty, the planner runs on the agent's own Plow model credits
# export PHONEFLOW_LLM_API_KEY=...                     # your own LLM key
# export PHONEFLOW_LLM_BASE_URL=https://ollama.com/v1 # its endpoint
export PHONEFLOW_AGENT_MODEL=glm-5.3                  # default planner

docker compose up --build -d
```

`plow-credentials` is git-ignored and is mounted into the container through
`env_file`. Never commit it.

> **Keep this file safe. It is your agent's identity.** Your Agent Index page
> belongs to the Plow agent that first published it. `plow-agents mint` always
> creates a **new** agent, so revoking and minting again locks you out of your
> own page for good. To replace a credential use **`plow-agents rotate`**, which
> keeps the same agent. Back the file up right after minting:
> ```sh
> mkdir -p ~/.config/plow/backups && chmod 700 ~/.config/plow/backups
> cp -p plow-credentials ~/.config/plow/backups/phoneflow-plow-credentials.$(date +%Y%m%d)
> ```

The first build needs network access. The Agent Index reporter comes with the
Plow base image; this repo ships no copy of it.

### Step 4. Verify

```sh
curl -sf http://<host>:8788/api/health
# {"ok": true, "latch": "up"}
```

`latch` stays `"down"` until Latch is open on the Mac. If it is down, stop and
open Latch first.

### Step 5. Icon detection model (optional, recommended)

Text OCR cannot see icons that carry no text, such as a magnifier, a bell or
tab-bar glyphs. For those PhoneFlow can run a small local CoreML detector on the
Mac. The model is **owner-supplied** and is not bundled. Install it once:

```sh
# on the Mac
pip3 install --user ultralytics huggingface_hub
python3 - <<'PYEOF'
from huggingface_hub import hf_hub_download
from ultralytics import YOLO
import shutil, os
pt = hf_hub_download("microsoft/OmniParser-v2.0", "icon_detect/model.pt")
shutil.copy(pt, "icon_detect.pt")
YOLO("icon_detect.pt").export(format="coreml", imgsz=640, nms=True)
os.makedirs(os.path.expanduser("~/.phoneflow"), exist_ok=True)
shutil.rmtree(os.path.expanduser("~/.phoneflow/icon_detect.mlpackage"), ignore_errors=True)
shutil.copytree("icon_detect.mlpackage", os.path.expanduser("~/.phoneflow/icon_detect.mlpackage"))
print("installed ~/.phoneflow/icon_detect.mlpackage")
PYEOF
```

> **License note:** OmniParser's `icon_detect` is **AGPL-3.0**. It is a
> separate, owner-supplied component and is not redistributed with PhoneFlow,
> which is MIT. Without it PhoneFlow still runs. It falls back to text OCR and a
> cloud grounder for icons.

### Step 6. Register in the Agent Index

The container self-registers on first boot: the base image's `agent-index` service
registers with `PLOW_AGENT_TOKEN` when it finds itself unregistered, then
reports usage every 5 minutes. To register by hand:

```sh
docker compose exec agent /opt/hermes/.venv/bin/python3 /opt/plow/agent-index-client.py \
  --register --agent phoneflow-agent \
  --name "PhoneFlow" \
  --blurb "Send a phone task; it runs on my iPhone via Latch + iPhone Mirroring." \
  --runtime "Hermes"
```

### Step 7. Use it

With Latch up, the iPhone locked, and the mirror on the home screen:

- **From iMessage**, text your Plow agent naturally:
  - *"Open TikTok, check the For You page, and give me the vibe of the top 3 videos."*
  - *"Open YouTube, go to the trending tab and tell me the first 3 video titles."*
  - `run wf_youtube_trending` runs a workflow you drew in the canvas.
- **From the canvas** at `http://<host>:8788`, draw a workflow and run it. The
  canvas is optional for the iMessage flow.

The agent replies with what it found.

## The Mac helpers

iPhone Mirroring ignores most synthetic input, so PhoneFlow ships a set of small
helpers that run on the Mac. **You never install them by hand**, and a new Mac does not need Xcode. Universal prebuilt binaries are checked in under [`mac/bin/`](mac/bin) (built by `sh mac/build.sh`); on first use the driver ships each one to `~/.phoneflow/` through Latch and verifies its sha256. Only when no prebuilt binary matches the helper's source does it fall back to compiling with `xcrun swiftc -O`, which needs the Xcode Command Line Tools. A hash of the source sits next to each binary, so a helper that changes in the repo is replaced automatically.

| Helper | What it does |
|---|---|
| [`pf_observe.sh`](mac/pf_observe.sh) | **The screenshot helper.** One call captures the screen, crops the mirror window, upscales it so small labels are readable, runs OCR and icon detection, and returns a single JSON object with the recognised text and a base64 JPEG frame. It replaced about 14 separate round trips through the Plow relay per step. |
| [`pf_ocr.swift`](mac/pf_ocr.swift) | macOS Vision text recognition. Returns every line with its centre position, normalised to the image with a top-left origin. |
| [`pf_icons.swift`](mac/pf_icons.swift) | Local CoreML icon detector for tappable icons with no text. Used only when the model from Step 5 is installed. No frame leaves the Mac. |
| [`pf_drag.swift`](mac/pf_drag.swift) | Real CoreGraphics mouse events: click, long press and drag. |
| [`pf_scroll.swift`](mac/pf_scroll.swift) | Trackpad-style scroll gesture with phases and a momentum tail, so feeds and carousels flick to the next page. A plain mouse drag does not work in the mirror. |
| [`pf_key.swift`](mac/pf_key.swift) | Types text with real US-ANSI virtual key codes. iPhone Mirroring does not forward Unicode-only key events. |
| [`pf_idle.swift`](mac/pf_idle.swift) | Waits for a pause in your own mouse and keyboard use before each action. Also reports which macOS permissions Latch holds, for the setup doctor. |

Two constraints shaped this design:

- **Latch never returns binary file content.** `plow_read_file` inlines text but
  answers only a byte count for a PNG, so the Mac writes a base64 copy and the
  driver reads that. Frames are JPEG for the same reason: size is real cost on
  this path.
- **`screencapture` cannot run under `plow_run_command`.** That path is
  sandboxed and the capture dies with exit -1. It goes through
  `plow_run_applescript` instead, which runs outside the sandbox. This is why
  Latch itself needs the Screen Recording permission.

The agent also ships three Hermes skills: [`pf-setup`](pf-setup/SKILL.md) is the
first-boot checklist, [`pf-run`](pf-run/SKILL.md) starts a run from chat, and
[`pf-mirror`](pf-mirror/SKILL.md) drives the mirror window through Latch.

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
PHONEFLOW_LLM_API_KEY=...                      # optional; empty = the agent's Plow credits
PHONEFLOW_LLM_BASE_URL=https://ollama.com/v1   # only with your own key
PHONEFLOW_AGENT_MODEL=glm-5.3                  # default; must support vision + tools
```

Graph-only workflows run without any of these. Try `wf_youtube_trending` first —
YouTube's trending tab is stable enough to tell a real failure from a flaky one.

## Before a real chore: fill `wf_app_lookup`

The bundled workflow [`workflows/wf_app_lookup.json`](workflows/wf_app_lookup.json)
ships with `"app": "CHANGE_ME"`. Open the canvas, load the workflow, and put
one real app name in the `phone.openApp` node before running "Get my agent
verified" — as shipped, it just Spotlight-searches the literal `CHANGE_ME`
and parks on a confirm.

## Configuration

Set these in the environment before `docker compose up`.

| Variable | Default | Purpose |
|---|---|---|
| `PHONEFLOW_LLM_API_KEY` | none | Your own key for the planner. Empty, an agent provisioned by Plow plans on its own Plow model credits (`PLOW_API_BASE`, agent token). Can also be set by chat (`PUT /api/config`). |
| `PHONEFLOW_LLM_BASE_URL` | `https://ollama.com/v1` | Endpoint for your own key. Any OpenAI-compatible one. Model names are matched to what the endpoint lists (`glm-5.3` → `z-ai/glm-5.3`). |
| `PHONEFLOW_AGENT_MODEL` | provider default | Planner model; it reads the screenshots, so it must support vision and tools. `anthropic/claude-sonnet-5` on Plow credits (the gateway also serves `moonshotai/kimi-k3`; GLM and Qwen are not allowed there), `glm-5.3` with your own key. |
| `PHONEFLOW_GROUNDER_MODEL` | provider default | Cloud grounder used for icons when the local model is absent. `qwen3.5:397b` with your own key, off on Plow credits (its gateway serves no grounding model); `off` disables it. |
| `PHONEFLOW_BLOCKED_APPS` | empty | Comma list of app names the agent must refuse, for example `"C6,Nubank,Wallet"`. Case-insensitive substring match. |
| `MIRROR_TITLEBAR_PX` | `28` | Title bar height subtracted from window-local clicks. |
| `PLOW_CREDENTIALS` | `./plow-credentials` | Path of the credential file minted by `plow-agents`. |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/api/health` says `latch: "down"` | Open Latch on the Mac and keep it running. |
| Error `mirror_window_missing` | Open iPhone Mirroring and keep the window visible. |
| Frames are black or empty | Grant Screen Recording to Latch, then quit and reopen Latch. |
| Taps or typing do nothing | Grant Accessibility to Latch and allow Automation for System Events. |
| Error `ocr_unavailable` | Install the Xcode Command Line Tools with `xcode-select --install`. |
| The mirror disconnects mid-run | The iPhone was unlocked. Lock it and reconnect. |
| First task is very slow | The helpers are compiling. This happens once, for about a minute. |

## Good to know

- **The cursor is borrowed, politely.** iPhone Mirroring only reacts to the Mac's real pointer and keyboard focus. Posting events straight to its process does not work: we tested clicks, drags and scrolls, focused and not, and other projects document the same. So each action waits for a short pause in your own mouse and keyboard use (`PHONEFLOW_IDLE_NEED`, default 0.7 s, giving up after `PHONEFLOW_IDLE_MAX`, default 10 s), brings the mirror forward, acts, then returns the pointer and the focus to the app you were in. You can keep working during a run, with brief interruptions. For zero interference, run Latch and iPhone Mirroring on a spare Mac.
- **App knowledge is extensible, by chat.** App hints teach the agent an app's layout, renamed features and popups. Bundled: Gmail, Instagram, Safari, Settings, Spotify, TikTok, WhatsApp, X and YouTube. Tell the agent what it should know ("in Instagram the Reels tab is the middle one") and it saves a note on your own install, which wins over the bundled one. Same thing over the API: `GET/PUT/DELETE /api/hints/<app>`. Developers can still drop a `.md` file in [`app_hints/`](app_hints).
- **Setup is checked, not assumed.** `GET /api/doctor` probes Latch, the helpers, each macOS permission, the mirror window and the LLM key, and names the next thing to fix. On your first message the agent walks you through it one step at a time and opens the right Settings page on the Mac. The LLM key and blocked apps can be set by chat too (`PUT /api/config`); they are stored on the agent's volume and override the environment.
- **Mac password prompts.** iPhone Mirroring asks for the Mac password when it connects and for apps that use Face ID. PhoneFlow never types it: Latch's vault fills web pages only, not macOS prompts. In iPhone Mirroring → Settings choose **Authenticate Automatically** to stop the connection prompt; an in-app Face ID prompt parks the run until you unlock on the Mac.
- **No phone-side setup.** No Developer Mode, no WebDriverAgent, no signing.
  That is also why apps that block automation may not work.
- **Smoke test.** Run the bundled Settings workflow from the canvas with the
  mirror visible and confirm the taps land on the phone. Then run a real chore.

Built for the Hermes Hackathon by AI Worth Using and Plow.
