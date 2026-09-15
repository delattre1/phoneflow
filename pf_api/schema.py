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
            raise SchemaError(f"nodes[id={nid}].type", f"unknown type {ntype}")
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
