---
name: pf-setup
description: Get PhoneFlow ready for a new owner, by chat. Use on the owner's first message, when a run fails, when Latch or iPhone Mirroring is not ready, or when they ask "is everything set up?".
---

The owner is not a developer. They will do this from iMessage, one step at a time. The PhoneFlow server on `http://127.0.0.1:8787` checks the real state of their Mac for you — never guess, never recite the whole list.

## The loop

1. `GET http://127.0.0.1:8787/api/doctor`. It answers `{"ready": bool, "next": {...}|null, "checks": [...]}`. Each check has `id`, `ok` (true / false / null = cannot be checked yet), `title`, and when not ok a `fix` written for a non-developer, plus sometimes `pane`.
2. `ready: true` → tell them everything is set, and offer two example tasks ("Open YouTube and tell me the top 3 trending videos", "Open X and tell me the latest post from @nasa"). Stop.
3. Otherwise take ONLY `next`. Send ONE short message: what is missing and what to do, from `fix`, in the owner's language and your own friendly words. If this is the very first message, start with a one-line hello and "let's get you set up, it takes about 3 minutes" and how many steps are left (count the checks that are not `true`).
4. If `next.pane` is set, first `POST /api/doctor/open` with `{"pane": "<pane>"}` — that opens the right Settings page on their Mac — and tell them "I opened the right page on your Mac, just turn on Plow Latch in the list".
5. Ask them to reply "done" (or the word in their language). When they reply, go back to step 1. Re-check every time; do not assume their "done" worked. If the same check fails twice, give the fix again with more detail (`detail` field, in plain words) and ask what they see on screen.

## Step notes

- `llmKey`: PhoneFlow needs a key for its AI model. Ask the owner to send it in one message. Save it with `PUT /api/config` `{"llmApiKey": "<key>"}`. Reply only "saved" — never repeat the key, never write it anywhere else. If they do not have one, tell them to get a key at https://ollama.com (Settings → Keys); other OpenAI-compatible providers also work with `{"llmBaseUrl": "...", "agentModel": "..."}` in the same call.
- `latch`: they must open the Plow Latch app on the Mac and sign in. Nothing else can be checked until this is ok.
- `helpers`: you install these yourself during the doctor call; the first time can take a minute. Only involve the owner if it keeps failing.
- `automation`, `accessibility`, `screenRecording`: macOS privacy switches for **Plow Latch**. Only the owner can flip them. After Screen Recording they must quit and reopen Plow Latch — say so. If Plow Latch is not in the list yet, tell them to use the "+" button and pick Plow Latch from Applications.
- `mirrorWindow`: the iPhone Mirroring app must be open and connected; the iPhone stays locked and near the Mac.

## After setup

- Suggest once: "Want me to refuse some apps no matter what? Banking apps, for example." Save with `PUT /api/config` `{"blockedApps": ["Nubank", "Wallet"]}`. `GET /api/config` shows the current settings (the key is only reported as set or not).
- While a task runs, PhoneFlow uses the Mac's mouse and keyboard for a moment at each step. It waits for a pause in the owner's own typing, then gives the mouse and the window focus back. Tell them this once so it is not a surprise, and that closing the iPhone Mirroring window stops everything.
