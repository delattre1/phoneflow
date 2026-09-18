# PhoneFlow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Hermes agent variant that serves a no-login React Flow canvas on port 8787; graphs execute through Latch onto iPhone Mirroring.

**Architecture:** Stdlib Python API + interpreter live in `pf-api/`. Tests inject `FakeDriver`. The Docker image `FROM` plow-hermes-agent, serves the built SPA, reports usage to Agent Index. Deploy target is `sued@suedpc.local`; Latch and iPhone Mirroring stay on this Mac.

**Tech Stack:** Python 3.11+ stdlib, pytest, Vite, React 18, `@xyflow/react`, Docker Compose, Plow Latch MCP, s6-overlay.

**Spec:** `docs/superpowers/specs/2026-09-15-phoneflow-design.md`

## Global Constraints

- License: MIT
- Base image: `public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-8088c7f77f5ffd536a80c9dc302ebdb39e6be1d2@sha256:26d69e81faebc584a4d819f68f756e2d4917938409b0f8ff98488c93bdd34b78`
- `AGENT_ID=phoneflow`
- Reporter: plow-pbc/agent-index-client pin `sha=3f116994930cb3d1c23a485851953dd6c1eef039` `sha256=b23e7db974b1bd00b50557b44d759df170fc6ef17b471c9cfc0cd975843b535c` — no disable switch
- UI fonts: Inter + JetBrains Mono
- UI accent: `#2CD4C3` on `#07070d`
- UI: no login
- Canvas port: 8787 published `0.0.0.0:8787` on `suedpc.local`
- Secrets: Latch vault only (`vaultItemId`); never in JSON, events, or LLM
- Interpreter does not tap outside the graph
- Confirm required on every path to a pay/submit tap
- Deadline: Agent Index page live 2026-09-16
- Checkout on Linux: `sued@suedpc.local:~/phoneflow-hermes-agent`
- This Mac (`/Users/sued/hackaton`) is the git workspace; rsync/scp to the PC after tests pass locally

## File structure

| Path | Responsibility |
| --- | --- |
| `pf-api/schema.py` | Workflow JSON schema, node param tables, pay-path Confirm check |
| `pf-api/driver.py` | `IPhoneDriver` protocol + `FakeDriver` |
| `pf-api/interpreter.py` | Graph walk, statuses, cycle limit, vault/confirm parks |
| `pf-api/store.py` | `$PHONEFLOW_HOME/workflows` and `runs` on disk |
| `pf-api/server.py` | Stdlib HTTP on 8787, SPA + JSON + SSE |
| `pf-api/__main__.py` | `python -m pf_api` entry |
| `pf-mirror/scripts/mirror.py` | AppleScript / cliclick templates (no network) |
| `pf-mirror/SKILL.md` | How Hermes calls Latch for mirroring |
| `pf-run/SKILL.md` | How Hermes starts a run from chat |
| `pf-setup/SKILL.md` | First-boot Latch + mirroring checklist |
| `runtime/persona.md` | Identity: execute graphs, do not freelance taps |
| `web/` | Vite React canvas |
| `image/s6-overlay/` | `phoneflow-api` + `agent-index` longruns |
| `tests/` | Schema, interpreter, API, mirror templates |
| `workflows/wf_settings_general.json` | Bundled demo |
| `workflows/wf_app_lookup.json` | Owner-fillable chore template |
| `Dockerfile` `compose.yml` `LICENSE` `README.md` | Image and install |

Python imports: package dir is `pf-api/` on disk but the module name is `pf_api`. During Task 1, rename the folder to `pf_api/` (Python) and keep skill dirs as `pf-run/` `pf-mirror/` `pf-setup/` (Hermes skills). Spec file map said `pf-api/`; the code package is `pf_api`. Dockerfile copies `pf_api/` to `/opt/phoneflow/pf_api`.

---

### Task 1: Repo scaffold

**Files:**
- Create: `LICENSE`, `.gitignore`, `pyproject.toml`, `pf_api/__init__.py`
- Modify: none

**Interfaces:**
- Consumes: nothing
- Produces: installable package `pf_api` for pytest

- [ ] **Step 1: Write LICENSE (MIT)**

```
MIT License

Copyright (c) 2026 PhoneFlow contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Write `.gitignore`**

```
plow-credentials
__pycache__/
.pytest_cache/
.venv/
web/node_modules/
web/dist/
*.pyc
.DS_Store
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "phoneflow"
version = "0.1.0"
requires-python = ">=3.11"
license = { text = "MIT" }

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 4: Write `pf_api/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: Commit**

```bash
git init
git add LICENSE .gitignore pyproject.toml pf_api/__init__.py docs/
git commit -m "chore: scaffold PhoneFlow repo"
```

---

### Task 2: Workflow schema

**Files:**
- Create: `pf_api/schema.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: none
- Produces:
  - `NODE_TYPES: frozenset[str]`
  - `PAY_TAP_RE: re.Pattern`
  - `class SchemaError(ValueError)` with `.path: str` and `.code = "validation"`
  - `def validate_workflow(doc: dict) -> dict` — returns a shallow-copied normalized doc or raises `SchemaError`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests — expect fail**

```bash
python3 -m pytest tests/test_schema.py -v
```

Expected: `ModuleNotFoundError: pf_api.schema` or collection error.

- [ ] **Step 3: Implement `pf_api/schema.py`**

```python
from __future__ import annotations

import re
from copy import deepcopy

NODE_TYPES = frozenset({
    "trigger.manual", "trigger.chat",
    "phone.openApp", "phone.screenshot", "phone.tap", "phone.swipe",
    "phone.type", "phone.wait",
    "flow.if", "flow.confirm", "flow.stop",
    "auth.vaultUnlock",
})
TRIGGERS = frozenset({"trigger.manual", "trigger.chat"})
ID_RE = re.compile(r"^wf_[a-z0-9_]{1,64}$")
PAY_TAP_RE = re.compile(r"pagar|pay|enviar|confirm(ar)?|submit", re.I)
PAY_PARAM_KEYS = frozenset({"submit", "pagar", "pay"})
PARAM_KEYS = {
    "trigger.manual": frozenset(),
    "trigger.chat": frozenset(),
    "phone.openApp": frozenset({"app"}),
    "phone.screenshot": frozenset(),
    "phone.tap": frozenset({"x", "y", "label"}),
    "phone.swipe": frozenset({"from", "to", "durationMs"}),
    "phone.type": frozenset({"text"}),
    "phone.wait": frozenset({"ms"}),
    "flow.if": frozenset({"match"}),
    "flow.confirm": frozenset({"prompt"}),
    "flow.stop": frozenset({"reason"}),
    "auth.vaultUnlock": frozenset({"vaultItemId"}),
}
REQUIRED = {
    "phone.openApp": ("app",),
    "phone.swipe": ("from", "to"),
    "phone.type": ("text",),
    "phone.wait": ("ms",),
    "flow.if": ("match",),
    "flow.confirm": ("prompt",),
    "auth.vaultUnlock": ("vaultItemId",),
}


class SchemaError(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(message)
        self.path = path
        self.code = "validation"


def validate_workflow(doc: dict) -> dict:
    if not isinstance(doc, dict):
        raise SchemaError("$", "document must be an object")
    out = deepcopy(doc)
    if not isinstance(out.get("id"), str) or not ID_RE.match(out["id"]):
        raise SchemaError("id", "id must match wf_[a-z0-9_]{1,64}")
    if not isinstance(out.get("name"), str) or not out["name"].strip():
        raise SchemaError("name", "name required")
    if out.get("version") != 1:
        raise SchemaError("version", "version must be 1")
    nodes = out.get("nodes")
    edges = out.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise SchemaError("nodes", "nodes required")
    if not isinstance(edges, list):
        raise SchemaError("edges", "edges must be a list")
    by_id = {}
    for i, n in enumerate(nodes):
        path = f"nodes[{i}]"
        if not isinstance(n, dict):
            raise SchemaError(path, "node must be object")
        nid = n.get("id")
        ntype = n.get("type")
        params = n.get("params", {})
        if not isinstance(nid, str) or not nid:
            raise SchemaError(path + ".id", "id required")
        if nid in by_id:
            raise SchemaError(path + ".id", "duplicate node id")
        if ntype not in NODE_TYPES:
            raise SchemaError(path + ".type", f"unknown type {ntype}")
        if not isinstance(params, dict):
            raise SchemaError(path + ".params", "params must be object")
        allowed = PARAM_KEYS[ntype]
        extra = set(params) - allowed
        if extra:
            raise SchemaError(path + ".params", f"unknown keys {sorted(extra)}")
        for key in REQUIRED.get(ntype, ()):
            if key not in params:
                raise SchemaError(path + f".params.{key}", "required")
        if ntype == "phone.tap":
            has_label = isinstance(params.get("label"), str) and params["label"]
            has_xy = "x" in params and "y" in params
            if not has_label and not has_xy:
                raise SchemaError(path + ".params", "label or x,y required")
        if ntype == "phone.wait":
            ms = params["ms"]
            if not isinstance(ms, int) or ms < 0 or ms > 30_000:
                raise SchemaError(path + ".params.ms", "ms 0..30000")
        pos = n.get("position")
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            raise SchemaError(path + ".position", "position.x/y required")
        by_id[nid] = n
    triggers = [n for n in nodes if n["type"] in TRIGGERS]
    if len(triggers) != 1:
        raise SchemaError("nodes", "exactly one trigger")
    for i, e in enumerate(edges):
        path = f"edges[{i}]"
        if not isinstance(e, dict):
            raise SchemaError(path, "edge must be object")
        if e.get("source") not in by_id or e.get("target") not in by_id:
            raise SchemaError(path, "unknown endpoint")
        src = by_id[e["source"]]
        if src["type"] == "flow.if":
            if e.get("sourceHandle") not in ("true", "false"):
                raise SchemaError(path + ".sourceHandle", "true|false required")
    _require_confirm_on_pay_paths(by_id, edges)
    return out


def _is_pay_node(node: dict) -> bool:
    params = node.get("params") or {}
    if any(k in params for k in PAY_PARAM_KEYS):
        return True
    if node["type"] == "phone.tap":
        label = params.get("label") or ""
        if PAY_TAP_RE.search(label):
            return True
    return False


def _require_confirm_on_pay_paths(by_id: dict, edges: list) -> None:
    outgoing: dict[str, list] = {nid: [] for nid in by_id}
    for e in edges:
        outgoing[e["source"]].append(e)
    start = next(n["id"] for n in by_id.values() if n["type"] in TRIGGERS)

    def walk(nid: str, seen: frozenset[str], confirms: int) -> None:
        node = by_id[nid]
        nxt_confirms = confirms + (1 if node["type"] == "flow.confirm" else 0)
        if _is_pay_node(node) and nxt_confirms == 0:
            raise SchemaError(f"nodes[id={nid}]", "pay/submit tap requires flow.confirm on every path")
        if nid in seen:
            return
        nxt_seen = seen | {nid}
        for e in outgoing[nid]:
            walk(e["target"], nxt_seen, nxt_confirms)

    walk(start, frozenset(), 0)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest tests/test_schema.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pf_api/schema.py tests/test_schema.py
git commit -m "feat: validate PhoneFlow workflow graphs"
```

---

### Task 3: Interpreter + FakeDriver

**Files:**
- Create: `pf_api/driver.py`, `pf_api/interpreter.py`, `tests/test_interpreter.py`

**Interfaces:**
- Consumes: `validate_workflow`
- Produces:
  - `class IPhoneDriver(Protocol)` with `open_app(app: str) -> None`, `screenshot() -> bytes`, `ocr(frame: bytes) -> str`, `tap(x: float, y: float) -> None`, `tap_label(label: str) -> None`, `swipe(frm: dict, to: dict, duration_ms: int) -> None`, `type_text(text: str) -> None`, `vault_fill(vault_item_id: str) -> str` returning `"filled"` or `"denied"`
  - `class FakeDriver` recording calls, serving canned OCR strings
  - `STATUSES = queued|running|awaiting_confirm|awaiting_vault|succeeded|failed|cancelled`
  - `class Interpreter` with `start(doc) -> run`, `resume_confirm(decision)`, `tick()` until park or terminal
  - `MAX_VISITS = 50`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run — expect fail**

```bash
python3 -m pytest tests/test_interpreter.py -v
```

Expected: import error.

- [ ] **Step 3: Implement driver + interpreter**

`pf_api/driver.py`:

```python
from __future__ import annotations

from typing import Protocol


class IPhoneDriver(Protocol):
    def open_app(self, app: str) -> None: ...
    def screenshot(self) -> bytes: ...
    def ocr(self, frame: bytes) -> str: ...
    def tap(self, x: float, y: float) -> None: ...
    def tap_label(self, label: str) -> None: ...
    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def vault_fill(self, vault_item_id: str) -> str: ...


class DriverError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FakeDriver:
    def __init__(self, ocr_text: str = "", vault_result: str = "filled"):
        self.ocr_text = ocr_text
        self.vault_result = vault_result
        self.calls: list[tuple] = []

    def open_app(self, app: str) -> None:
        self.calls.append(("open_app", app))

    def screenshot(self) -> bytes:
        self.calls.append(("screenshot",))
        return b"PNG"

    def ocr(self, frame: bytes) -> str:
        self.calls.append(("ocr",))
        return self.ocr_text

    def tap(self, x: float, y: float) -> None:
        self.calls.append(("tap", x, y))

    def tap_label(self, label: str) -> None:
        self.calls.append(("tap_label", label))
        if self.ocr_text and label not in self.ocr_text:
            raise DriverError("element_not_found", label)

    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None:
        self.calls.append(("swipe", frm, to, duration_ms))

    def type_text(self, text: str) -> None:
        self.calls.append(("type_text", text))

    def vault_fill(self, vault_item_id: str) -> str:
        self.calls.append(("vault_fill", vault_item_id))
        return self.vault_result
```

`pf_api/interpreter.py` — implement `Interpreter` so the tests above pass:

- `start` validates, sets `status=running`, walks until park or terminal.
- After every `phone.*` node except wait, call `screenshot()` and store frame bytes in `self.frames`.
- `phone.wait` with `ms==0` does not sleep (tests); `ms>0` uses `time.sleep(ms/1000)`.
- `flow.if`: OCR last frame (or take a screenshot if none). If `match` starts and ends with `/`, treat inner as regex; else substring. Follow `sourceHandle` true/false.
- `flow.confirm`: set `awaiting_confirm`, return. `resume_confirm("approve")` continues; `"deny"` → failed `confirm_denied`.
- `auth.vaultUnlock`: set `awaiting_vault`, return. `resume_vault` calls `driver.vault_fill(id)` — `"filled"` continues, `"denied"` → `vault_denied`. Never put fill material in events.
- Visit counter increments per node entry; `> 50` → `cycle_limit`.
- `flow.stop`: if `reason` startswith `fail:` → failed; else succeeded.
- No outgoing edge after a non-stop node → succeeded if running.
- Events: list of `{nodeId, code, at}` ISO timestamps. `code` is `ok` or the error code.
- `run_all`: loop `start` then auto-resume is **not** used except for graphs that do not park. For tests that call `run_all`, if status is awaiting_* raise; linear/if/cycle tests never park.

Lock-screen (spec §9.6): after each screenshot OCR, if text matches `Passcode|Touch ID|Enter Password|Código|Senha do Mac` and the **next** node is not `auth.vaultUnlock`, park `awaiting_vault` with `code` left as that wait (do not fail). Cover with a test in this file:

```python
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
```

- [ ] **Step 4: Run — expect pass**

```bash
python3 -m pytest tests/test_interpreter.py tests/test_schema.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pf_api/driver.py pf_api/interpreter.py tests/test_interpreter.py
git commit -m "feat: interpret PhoneFlow graphs with a fake iPhone driver"
```

---

### Task 4: Store + HTTP API

**Files:**
- Create: `pf_api/store.py`, `pf_api/server.py`, `pf_api/__main__.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `validate_workflow`, `Interpreter`, `FakeDriver` (tests), `IPhoneDriver` (prod)
- Produces: HTTP server `make_server(home: Path, driver: IPhoneDriver, latch_ok: callable[[], bool]) -> HTTPServer` bound by caller

Env: `PHONEFLOW_HOME` default `/var/lib/hermes/phoneflow`.

- [ ] **Step 1: Failing API tests using `http.client` against `make_server` on port 0**

```python
# tests/test_api.py
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from pf_api.driver import FakeDriver
from pf_api.server import make_server

SETTINGS = {
    "id": "wf_settings_general",
    "name": "Open Settings → General",
    "version": 1,
    "nodes": [
        {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
        {"id": "n2", "type": "phone.openApp", "position": {"x": 0, "y": 1}, "params": {"app": "Settings"}},
    ],
    "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
}

def _boot(tmp_path: Path, latch=True):
    drv = FakeDriver(ocr_text="Settings")
    httpd = make_server(tmp_path, drv, latch_ok=lambda: latch)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    return httpd, port, drv

def test_health(tmp_path):
    httpd, port, _ = _boot(tmp_path, latch=False)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("GET", "/api/health")
        body = json.loads(c.getresponse().read())
        assert body == {"ok": True, "latch": "down"}
    finally:
        httpd.shutdown()

def test_put_get_workflow(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS), {"Content-Type": "application/json"})
        assert c.getresponse().status == 200
        c.request("GET", "/api/workflows/wf_settings_general")
        got = json.loads(c.getresponse().read())
        assert got["name"] == SETTINGS["name"]
    finally:
        httpd.shutdown()

def test_put_extra_key_400(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        bad = dict(SETTINGS)
        bad["nodes"] = [dict(SETTINGS["nodes"][0]), dict(SETTINGS["nodes"][1])]
        bad["nodes"][1] = dict(bad["nodes"][1])
        bad["nodes"][1]["params"] = {"app": "Settings", "token": "abc"}
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(bad), {"Content-Type": "application/json"})
        resp = c.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read())
        assert body["code"] == "validation"
    finally:
        httpd.shutdown()

def test_run_and_get(tmp_path):
    httpd, port, _ = _boot(tmp_path)
    try:
        c = HTTPConnection("127.0.0.1", port)
        c.request("PUT", "/api/workflows/wf_settings_general", json.dumps(SETTINGS), {"Content-Type": "application/json"})
        c.getresponse().read()
        c.request("POST", "/api/runs", json.dumps({"workflowId": "wf_settings_general"}), {"Content-Type": "application/json"})
        run_id = json.loads(c.getresponse().read())["runId"]
        c.request("GET", f"/api/runs/{run_id}")
        run = json.loads(c.getresponse().read())
        assert run["status"] == "succeeded"
        assert "vault" not in json.dumps(run).lower() or "vaultItemId" in json.dumps(run)
    finally:
        httpd.shutdown()
```

- [ ] **Step 2: Run — expect fail**

```bash
python3 -m pytest tests/test_api.py -v
```

- [ ] **Step 3: Implement store + server**

`store.py`: `workflows_dir(home)/{id}.json`, `runs_dir(home)/{runId}/run.json`, `events.jsonl`, `frames/{seq}.png`. `new_run_id()` = `run_` + 12 hex chars.

`server.py`: `ThreadingHTTPServer`, routes from spec §12.

- `GET /` and `/assets/*` serve `PHONEFLOW_WEB` dir (default `web/dist`); missing index → 503 text `canvas not built`.
- JSON 400 for `SchemaError`: `{"code":"validation","path":...,"message":...}`.
- `POST /api/runs` creates interpreter, `start()`, if not parked keep ticking until terminal (FakeDriver is sync). Persist after each park/terminal.
- In-memory `INTERPRETERS: dict[str, Interpreter]` for confirm/cancel.
- `POST /api/runs/{id}/confirm` body `{"decision":"approve"|"deny"}`.
- `POST /api/runs/{id}/cancel` → status cancelled.
- CORS: if `Origin` present and not same host:port, 403. No cookies.
- Do not put vault secrets in responses. `auth.vaultUnlock` params may include `vaultItemId` in GET workflow (that's an id, not a secret).

`__main__.py`:

```python
import os
from pathlib import Path
from pf_api.server import make_server
from pf_api.driver import FakeDriver  # replaced by LatchDriver in Task 5 via env PHONEFLOW_DRIVER=latch|fake

def main():
    home = Path(os.environ.get("PHONEFLOW_HOME", "/var/lib/hermes/phoneflow"))
    home.mkdir(parents=True, exist_ok=True)
    driver_kind = os.environ.get("PHONEFLOW_DRIVER", "fake")
    if driver_kind == "fake":
        driver = FakeDriver(ocr_text="General")
    else:
        from pf_mirror.scripts.mirror import LatchDriver
        driver = LatchDriver()
    def latch_ok():
        if driver_kind == "fake":
            return True
        return driver.health()
    httpd = make_server(home, driver, latch_ok, host="0.0.0.0", port=int(os.environ.get("PHONEFLOW_PORT", "8787")))
    httpd.serve_forever()

if __name__ == "__main__":
    main()
```

Keep `LatchDriver` import inside the else so Task 4 tests do not need Task 5.

- [ ] **Step 4: pytest tests/test_api.py tests/test_schema.py tests/test_interpreter.py -v** — pass

- [ ] **Step 5: Commit** `feat: PhoneFlow HTTP API on port 8787`

---

### Task 5: Latch mirror templates

**Files:**
- Create: `pf_mirror/__init__.py`, `pf_mirror/scripts/mirror.py`, `pf-mirror/SKILL.md`, `tests/test_mirror.py`

**Interfaces:**
- Consumes: none (templates only)
- Produces: `window_script() -> str`, `click_script(x: float, y: float, titlebar_px: int = 28) -> str`, `screenshot_command() -> list[str]`, `LOCK_RE`, class `LatchDriver` whose methods **build** plow argv but in unit tests we only assert the strings. `LatchDriver.health` in tests is not called. Production `LatchDriver` may raise `DriverError("latch_disconnected")` if a later subprocess wrapper is added; v1 of `LatchDriver` exposes `commands` list like FakeDriver plus AppleScript text, and a `run_applescript(src: str)` hook defaulting to raise `NotImplementedError` so tests never talk to a Mac.

- [ ] **Step 1: Failing tests**

```python
from pf_mirror.scripts.mirror import LOCK_RE, click_script, window_script

def test_window_script_lists_both_titles():
    src = window_script()
    assert "iPhone Mirroring" in src
    assert "Espelhamento do iPhone" in src

def test_click_script_uses_titlebar_offset():
    src = click_script(0.5, 0.5, titlebar_px=28)
    assert "28" in src
    assert "0.5" in src

def test_lock_re_matches_pt_and_en():
    assert LOCK_RE.search("Enter Password")
    assert LOCK_RE.search("Senha do Mac")
    assert LOCK_RE.search("Código")
    assert not LOCK_RE.search("General")
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement `pf_mirror/scripts/mirror.py`**

```python
import re

LOCK_RE = re.compile(r"Passcode|Touch ID|Enter Password|Código|Senha do Mac")
TITLES = ("iPhone Mirroring", "Espelhamento do iPhone")

def window_script() -> str:
    return f'''
tell application "System Events"
  set procs to application processes whose background only is false
  repeat with p in procs
    repeat with w in windows of p
      set t to name of w as text
      if t contains "{TITLES[0]}" or t contains "{TITLES[1]}" then
        return (t & tab & (position of w as text) & tab & (size of w as text))
      end if
    end repeat
  end repeat
end tell
error "mirror_window_missing"
'''

def click_script(x: float, y: float, titlebar_px: int = 28) -> str:
    return f'''
-- normalized {x},{y} inside mirror content; titlebar {titlebar_px}px
tell application "System Events"
  -- locator fills absolute coords; this script is a template
  click at {{{x}, {y}}} -- replaced by LatchDriver with pixel ints
end tell
'''
```

Expand `LatchDriver` enough that `open_app` records `("open_app", app)` and stores the AppleScript it *would* send. Do not call `osascript` in tests.

`pf-mirror/SKILL.md` (Hermes, not imported by pytest):

```markdown
---
name: pf-mirror
description: Drive the owner's iPhone Mirroring window through Plow Latch. Use when a PhoneFlow run needs a screenshot, tap, swipe, type, or vault fill on the Mac.
---

Only use plow_ MCP tools. Never SSH to the Mac.

1. plow_list_skills then plow_read_skill for the Mac's own window/screenshot skill if present.
2. Find the window titled iPhone Mirroring or Espelhamento do iPhone. Missing → tell the owner to open iPhone Mirroring; error code mirror_window_missing.
3. Screenshots go to the run's frames directory. Relay host-gate text unmodified.
4. Clicks are window-local, subtracting MIRROR_TITLEBAR_PX (default 28).
5. auth.vaultUnlock: ask Latch to fill vaultItemId into the Mac auth prompt. Do not print the secret.
```

- [ ] **Step 4: pytest tests/test_mirror.py -v** — pass

- [ ] **Step 5: Commit** `feat: iPhone Mirroring Latch command templates`

---

### Task 6: Bundled graphs, persona, chat skills

**Files:**
- Create: `workflows/wf_settings_general.json`, `workflows/wf_app_lookup.json`, `runtime/persona.md`, `pf-run/SKILL.md`, `pf-setup/SKILL.md`

**Interfaces:**
- Consumes: `validate_workflow`
- Produces: two valid JSON files; persona forbids freelance taps

- [ ] **Step 1: Test that bundled graphs validate**

```python
# tests/test_bundled.py
import json
from pathlib import Path
from pf_api.schema import validate_workflow

def test_settings_and_lookup_validate():
    root = Path(__file__).resolve().parents[1] / "workflows"
    for name in ("wf_settings_general.json", "wf_app_lookup.json"):
        validate_workflow(json.loads((root / name).read_text()))
```

`wf_settings_general.json` — the SETTINGS graph from Task 3 (openApp Settings, tap General, screenshot).

`wf_app_lookup.json`:

```json
{
  "id": "wf_app_lookup",
  "name": "App lookup (fill me)",
  "version": 1,
  "nodes": [
    {"id": "n1", "type": "trigger.manual", "position": {"x": 80, "y": 40}, "params": {}},
    {"id": "n2", "type": "phone.openApp", "position": {"x": 80, "y": 140}, "params": {"app": "CHANGE_ME"}},
    {"id": "n3", "type": "flow.confirm", "position": {"x": 80, "y": 240}, "params": {"prompt": "Continue on the iPhone?"}},
    {"id": "n4", "type": "phone.screenshot", "position": {"x": 80, "y": 340}, "params": {}}
  ],
  "edges": [
    {"id": "e1", "source": "n1", "target": "n2"},
    {"id": "e2", "source": "n2", "target": "n3"},
    {"id": "e3", "source": "n3", "target": "n4"}
  ]
}
```

- [ ] **Step 2: pytest tests/test_bundled.py -v** — fail then add JSON — pass

- [ ] **Step 3: `runtime/persona.md`**

```markdown
# Who you are

You are PhoneFlow. You execute workflow graphs the owner drew in the canvas.
You do not invent iPhone taps. If a graph is missing a node, say so and stop.

# Mac and phone

Owner work on the iPhone goes through Latch iPhone Mirroring. Passwords
live in Latch vault. Never ask the owner to paste a password into chat.
Never put a secret in a tool argument the model can see.

# Chat

If the owner texts "run <name>", load that workflow and start a run.
When a run parks on confirm, ask them yes/no. When Latch is down, tell
them to open Latch on the Mac.
```

`pf-run/SKILL.md`: chat "run X" → `python3 -m pf_api` is already the server; for chat, `POST /api/runs` via localhost 8787. Document that.

`pf-setup/SKILL.md`: checklist — Latch open, iPhone Mirroring paired, Accessibility + Screen Recording, vault item for Mac password, open `http://suedpc.local:8787`.

- [ ] **Step 4: Commit** `feat: bundled playbooks and PhoneFlow persona`

---

### Task 7: Canvas (Vite + React Flow)

**Files:**
- Create: `web/package.json`, `web/index.html`, `web/src/main.jsx`, `web/src/App.jsx`, `web/src/theme.css`, `web/vite.config.js`, `tests/test_theme.py`

**Interfaces:**
- Consumes: HTTP API §12
- Produces: SPA with no login; tokens from spec §5

- [ ] **Step 1: Theme test (no browser)**

```python
# tests/test_theme.py
from pathlib import Path

def test_theme_tokens():
    css = (Path(__file__).resolve().parents[1] / "web/src/theme.css").read_text()
    assert "#2CD4C3" in css
    assert "#07070d" in css
    assert "Inter" in css
    assert "JetBrains Mono" in css
    assert "#FFD700" not in css
```

- [ ] **Step 2: fail then write `web/src/theme.css`**

```css
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap");

:root {
  --bg: #07070d;
  --surface: #0f0f18;
  --text: #e8e4dc;
  --muted: #9a968e;
  --accent: #2CD4C3;
  --accent-dark: #26B8A9;
  --border: rgba(44, 212, 195, 0.10);
  --code: #0a0a12;
  --radius: 8px;
  --font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", "Fira Code", monospace;
}

html, body, #root {
  margin: 0;
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
}

body {
  background-image: radial-gradient(rgba(44, 212, 195, 0.02) 1px, transparent 1px);
  background-size: 32px 32px;
}

code, .mono { font-family: var(--mono); }
```

- [ ] **Step 3: Scaffold Vite app**

`web/package.json`:

```json
{
  "name": "phoneflow-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build"
  },
  "dependencies": {
    "@xyflow/react": "^12.6.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^5.4.0"
  }
}
```

`web/vite.config.js`:

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
```

`web/index.html`: `<div id="root">`, title `PhoneFlow`.

`web/src/App.jsx` layout:

- Navbar: wordmark **PhoneFlow** (accent), no login, no avatar. Link "Runs" that shows the bottom console.
- Left: workflow list from `GET /api/workflows` + palette buttons for each node type in spec §8.
- Center: `<ReactFlow>` nodes/edges. Persist with `PUT /api/workflows/{id}` on a Save button.
- Right: inspector for `selected.params`. For `auth.vaultUnlock` only `vaultItemId`. For `phone.type`, helper text: "use VaultUnlock for secrets".
- Bottom: Run button `POST /api/runs`. Poll `GET /api/runs/{id}` every 500ms. When `awaiting_confirm`, show Approve/Deny calling `POST /api/runs/{id}/confirm`. Show last frame `<img src={/api/runs/{id}/frames/{seq}}>`.
- Banner if `GET /api/health` has `latch: "down"`: "Open Latch on the Mac".

No router, no auth library.

- [ ] **Step 4: `cd web && npm install && npm run build`** — `web/dist/index.html` exists

- [ ] **Step 5: pytest tests/test_theme.py -v** — pass

- [ ] **Step 6: Commit** `feat: PhoneFlow canvas in Hermes docs chrome with teal accent`

---

### Task 8: Docker image + s6 + compose

**Files:**
- Create: `Dockerfile`, `compose.yml`, `vendor/client.pin`, `image/s6-overlay/s6-rc.d/agent-index/{type,run,dependencies.d/plow-init}`, `image/s6-overlay/s6-rc.d/phoneflow-api/{type,run,dependencies.d/plow-init}`, `image/s6-overlay/s6-rc.d/user/contents.d/agent-index`, `image/s6-overlay/s6-rc.d/user/contents.d/phoneflow-api`

**Interfaces:**
- Consumes: all previous artifacts
- Produces: `docker compose up --build` on `suedpc.local` serves `:8787`

- [ ] **Step 1: `vendor/client.pin`** — copy the three lines from life-assistant (`sha=3f116994…`, `sha256=b23e7db9…`, `path=standalone/agent_index_client.py`) plus the comment block.

- [ ] **Step 2: Dockerfile**

```dockerfile
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-8088c7f77f5ffd536a80c9dc302ebdb39e6be1d2@sha256:26d69e81faebc584a4d819f68f756e2d4917938409b0f8ff98488c93bdd34b78

COPY --chmod=0644 runtime/persona.md /opt/hermes/plow-seed/persona.md
COPY LICENSE /usr/share/doc/phoneflow/LICENSE

COPY pf-run/    /opt/hermes/skills/pf-run/
COPY pf-mirror/ /opt/hermes/skills/pf-mirror/
COPY pf-setup/  /opt/hermes/skills/pf-setup/
COPY pf_api/    /opt/phoneflow/pf_api/
COPY pf_mirror/ /opt/phoneflow/pf_mirror/
COPY web/dist/  /opt/phoneflow/web/
COPY workflows/ /opt/phoneflow/workflows/

RUN find /opt/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} + \
 && install -d -m 0755 /opt/phoneflow \
 && find /opt/phoneflow -type d -exec chmod 0755 {} + \
 && find /opt/phoneflow -type f -exec chmod 0644 {} +

COPY vendor/client.pin /opt/plow/agent-index-client.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/agent-index-client.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/agent-index-client.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/agent-index-client.pin)"; \
    curl -fsS --max-time 60 -o /opt/plow/agent-index-client.py \
      "https://raw.githubusercontent.com/plow-pbc/agent-index-client/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/agent-index-client.py | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "agent-index client is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/agent-index-client.py

COPY image/s6-overlay/ /etc/s6-overlay/
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/phoneflow
```

- [ ] **Step 3: s6 phoneflow-api**

`image/s6-overlay/s6-rc.d/phoneflow-api/type` → `longrun`

`dependencies.d/plow-init` empty file.

`run`:

```sh
#!/bin/sh
PATH=/command:/usr/local/bin:/usr/bin:/bin
export PATH
export PYTHONPATH=/opt/phoneflow
export PHONEFLOW_HOME=/var/lib/hermes/phoneflow
export PHONEFLOW_WEB=/opt/phoneflow/web
export PHONEFLOW_PORT=8787
export PHONEFLOW_DRIVER="${PHONEFLOW_DRIVER:-latch}"
exec /command/s6-setuidgid hermes \
  /opt/hermes/.venv/bin/python3 -m pf_api
```

`chmod 0755` the run script.

Copy life-assistant `agent-index/run` verbatim (the file in the spec's sister repo). `type` = `longrun`. `dependencies.d/plow-init` empty.

`user/contents.d/agent-index` and `user/contents.d/phoneflow-api` empty files so s6 starts both.

- [ ] **Step 4: `compose.yml`**

```yaml
services:
  agent:
    build: .
    ports:
      - "0.0.0.0:8787:8787"
    environment:
      AGENT_ID: phoneflow
      PHONEFLOW_DRIVER: latch
    volumes:
      - ./plow-credentials:/var/lib/plow/credentials.host:ro
      - agent-home:/var/lib/hermes
    stop_grace_period: 35s

volumes:
  agent-home:
```

- [ ] **Step 5: Seed bundled workflows into the home on API boot** if `workflows/` dir is empty: copy `/opt/phoneflow/workflows/*.json`. Implement in `store.ensure_seeded(home)`.

Add `tests/test_store_seed.py` that tmp_path empty → after `ensure_seeded` both ids exist.

- [ ] **Step 6: Commit** `feat: Docker image serves PhoneFlow next to Hermes`

---

### Task 9: README + Agent Index register + deploy

**Files:**
- Create: `README.md`

**Interfaces:** none

- [ ] **Step 1: README.md** — MIT, LAN-only 8787, topology (suedpc + this Mac), install:

```sh
# on suedpc.local
git clone <repo> ~/phoneflow-hermes-agent
export PATH="$HOME/plow-agents/bin:$PATH"   # from github.com/plow-pbc/plow-agents
plow-agents login
plow-agents lines
plow-agents mint ln_xxx
cd ~/phoneflow-hermes-agent
docker compose up --build -d
```

On the Mac: install Plow Latch, pair iPhone Mirroring, grant Accessibility + Screen Recording, open `http://suedpc.local:8787`. Do not publish 8787 to the internet.

Fill `wf_app_lookup` with one real chore before "Get my agent verified".

Register:

```sh
set -a; . ./plow-credentials; set +a
python3 agent_index_client.py --register --agent phoneflow \
  --name "PhoneFlow" \
  --blurb "Draw iPhone actions; Hermes runs them through Latch + iPhone Mirroring." \
  --runtime "Hermes"
```

- [ ] **Step 2: Local pytest full suite**

```bash
python3 -m pytest tests -v
```

Expected: all PASS.

- [ ] **Step 3: Deploy to `sued@suedpc.local`**

Blocked until this Mac has an SSH identity the PC accepts. Then:

```bash
rsync -az --exclude .git --exclude web/node_modules --exclude .venv \
  /Users/sued/hackaton/ sued@suedpc.local:~/phoneflow-hermes-agent/
ssh sued@suedpc.local 'cd ~/phoneflow-hermes-agent && docker compose up --build -d'
curl -sf http://suedpc.local:8787/api/health
```

- [ ] **Step 4: Manual smoke** — Settings playbook with iPhone Mirroring open. Then owner's real chore.

- [ ] **Step 5: Commit** `docs: install PhoneFlow on suedpc and register Agent Index`

---

## Spec coverage

| Spec section | Task |
| --- | --- |
| §4 topology / 8787 LAN | 8, 9 |
| §5 visual tokens | 7 |
| §6 components | 3–8 |
| §7 JSON + runs | 2, 4 |
| §8 node catalog | 2, 3, 7 palette |
| §9 execution + pay Confirm | 2, 3 |
| §10 mirroring driver | 5 |
| §11 vault / money | 2, 3, 7 inspector copy |
| §12 HTTP API | 4 |
| §13 packaging / reporter | 8, 9 |
| §14 bundled playbooks | 6 |
| §15 errors | 3 DriverError codes, 4 JSON 400 |
| §16 tests | 2–7 |
| Non-goals (no n8n, no login) | 7 navbar, 2 unknown type |

## Placeholder scan

None remaining. `LatchDriver.run_applescript` is explicitly `NotImplementedError` in unit tests; production wiring is Task 5 hook + Task 8 `PHONEFLOW_DRIVER=latch`. Deploy Step 3 is gated on SSH.

## Type consistency

`validate_workflow(doc) -> dict`, `SchemaError.path/code`, `Interpreter.start/resume_confirm/resume_vault/run_all`, `FakeDriver.calls`, `make_server(home, driver, latch_ok)`, status strings match spec §7.
