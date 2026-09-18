# Installing PhoneFlow

PhoneFlow lets you send a plain-language phone task to your Plow agent — over
iMessage or the canvas — and it carries it out on a real iPhone: opening apps,
tapping, typing, scrolling, and reporting back what it found. Any app works;
no per-task setup.

> **Language:** this guide is in English. Versão em português:
> [INSTALL.pt-BR.md](INSTALL.pt-BR.md).

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

- The **agent host** (a Linux box or a Mac; the examples call it `suedpc`) runs
  PhoneFlow in Docker and holds the planner.
- **Your Mac** runs Plow **Latch** and **iPhone Mirroring**; every tap, type and
  screenshot happens there. It can be the same machine as the host or a separate
  one on the same network.
- The **iPhone** is driven while **locked**, through iPhone Mirroring.

**Security:** LAN only — never publish port 8788 to the internet. The agent's
persona tells it never to act on banking, wallet or password apps. That is an
instruction, not a hard block: for an enforced refusal, list the app names in
`PHONEFLOW_BLOCKED_APPS`, which is empty by default.

## What you need

- **Agent host:** Docker + the docker compose plugin, and the
  [`plow-agents`](https://github.com/plow-pbc/plow-agents) tooling.
- **Mac:** macOS 15+ (Apple Silicon), Plow **Latch**, **iPhone Mirroring**
  paired with your iPhone. The Mac helpers ship prebuilt; the **Xcode Command
  Line Tools** (`xcode-select --install`) are only a fallback.
- **A model for the planner.** An agent installed through Plow already has one:
  the planner uses the agent's own Plow model credits (the same gateway the chat
  agent uses), default model `anthropic/claude-sonnet-5` (Plow's gateway does not allow GLM or Qwen). Optionally bring your own key for an
  OpenAI-compatible endpoint with `PHONEFLOW_LLM_API_KEY` (and
  `PHONEFLOW_LLM_BASE_URL`, default `https://ollama.com/v1`), or by chat.

---

## Part 1 — The agent host (Docker)

```sh
# on the agent host
git clone https://github.com/plow-pbc/plow-agents ~/plow-agents
export PATH="$HOME/plow-agents/bin:$PATH"

git clone https://github.com/visued/phoneflow ~/phoneflow
cd ~/phoneflow

plow-agents login          # authenticate to Plow
plow-agents lines          # list your lines
plow-agents mint ln_xxx    # writes ./plow-credentials next to compose.yml

# the planner needs an LLM key (agent tasks); put it in the environment:
# optional — empty, the planner runs on the agent's own Plow model credits:
# export PHONEFLOW_LLM_API_KEY=sk-...          # your own OpenAI-compatible key
export PHONEFLOW_LLM_BASE_URL=https://ollama.com/v1   # default
# export PHONEFLOW_AGENT_MODEL=...              # planner override (default follows the provider)

docker compose up --build -d
```

> **Keep `plow-credentials` safe — it is your agent's identity.** Your Agent Index
> page belongs to the Plow agent that first published it, and `plow-agents mint`
> always creates a **new** agent. Never revoke and mint again: use
> **`plow-agents rotate`** to replace a credential, and back the file up
> (`cp -p plow-credentials ~/.config/plow/backups/`).

The first build needs network access (it fetches the pinned Agent Index
client). Verify the API is up:

```sh
curl -sf http://<host>:8788/api/health
# {"ok": true, "latch": "up"}   ← "latch" is "down" until the Mac side is ready
```

## Part 2 — The Mac (Latch + iPhone Mirroring)

1. Install and open **Plow Latch**; leave it running.
2. Open **iPhone Mirroring** and pair it with your iPhone. Keep the mirror
   window visible on the **home screen**.
3. Grant macOS permissions **to Latch** in System Settings → Privacy & Security:
   - **Screen Recording** — screenshots of the mirror window. Without it every
     frame is blank.
   - **Accessibility** — real clicks, drags, scrolls and key presses.
   - **Automation → System Events** — finding and focusing the mirror window and
     pressing the Home, App Switcher and Spotlight shortcuts. macOS asks the
     first time; answer **OK**.

   After granting Screen Recording, **quit and reopen Latch** so it picks the
   grant up. The helpers run as children of Latch and inherit these grants.
4. Keep the iPhone **locked** — iPhone Mirroring only drives a locked phone (the
   Mac session acts unlocked). If you unlock the phone, the mirror disconnects.

When both are running, `/api/health` reports `latch: "up"`.

## The Mac helpers

iPhone Mirroring ignores most synthetic input, so PhoneFlow ships small helpers
that run on the Mac. **You never install them by hand**, and a new Mac does not need Xcode. Universal prebuilt binaries are checked in under [`mac/bin/`](../mac/bin) (built by `sh mac/build.sh`); on first use the driver ships each one to `~/.phoneflow/` through Latch and verifies its sha256. Only when no prebuilt binary matches the helper's source does it fall back to compiling with `xcrun swiftc -O`, which needs the Xcode Command Line Tools. A hash of the source sits next to each binary, so a helper that changes in the repo is replaced automatically.

| Helper | What it does |
|---|---|
| `pf_observe.sh` | **The screenshot helper.** One call captures the screen, crops the mirror window, upscales it, runs OCR and icon detection, and returns one JSON object with the recognised text and a base64 JPEG frame. It replaced about 14 round trips per step. |
| `pf_ocr` | macOS Vision text recognition, with the position of every line. |
| `pf_icons` | Local CoreML icon detector. Used only when the model from Part 3 is installed. |
| `pf_drag` | Real mouse events: click, long press and drag. |
| `pf_scroll` | Trackpad-style scroll gesture with momentum, so feeds flick to the next page. |
| `pf_key` | Types text with real key codes, which is what iPhone Mirroring forwards. |
| `pf_idle` | Waits for a pause in your own mouse and keyboard use before each action, and reports which macOS permissions Latch holds for the setup doctor. |

## Part 3 — Icon detection model (recommended)

For precise taps on icons with no text (a magnifier, a bell, tab-bar glyphs),
PhoneFlow uses a small local CoreML model on the Mac. It is **owner-supplied**,
so it is not bundled. Install it once on the Mac:

```sh
# on the Mac
pip3 install --user ultralytics huggingface_hub
python3 - <<'PY'
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
PY
```

> **License note:** OmniParser's `icon_detect` is **AGPL-3.0**. It is used here
> as a separate, owner-supplied component (not redistributed with PhoneFlow,
> which is MIT). Without it, PhoneFlow still runs — it falls back to text OCR and
> a cloud grounder for icons.

## Part 4 — Register the agent (for iMessage)

Register PhoneFlow in the Plow Agent Index so your Plow agent can reach it:

```sh
docker compose exec agent /opt/hermes/.venv/bin/python3 \
  /opt/plow/agent-index-client.py --register --agent phoneflow-agent \
  --name "PhoneFlow" \
  --blurb "Send a phone task; it runs on my iPhone via Latch + iPhone Mirroring." \
  --runtime "Hermes"
```

(The container also self-registers on first boot when it finds itself
unregistered.)

## Part 5 — Use it

With the Mac ready (Latch up, iPhone locked, mirror on the home screen):

- **From iMessage**, text your Plow agent naturally:
  - `run wf_youtube_trending` — run a workflow you drew in the canvas, or
  - *"Open YouTube, go to the trending (Hype) tab and tell me the first 3 video
    titles."* — a plain-language task; no workflow needed.
  - *"Open X, go to @elonmusk's official profile and tell me his latest post."*
- **From the canvas** at `http://<host>:8788`, draw a workflow and run it
  (optional — the canvas is not required for the iMessage flow).

The agent replies with what it found (e.g. the post text, the titles).

## Notes & tips

- **App knowledge is extensible, by chat.** App hints teach the agent an app's layout, renamed features and popups. Bundled: Gmail, Instagram, Safari, Settings, Spotify, TikTok, WhatsApp, X and YouTube. Tell the agent what it should know ("in Instagram the Reels tab is the middle one") and it saves a note on your own install, which wins over the bundled one. Same thing over the API: `GET/PUT/DELETE /api/hints/<app>`. Developers can still drop a `.md` file in [`app_hints/`](../app_hints).
- **Setup is checked, not assumed.** `GET /api/doctor` probes Latch, the helpers, each macOS permission, the mirror window and the LLM key, and names the next thing to fix. On your first message the agent walks you through it one step at a time and opens the right Settings page on the Mac. The LLM key and blocked apps can be set by chat too (`PUT /api/config`); they are stored on the agent's volume and override the environment.
- **The cursor is borrowed, politely.** iPhone Mirroring only reacts to the Mac's real pointer and keyboard focus. Posting events straight to its process does not work: we tested clicks, drags and scrolls, focused and not, and other projects document the same. So each action waits for a short pause in your own mouse and keyboard use (`PHONEFLOW_IDLE_NEED`, default 0.7 s, giving up after `PHONEFLOW_IDLE_MAX`, default 10 s), brings the mirror forward, acts, then returns the pointer and the focus to the app you were in. You can keep working during a run, with brief interruptions. For zero interference, run Latch and iPhone Mirroring on a spare Mac.
- **Blocked apps.** The list is empty by default. Set
  `PHONEFLOW_BLOCKED_APPS="C6,Nubank,Wallet"` to make the agent refuse apps by
  name (case-insensitive substring match).
- **No phone-side setup.** No Developer Mode, no WebDriverAgent, no signing — just
  iPhone Mirroring. That is also why banking apps that block automation may not
  work, and why nothing needs weekly re-signing.
