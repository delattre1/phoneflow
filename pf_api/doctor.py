"""The setup doctor: one call that says what is still missing, in plain words.

`/api/health` only ever knew "Latch up / Latch down", so a new owner found out
about a missing macOS permission when their first task failed with a blank
frame. The doctor probes each requirement and answers an ordered checklist the
chat agent can relay one step at a time — see the pf-setup skill.

Each check is {id, ok, title, fix, pane?}: `ok` is True, False, or None when it
could not be checked yet (something before it is missing). `fix` is written for
someone who is not a developer. `pane` names a System Settings pane that
POST /api/doctor/open can open on the Mac for them.
"""

from __future__ import annotations

from pf_api import settings

_CHECKS = [
    ("llmKey", "AI model access",
     "PhoneFlow needs an AI model to plan taps. This agent was not installed through Plow, "
     "so it has no model credits of its own: send an API key for an OpenAI-compatible "
     "endpoint and I will save it (stored on your agent, never shown again).", None),
    ("llmModel", "AI model available on the endpoint",
     "The planner model is not offered by the model endpoint. Pick one it lists "
     "(the `detail` says which are available) with PUT /api/config {\"agentModel\": \"...\"}.", None),
    ("latch", "Plow Latch is running on the Mac",
     "Open the Plow Latch app on your Mac, sign in, and leave it running.", None),
    ("helpers", "PhoneFlow helpers installed on the Mac",
     "I install these myself. If it keeps failing, open Terminal on the Mac, run "
     "'xcode-select --install', finish the installer, then ask me to check again.", None),
    ("automation", "Permission: Automation (System Events)",
     "On the Mac: System Settings > Privacy & Security > Automation > Plow Latch > "
     "turn on System Events. If macOS shows a pop-up asking, click OK.", "automation"),
    ("accessibility", "Permission: Accessibility",
     "On the Mac: System Settings > Privacy & Security > Accessibility > turn on "
     "Plow Latch. This is what lets me tap and type.", "accessibility"),
    ("screenRecording", "Permission: Screen Recording",
     "On the Mac: System Settings > Privacy & Security > Screen & System Audio "
     "Recording > turn on Plow Latch, then quit and reopen Plow Latch. This is what "
     "lets me see the phone.", "screenRecording"),
    ("mirrorWindow", "iPhone Mirroring window is open",
     "Open the iPhone Mirroring app on your Mac and connect to your iPhone. Keep the "
     "iPhone locked and nearby, and leave the mirroring window open on the home screen.", None),
]


def run(driver) -> dict:
    probe = getattr(driver, "doctor", None)
    if probe is None:
        # Drivers with no Mac behind them (the fake one) have nothing to grant.
        raw = {cid: True for cid, *_ in _CHECKS}
        raw["detail"] = {}
    else:
        raw = probe()
    raw["llmKey"] = bool(settings.llm_api_key())
    detail = raw.get("detail") or {}
    raw["llmModel"] = None
    if raw["llmKey"]:
        try:
            report = settings.model_report(probe=True)
        except Exception as exc:  # noqa: BLE001
            report = None
            detail["llmModel"] = f"could not reach the model endpoint: {str(exc)[:200]}"
        if report and report.get("planner"):
            planner, grounder = report["planner"], report["grounder"]
            # Only a listed endpoint that lacks the model is a failure; one that
            # does not list models cannot be checked and is not blocked on.
            raw["llmModel"] = planner["found"] is not False
            served = report["available"]
            detail["llmModel"] = (
                f"planner {planner['wanted']} -> {planner['resolved']}"
                f"{'' if planner['found'] is not False else ' (NOT served: ' + planner.get('error', '')[:120] + ')'}; "
                f"grounder {grounder['wanted'] or 'off'} -> "
                f"{grounder['resolved'] if grounder['found'] is not False else 'not served, icons by OCR/local model only'}"
                + (f"; endpoint lists {len(served)} models" if served is not None else "; endpoint does not list models")
            )
        else:
            raw["llmModel"] = True
    checks = []
    for cid, title, fix, pane in _CHECKS:
        ok = raw.get(cid)
        item = {"id": cid, "ok": ok, "title": title}
        if ok is not True:
            item["fix"] = fix
            if pane:
                item["pane"] = pane
        if detail.get(cid):
            item["detail"] = detail[cid]
        checks.append(item)
    missing = [c for c in checks if c["ok"] is False]
    unknown = [c for c in checks if c["ok"] is None]
    return {
        "ready": not missing and not unknown,
        # The one thing to tell the owner now. Later steps often cannot even be
        # checked until this one is done, so the list is walked in order.
        "next": (missing or unknown or [None])[0],
        "checks": checks,
    }
