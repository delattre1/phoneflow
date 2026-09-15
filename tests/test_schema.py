# tests/test_schema.py
import pytest
from pf_api.schema import SchemaError, validate_workflow

def _wf(**over):
    base = {
        "id": "wf_settings_general",
        "name": "Open Settings → General",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "phone.openApp", "position": {"x": 0, "y": 120}, "params": {"app": "Settings"}},
        ],
        "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
    }
    base.update(over)
    return base

def test_valid_settings_graph():
    out = validate_workflow(_wf())
    assert out["id"] == "wf_settings_general"

def test_rejects_bad_id():
    with pytest.raises(SchemaError) as e:
        validate_workflow(_wf(id="Settings"))
    assert e.value.path == "id"

def test_rejects_unknown_type():
    doc = _wf()
    doc["nodes"][1]["type"] = "n8n.http"
    with pytest.raises(SchemaError) as e:
        validate_workflow(doc)
    assert "n2" in e.value.path

def test_rejects_extra_param():
    doc = _wf()
    doc["nodes"][1]["params"]["secret"] = "hunter2"
    with pytest.raises(SchemaError):
        validate_workflow(doc)

def test_rejects_two_triggers():
    doc = _wf()
    doc["nodes"].append({"id": "n3", "type": "trigger.chat", "position": {"x": 0, "y": 0}, "params": {}})
    with pytest.raises(SchemaError):
        validate_workflow(doc)

def test_pay_tap_without_confirm_rejected():
    doc = {
        "id": "wf_pay",
        "name": "pay",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "phone.tap", "position": {"x": 0, "y": 1}, "params": {"label": "Pagar"}},
        ],
        "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
    }
    with pytest.raises(SchemaError) as e:
        validate_workflow(doc)
    assert "confirm" in str(e.value).lower() or "n2" in e.value.path

def test_pay_tap_with_confirm_ok():
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
    validate_workflow(doc)

def test_if_requires_true_false_handles():
    doc = {
        "id": "wf_if",
        "name": "if",
        "version": 1,
        "nodes": [
            {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
            {"id": "n2", "type": "flow.if", "position": {"x": 0, "y": 1}, "params": {"match": "General"}},
            {"id": "n3", "type": "flow.stop", "position": {"x": 0, "y": 2}, "params": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n2", "target": "n3"},
        ],
    }
    with pytest.raises(SchemaError):
        validate_workflow(doc)
