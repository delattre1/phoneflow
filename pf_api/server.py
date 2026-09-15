from __future__ import annotations

import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PhoneFlowHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _path(self) -> str:
        return urlparse(self.path).path

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        netloc = urlparse(origin).netloc
        if netloc != host:
            self._send(403, {"code": "forbidden", "message": "cross-origin denied"})
            return False
        return True

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
            run_id = m.group(1)
            if load_run(self._home(), run_id) is None:
                self._send(404, {"code": "not_found", "message": "run not found"})
                return
            events = load_events(self._home(), run_id)
            chunks = "".join(f"data: {json.dumps(ev)}\n\n" for ev in events)
            self._send(200, chunks, "text/event-stream")
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
            self._send(200, run)
            return
        self._send(404, {"code": "not_found", "message": "no such route"})

    def do_PUT(self):
        if not self._origin_ok():
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

    def do_DELETE(self):
        if not self._origin_ok():
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
        if not wf_id:
            self._send(400, {"code": "validation", "path": "workflowId", "message": "workflowId required"})
            return
        doc = load_workflow(self._home(), wf_id)
        if doc is None:
            self._send(404, {"code": "not_found", "message": "workflow not found"})
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
        run = itp.start(doc)
        run["id"] = run_id
        while run["status"] == "running":
            run = itp.tick()
        self._persist(itp)
        self.server.interpreters[run_id] = itp
        self._send(200, {"runId": run_id})

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
        while run["status"] == "running":
            run = itp.tick()
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
