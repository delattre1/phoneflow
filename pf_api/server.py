"""PhoneFlow stdlib HTTP API.

Same-origin enforcement: any request carrying an Origin header is accepted
only when the Origin netloc equals the PHONEFLOW_ORIGIN env (when set) or the
request's own Host header. Residual DNS-rebinding exposure: when
PHONEFLOW_ORIGIN is unset, an attacker who can also control the Host header
(possible through some transparent proxies / rebinding setups) still passes;
deployers on hostile LANs SHOULD pin PHONEFLOW_ORIGIN.
"""

from __future__ import annotations

import json
import threading
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pf_api import doctor, settings
from pf_api.interpreter import Interpreter
from pf_api.schema import SchemaError, validate_workflow
from pf_api.store import (
    delete_workflow,
    ensure_seeded,
    frame_path,
    load_events,
    load_run,
    load_workflow,
    list_workflows,
    new_run_id,
    save_run,
    save_workflow,
)

_WF_ID = re.compile(r"^/api/workflows/([^/]+)$")
_RUN_ID = re.compile(r"^/api/runs/([^/]+)$")
_RUN_EVENTS = re.compile(r"^/api/runs/([^/]+)/events$")
_RUN_FRAME = re.compile(r"^/api/runs/([^/]+)/frames/([^/]+)$")
_RUN_CONFIRM = re.compile(r"^/api/runs/([^/]+)/confirm$")
_RUN_CANCEL = re.compile(r"^/api/runs/([^/]+)/cancel$")
_HINT = re.compile(r"^/api/hints/([^/]+)$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PhoneFlowHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        netloc = urlparse(origin).netloc
        configured = os.environ.get("PHONEFLOW_ORIGIN")
        if configured:
            # Pinned deployment: the configured origin is the only allowed one,
            # so a DNS-rebinding Host/Origin pair cannot ride the Host fallback.
            allowed = {urlparse(configured if "//" in configured else f"//{configured}", scheme="http").netloc}
        else:
            # Residual DNS-rebinding exposure (see module docstring): without a
            # pin, a rebinding attacker who controls both Host and Origin passes.
            allowed = {self.headers.get("Host", "")}
        if netloc not in allowed or not netloc:
            self._send(403, {"code": "forbidden", "message": "cross-origin denied"})
            return False
        return True

    def _path(self) -> str:
        return urlparse(self.path).path

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode())

    def _send(self, status: int, body, content_type: str = "application/json") -> None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif isinstance(body, str):
            data = body.encode()
        else:
            data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _schema_error(self, exc: SchemaError) -> None:
        self._send(400, {"code": exc.code, "path": exc.path, "message": str(exc)})

    def _home(self) -> Path:
        return self.server.home

    def _persist(self, itp: Interpreter) -> None:
        save_run(self._home(), itp.run, itp.events, itp.frames)

    def _web_root(self) -> Path:
        return Path(os.environ.get("PHONEFLOW_WEB", "web/dist"))

    def _serve_index(self) -> None:
        index = self._web_root() / "index.html"
        if not index.is_file():
            self._send(503, "canvas not built", "text/plain; charset=utf-8")
            return
        self._send(200, index.read_bytes(), "text/html; charset=utf-8")

    def _serve_asset(self, path: str) -> None:
        root = self._web_root().resolve()
        target = (root / path.lstrip("/")).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._send(404, {"code": "not_found", "message": "missing"})
            return
        if not target.is_file():
            self._send(404, {"code": "not_found", "message": "missing"})
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        if not self._origin_ok():
            return
        path = self._path()
        if path == "/":
            self._serve_index()
            return
        if path.startswith("/assets/"):
            self._serve_asset(path)
            return
        if path == "/api/health":
            latch = "up" if self.server.latch_ok() else "down"
            self._send(200, {"ok": True, "latch": latch})
            return
        if path == "/api/doctor":
            self._send(200, doctor.run(self.server.driver))
            return
        if path == "/api/config":
            self._send(200, settings.public_config(self._home()))
            return
        if path == "/api/latch/tools":
            # Which relay tools this Latch offers (names only): what the driver
            # can and cannot delegate to it, vault fill included.
            drv = self.server.driver
            try:
                if hasattr(drv, "_ensure_session"):
                    drv._ensure_session()
                self._send(200, {"tools": sorted(getattr(drv, "_tools", [])),
                                 "docs": getattr(drv, "_tool_docs", {})})
            except Exception as exc:  # noqa: BLE001
                self._send(502, {"code": "latch_disconnected", "message": str(exc)[:300]})
            return
        if path == "/api/models":
            probe = "probe=1" in (urlparse(self.path).query or "")
            try:
                self._send(200, settings.model_report(probe=probe))
            except Exception as exc:  # noqa: BLE001
                self._send(502, {"code": "llm_unreachable", "message": str(exc)[:300]})
            return
        if path == "/api/hints":
            self._send(200, settings.list_hints())
            return
        m = _HINT.match(path)
        if m:
            hint = settings.read_hint(unquote(m.group(1)))
            if hint is None:
                self._send(404, {"code": "not_found", "message": "no hint for that app"})
                return
            self._send(200, hint)
            return
        if path == "/api/workflows":
            self._send(200, list_workflows(self._home()))
            return
        m = _WF_ID.match(path)
        if m:
            doc = load_workflow(self._home(), m.group(1))
            if doc is None:
                self._send(404, {"code": "not_found", "message": "workflow not found"})
                return
            self._send(200, doc)
            return
        m = _RUN_EVENTS.match(path)
        if m:
            self._stream_events(m.group(1))
            return
        m = _RUN_FRAME.match(path)
        if m:
            p = frame_path(self._home(), m.group(1), m.group(2))
            if not p.is_file():
                self._send(404, {"code": "not_found", "message": "frame not found"})
                return
            self._send(200, p.read_bytes(), "image/png")
            return
        m = _RUN_ID.match(path)
        if m:
            run = load_run(self._home(), m.group(1))
            if run is None:
                self._send(404, {"code": "not_found", "message": "run not found"})
                return
            live = self.server.interpreters.get(m.group(1))
            if live is not None and run.get("status") != "running":
                result = self._outcome(live, m.group(1)).get("result")
                if result:
                    run = dict(run, result=result)
            self._send(200, run)
            return
        self._send(404, {"code": "not_found", "message": "no such route"})

    def _stream_events(self, run_id: str) -> None:
        # Real SSE: flush the persisted lines, then poll events.jsonl until the
        # run reaches a terminal status (cheap: file reads, no interpreter hold).
        if load_run(self._home(), run_id) is None:
            self._send(404, {"code": "not_found", "message": "run not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sent = 0
        try:
            while True:
                run = load_run(self._home(), run_id)
                events = load_events(self._home(), run_id)
                for ev in events[sent:]:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                sent = len(events)
                self.wfile.flush()
                if run is None or run.get("status") not in ("running", "queued"):
                    break
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_PUT(self):
        if not self._origin_ok():
            return
        if self._path() == "/api/config" or _HINT.match(self._path()):
            self._put_setting()
            return
        m = _WF_ID.match(self._path())
        if not m:
            self._send(404, {"code": "not_found", "message": "no such route"})
            return
        wf_id = m.group(1)
        try:
            doc = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"code": "validation", "path": "$", "message": "invalid json"})
            return
        if not isinstance(doc, dict):
            self._send(400, {"code": "validation", "path": "$", "message": "document must be an object"})
            return
        if doc.get("id") not in (None, wf_id):
            self._send(400, {"code": "validation", "path": "id", "message": "id must match URL"})
            return
        doc["id"] = wf_id
        try:
            out = validate_workflow(doc)
        except SchemaError as e:
            self._schema_error(e)
            return
        save_workflow(self._home(), out)
        self._send_empty(200)

    def _put_setting(self) -> None:
        """PUT /api/config {llmApiKey?, llmBaseUrl?, agentModel?, blockedApps?}
        and PUT /api/hints/<app> {"text": "..."} — the owner's own settings."""
        try:
            doc = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"code": "validation", "path": "$", "message": "invalid json"})
            return
        if not isinstance(doc, dict):
            self._send(400, {"code": "validation", "path": "$", "message": "body must be an object"})
            return
        m = _HINT.match(self._path())
        try:
            if m:
                out = settings.write_hint(self._home(), unquote(m.group(1)), doc.get("text"))
            else:
                out = settings.save_config(self._home(), doc)
        except ValueError as exc:
            self._send(400, {"code": "validation", "path": "$", "message": str(exc)})
            return
        self._send(200, out)

    def _open_pane(self) -> None:
        """POST /api/doctor/open {"pane": "screenRecording"|"accessibility"|"automation"}
        opens that Privacy pane on the owner's Mac."""
        try:
            pane = (self._read_json() or {}).get("pane")
        except json.JSONDecodeError:
            pane = None
        opener = getattr(self.server.driver, "open_settings", None)
        if opener is None:
            self._send(200, {"opened": False, "message": "this driver has no Mac to open settings on"})
            return
        try:
            opener(pane)
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"code": getattr(exc, "code", "open_failed"), "message": str(exc)[:300]})
            return
        self._send(200, {"opened": True, "pane": pane})

    def do_DELETE(self):
        if not self._origin_ok():
            return
        m = _HINT.match(self._path())
        if m:
            try:
                gone = settings.delete_hint(self._home(), unquote(m.group(1)))
            except ValueError as exc:
                self._send(400, {"code": "validation", "path": "$", "message": str(exc)})
                return
            if not gone:
                self._send(404, {"code": "not_found", "message": "no owner hint for that app"})
                return
            self.send_response(204)
            self.end_headers()
            return
        m = _WF_ID.match(self._path())
        if not m:
            self._send(404, {"code": "not_found", "message": "no such route"})
            return
        if not delete_workflow(self._home(), m.group(1)):
            self._send(404, {"code": "not_found", "message": "workflow not found"})
            return
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        if not self._origin_ok():
            return
        path = self._path()
        if path == "/api/runs":
            self._start_run()
            return
        if path == "/api/doctor/open":
            self._open_pane()
            return
        m = _RUN_CONFIRM.match(path)
        if m:
            self._confirm(m.group(1))
            return
        m = _RUN_CANCEL.match(path)
        if m:
            self._cancel(m.group(1))
            return
        self._send(404, {"code": "not_found", "message": "no such route"})

    def _start_run(self) -> None:
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"code": "validation", "path": "$", "message": "invalid json"})
            return
        wf_id = body.get("workflowId") if isinstance(body, dict) else None
        goal = body.get("goal") if isinstance(body, dict) else None
        if goal:
            # Ad-hoc task: a free-text goal (e.g. sent by the owner over iMessage)
            # runs as a one-node agent.task graph, so any request works without a
            # workflow drawn in the canvas first.
            wf_id = "wf_adhoc"
            steps = body.get("maxSteps")
            params = {"goal": str(goal)}
            if isinstance(steps, int) and steps > 0:
                params["maxSteps"] = steps
            doc = {
                "id": wf_id, "name": "Ad-hoc task", "version": 1,
                "nodes": [
                    {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "params": {}},
                    {"id": "n2", "type": "agent.task", "position": {"x": 0, "y": 1}, "params": params},
                ],
                "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
            }
        elif not wf_id:
            self._send(400, {"code": "validation", "path": "workflowId",
                             "message": "workflowId or goal required"})
            return
        else:
            doc = load_workflow(self._home(), wf_id)
            if doc is None:
                self._send(404, {"code": "not_found", "message": "workflow not found"})
                return
        # One phone, one run. Two runs fight over the same mirror window and the
        # same cursor, and a caller that timed out and retried would publish or
        # send twice. The caller gets the id of the run that holds the phone so
        # it can poll that one instead.
        for busy_id, busy in list(self.server.interpreters.items()):
            if busy.run and busy.run.get("status") in ("running", "awaiting_confirm"):
                self._send(409, {"code": "busy", "runId": busy_id,
                                 "message": "a run is already using the phone; poll it, do not start another"})
                return
        run_id = new_run_id()
        save_run(
            self._home(),
            {
                "id": run_id,
                "workflowId": wf_id,
                "status": "queued",
                "currentNodeId": None,
                "error": None,
                "startedAt": None,
                "finishedAt": None,
            },
            [],
        )
        itp = Interpreter(self.server.driver)
        self.server.interpreters[run_id] = itp
        run = itp.start(doc)
        run["id"] = run_id
        self._persist(itp)  # running state observable while the run executes
        if isinstance(body, dict) and body.get("async") is True:
            # A phone task takes minutes; a chat agent's HTTP tool gives up long
            # before that, reads the dropped call as a failure and retries. So the
            # run goes to a thread and the caller polls GET /api/runs/{id}.
            threading.Thread(target=self._drive, args=(itp, run_id), daemon=True).start()
            self._send(202, {"runId": run_id, "status": "running"})
            return
        self._drive(itp, run_id)
        self._send(200, self._outcome(itp, run_id))

    def _drive(self, itp: Interpreter, run_id: str) -> None:
        """Tick a run to its end, persisting as it goes. Stops if it was cancelled."""
        run = itp.run
        while run["status"] == "running" and self.server.interpreters.get(run_id) is itp:
            run = itp.tick()
            if run["status"] == "running":
                self._persist(itp)
        if self.server.interpreters.get(run_id) is itp:
            self._persist(itp)

    @staticmethod
    def _outcome(itp: Interpreter, run_id: str) -> dict:
        out = {"runId": run_id, "status": itp.run.get("status") if itp.run else None}
        results = getattr(itp, "agent_results", None)
        if results:
            last = results[-1]
            out["result"] = {
                "status": last.get("status"),
                "message": last.get("message"),
                "records": last.get("records"),
            }
        return out

    def _confirm(self, run_id: str) -> None:
        itp = self.server.interpreters.get(run_id)
        if itp is None or itp.run is None:
            self._send(404, {"code": "not_found", "message": "run not found"})
            return
        if itp.run.get("status") != "awaiting_confirm":
            self._send(409, {"code": "conflict", "message": "run is not awaiting confirm"})
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(400, {"code": "validation", "path": "$", "message": "invalid json"})
            return
        decision = body.get("decision") if isinstance(body, dict) else None
        if decision not in ("approve", "deny"):
            self._send(400, {"code": "validation", "path": "decision", "message": "approve or deny"})
            return
        run = itp.resume_confirm(decision)
        self._persist(itp)
        while run["status"] == "running":
            run = itp.tick()
            if run["status"] == "running":
                self._persist(itp)
        self._persist(itp)
        self._send(200, run)

    def _cancel(self, run_id: str) -> None:
        itp = self.server.interpreters.pop(run_id, None)
        run = itp.run if itp is not None else load_run(self._home(), run_id)
        if run is None:
            self._send(404, {"code": "not_found", "message": "run not found"})
            return
        run["status"] = "cancelled"
        run["finishedAt"] = _now()
        events = itp.events if itp is not None else load_events(self._home(), run_id)
        frames = itp.frames if itp is not None else None
        save_run(self._home(), run, events, frames)
        self._send_empty(200)


def make_server(home, driver, latch_ok, host="127.0.0.1", port=0):
    home = Path(home)
    # settings (owner hints, config.json) resolve against the same home the
    # server stores runs in, whatever the environment said before.
    os.environ["PHONEFLOW_HOME"] = str(home)
    ensure_seeded(home)

    class BoundServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = BoundServer((host, port), PhoneFlowHandler)
    httpd.home = home
    httpd.driver = driver
    httpd.latch_ok = latch_ok
    httpd.interpreters = {}
    return httpd
