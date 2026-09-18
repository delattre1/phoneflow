---
name: pf-hints
description: Read, add, change or remove PhoneFlow's per-app notes (app hints). Use when the owner teaches you how an app works, asks what you know about an app, or a task failed because an app's layout was not what the agent expected.
---

App hints are short notes, one per app, that the phone agent reads before it works in that app: where a tab is, what a renamed feature is called now, which popup to dismiss, how to confirm something worked. The owner's notes are kept on their own agent and win over the built-in ones. All on `http://127.0.0.1:8787`:

- `GET /api/hints` → `[{"app": "instagram", "source": "bundled"|"owner"}, ...]`
- `GET /api/hints/<app>` → `{"app", "source", "text"}`
- `PUT /api/hints/<app>` with `{"text": "<the whole note>"}` → saves the owner's note for that app. `<app>` is the app's name in lower case, the way the owner would say it in a task ("instagram", "x", "google maps" → `google%20maps`).
- `DELETE /api/hints/<app>` → removes the owner's note; the built-in one (if any) applies again.

## Changing a hint

PUT replaces the whole note, so never send only the new sentence:

1. `GET /api/hints/<app>`. If it exists, start from its `text` (even when `source` is `bundled` — the owner's copy replaces the bundled one entirely).
2. Add or fix the lines. Keep the format: a `# App name` title, then `- ` bullets, each one a concrete instruction about what is on screen and what to tap. Write them in English, in the imperative, no stories. Keep the note short — under ~25 bullets.
3. `PUT` the full text. Tell the owner in one line what you noted ("Got it — I'll remember that the Reels tab is the middle one").

Never put passwords, codes or personal data in a hint. Do not write hints for banking, wallet or password apps.

When a run fails inside an app and the run's messages show the agent looking for something that is not there, ask the owner what the screen really shows, then save that as a hint and offer to try again.
