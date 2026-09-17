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

**Security:** LAN only — never publish port 8788 to the internet. Banking,
wallet and password apps are blocked by default.

## What you need

- **Agent host:** Docker + the docker compose plugin, and the
  [`plow-agents`](https://github.com/plow-pbc/plow-agents) tooling.
- **Mac:** macOS 15+ (Apple Silicon), Plow **Latch**, and **iPhone Mirroring**
  paired with your iPhone.
- **An LLM key** for an OpenAI-compatible endpoint (the planner). The default
  endpoint is `https://ollama.com/v1` and the default model is `glm-5.3-flash` (set `PHONEFLOW_AGENT_MODEL=kimi-k3` for tougher reasoning).

---

## Part 1 — The agent host (Docker)

```sh
# on the agent host
git clone https://github.com/plow-pbc/plow-agents ~/plow-agents
export PATH="$HOME/plow-agents/bin:$PATH"

git clone <this-repo> ~/phoneflow-hermes-agent
cd ~/phoneflow-hermes-agent

plow-agents login          # authenticate to Plow
plow-agents lines          # list your lines
plow-agents mint ln_xxx    # writes ./plow-credentials next to compose.yml

# the planner needs an LLM key (agent tasks); put it in the environment:
export PHONEFLOW_LLM_API_KEY=sk-...            # your OpenAI-compatible key
export PHONEFLOW_LLM_BASE_URL=https://ollama.com/v1   # default
export PHONEFLOW_AGENT_MODEL=glm-5.3-flash    # default planner (kimi-k3 for harder tasks)

docker compose up --build -d
```

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
3. Grant macOS permissions **to Latch**: System Settings → Privacy & Security →
   **Accessibility** and **Screen Recording**. After granting Screen Recording,
   **quit and reopen Latch** so it picks the grant up.
4. Keep the iPhone **locked** — iPhone Mirroring only drives a locked phone (the
   Mac session acts unlocked). If you unlock the phone, the mirror disconnects.

When both are running, `/api/health` reports `latch: "up"`.

The Mac-side helpers (`pf_ocr`, `pf_drag`, `pf_scroll`) compile themselves on
first use through Latch — no manual step, ~a minute once.

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
  /opt/plow/agent-index-client.py --register --agent phoneflow \
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

- **App knowledge is extensible.** `app_hints/<app>.md` files teach the agent an
  app's layout, renamed features and popups. Bundled: YouTube, Instagram,
  TikTok, WhatsApp, Settings, Safari, Spotify, Gmail, X. Add your own by dropping
  a new `.md` file.
- **The cursor is shared.** While a task runs, it moves the Mac's real pointer to
  click the mirror (iPhone Mirroring only reacts to real mouse events); it
  returns the cursor after each click, but you cannot use the mouse at the same
  time.
- **Blocked apps.** Set `PHONEFLOW_BLOCKED_APPS="C6,Nubank,Wallet"` to refuse
  more apps by name.
- **No phone-side setup.** No Developer Mode, no WebDriverAgent, no signing — just
  iPhone Mirroring. That is also why banking apps that block automation may not
  work, and why nothing needs weekly re-signing.
