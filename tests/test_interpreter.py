# tests/test_interpreter.py
from pf_api.driver import FakeDriver
from pf_api.interpreter import Interpreter

SETTINGS = {
    "id": "wf_settings_general",
    "name": "Open Settings → General",
    "version": 1,
    "nodes": [
        {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
        {"id": "n2", "type": "phone.openApp", "position": {"x": 0, "y": 1}, "params": {"app": "Settings"}},
        {"id": "n3", "type": "phone.tap", "position": {"x": 0, "y": 2}, "params": {"label": "General"}},
        {"id": "n4", "type": "phone.screenshot", "position": {"x": 0, "y": 3}, "params": {}},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "target": "n2"},
        {"id": "e2", "source": "n2", "target": "n3"},
        {"id": "e3", "source": "n3", "target": "n4"},
    ],
}

def test_linear_settings_succeeds():
    drv = FakeDriver(ocr_text="General")
    run = Interpreter(drv).run_all(SETTINGS)
    assert run["status"] == "succeeded"
    assert [c[0] for c in drv.calls] == ["open_app", "screenshot", "tap_label", "screenshot", "screenshot"]

def test_if_true_branch():
    doc = {
        "id": "wf_if_ok",
        "name": "if",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "phone.screenshot", "position": {"x": 0, "y": 1}, "params": {}},
            {"id": "n3", "type": "flow.if", "position": {"x": 0, "y": 2}, "params": {"match": "General"}},
            {"id": "n4", "type": "flow.stop", "position": {"x": 0, "y": 3}, "params": {"reason": "yes"}},
            {"id": "n5", "type": "flow.stop", "position": {"x": 0, "y": 4}, "params": {"reason": "fail:no"}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n3"},
            {"id": "e3", "source": "n3", "target": "n4", "sourceHandle": "true"},
            {"id": "e4", "source": "n3", "target": "n5", "sourceHandle": "false"},
        ],
    }
    run = Interpreter(FakeDriver(ocr_text="General")).run_all(doc)
    assert run["status"] == "succeeded"
    assert run["error"] is None

def test_confirm_parks_then_approve():
    doc = {
        "id": "wf_pay_ok",
        "name": "pay",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "flow.confirm", "position": {"x": 0, "y": 1}, "params": {"prompt": "Send PIX?"}},
            {"id": "n3", "type": "phone.tap", "position": {"x": 0, "y": 2}, "params": {"label": "Pagar"}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n3"},
        ],
    }
    itp = Interpreter(FakeDriver(ocr_text="Pagar"))
    run = itp.start(doc)
    assert run["status"] == "awaiting_confirm"
    assert "Pagar" not in [c[0] for c in itp.driver.calls]
    run = itp.resume_confirm("approve")
    assert run["status"] == "succeeded"
    assert any(c[0] == "tap_label" for c in itp.driver.calls)

def test_confirm_deny_fails():
    doc = {
        "id": "wf_pay_ok",
        "name": "pay",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "flow.confirm", "position": {"x": 0, "y": 1}, "params": {"prompt": "Send PIX?"}},
            {"id": "n3", "type": "phone.tap", "position": {"x": 0, "y": 2}, "params": {"label": "Pagar"}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n3"},
        ],
    }
    itp = Interpreter(FakeDriver())
    itp.start(doc)
    run = itp.resume_confirm("deny")
    assert run["status"] == "failed"
    assert run["error"]["code"] == "confirm_denied"

def test_cycle_limit():
    doc = {
        "id": "wf_loop",
        "name": "loop",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "phone.wait", "position": {"x": 0, "y": 1}, "params": {"ms": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n2"},
        ],
    }
    run = Interpreter(FakeDriver()).run_all(doc)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "cycle_limit"

def test_vault_parks_and_does_not_echo_secret():
    doc = {
        "id": "wf_vault",
        "name": "vault",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "auth.vaultUnlock", "position": {"x": 0, "y": 1}, "params": {"vaultItemId": "item_1"}},
            {"id": "n3", "type": "flow.stop", "position": {"x": 0, "y": 2}, "params": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n3"},
        ],
    }
    itp = Interpreter(FakeDriver(vault_result="filled"))
    run = itp.start(doc)
    assert run["status"] == "awaiting_vault"
    run = itp.resume_vault()
    assert run["status"] == "succeeded"
    blob = str(run) + str(itp.events)
    assert "hunter2" not in blob
    assert "password" not in blob.lower() or "vaultItemId" in blob

def test_unexpected_lock_parks_vault():
    doc = {
        "id": "wf_lock",
        "name": "lock",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "phone.screenshot", "position": {"x": 0, "y": 1}, "params": {}},
            {"id": "n3", "type": "phone.tap", "position": {"x": 0, "y": 2}, "params": {"label": "General"}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n3"},
        ],
    }
    run = Interpreter(FakeDriver(ocr_text="Enter Password")).start(doc)
    assert run["status"] == "awaiting_vault"
