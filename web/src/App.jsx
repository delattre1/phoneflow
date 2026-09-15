import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

const NODE_CATALOG = [
  "trigger.manual",
  "trigger.chat",
  "phone.openApp",
  "phone.screenshot",
  "phone.tap",
  "phone.swipe",
  "phone.type",
  "phone.wait",
  "flow.if",
  "auth.vaultUnlock",
  "flow.confirm",
  "flow.stop",
];

const PARAM_KEYS = {
  "trigger.manual": [],
  "trigger.chat": [],
  "phone.openApp": ["app"],
  "phone.screenshot": [],
  "phone.tap": ["x", "y", "label"],
  "phone.swipe": ["from", "to", "durationMs"],
  "phone.type": ["text"],
  "phone.wait": ["ms"],
  "flow.if": ["match"],
  "flow.confirm": ["prompt"],
  "flow.stop": ["reason"],
  "auth.vaultUnlock": ["vaultItemId"],
};

const DEFAULTS = {
  "trigger.manual": {},
  "trigger.chat": {},
  "phone.openApp": { app: "Settings" },
  "phone.screenshot": {},
  "phone.tap": { label: "item" },
  "phone.swipe": { from: { x: 0.5, y: 0.8 }, to: { x: 0.5, y: 0.2 } },
  "phone.type": { text: "" },
  "phone.wait": { ms: 500 },
  "flow.if": { match: "" },
  "flow.confirm": { prompt: "Continue?" },
  "flow.stop": {},
  "auth.vaultUnlock": { vaultItemId: "" },
};

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

function PhoneNode({ data, selected }) {
  const isIf = data.pfType === "flow.if";
  return (
    <div className={`pf-node${selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <div className="pf-node-type">{data.pfType}</div>
      {isIf ? (
        <>
          <div className="pf-handles">
            <span>true</span>
            <span>false</span>
          </div>
          <Handle type="source" position={Position.Bottom} id="true" style={{ left: "25%" }} />
          <Handle type="source" position={Position.Bottom} id="false" style={{ left: "75%" }} />
        </>
      ) : (
        <Handle type="source" position={Position.Bottom} />
      )}
    </div>
  );
}

const nodeTypes = { phone: PhoneNode };

function toRfNodes(doc) {
  return (doc.nodes || []).map((n) => ({
    id: n.id,
    type: "phone",
    position: n.position,
    data: { pfType: n.type, params: { ...(n.params || {}) } },
  }));
}

function toRfEdges(doc) {
  return (doc.edges || []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle,
    targetHandle: e.targetHandle,
  }));
}

function cleanParams(pfType, params) {
  const allowed = new Set(PARAM_KEYS[pfType] || []);
  const out = {};
  for (const [k, v] of Object.entries(params || {})) {
    if (!allowed.has(k)) continue;
    if (v === "" || v === undefined || v === null) continue;
    out[k] = v;
  }
  return out;
}

function toDoc(id, name, nodes, edges) {
  return {
    id,
    name,
    version: 1,
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.pfType,
      position: n.position,
      params: cleanParams(n.data.pfType, n.data.params),
    })),
    edges: edges.map((e) => {
      const edge = { id: e.id, source: e.source, target: e.target };
      if (e.sourceHandle) edge.sourceHandle = e.sourceHandle;
      if (e.targetHandle) edge.targetHandle = e.targetHandle;
      return edge;
    }),
  };
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers || {}),
    },
  });
  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const msg = body && body.message ? body.message : res.statusText;
    throw new Error(msg);
  }
  return body;
}

function errorText(err) {
  if (!err) return "";
  if (typeof err === "string") return err;
  return err.code || "";
}

function Field({ label, value, onChange, type = "text" }) {
  return (
    <label className="field">
      {label}
      <input
        type={type}
        value={value ?? ""}
        onChange={(e) => onChange(type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
      />
    </label>
  );
}

export default function App() {
  const [workflows, setWorkflows] = useState([]);
  const [wfId, setWfId] = useState(null);
  const [wfName, setWfName] = useState("Untitled");
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [latchDown, setLatchDown] = useState(false);
  const [runId, setRunId] = useState(null);
  const [run, setRun] = useState(null);
  const [frameSeq, setFrameSeq] = useState(-1);
  const [error, setError] = useState("");
  const [rf, setRf] = useState(null);

  const selected = useMemo(
    () => nodes.find((n) => n.id === selectedId) || null,
    [nodes, selectedId],
  );

  const refreshList = useCallback(async () => {
    try {
      const list = await api("/api/workflows");
      setWorkflows(Array.isArray(list) ? list : []);
    } catch {
      setWorkflows([]);
    }
  }, []);

  useEffect(() => {
    refreshList();
    api("/api/health")
      .then((h) => setLatchDown(h && h.latch === "down"))
      .catch(() => setLatchDown(false));
  }, [refreshList]);

  useEffect(() => {
    if (!runId) return undefined;
    let stop = false;
    let timer;
    async function tick() {
      try {
        const next = await api(`/api/runs/${runId}`);
        if (stop) return;
        setRun(next);
        if (TERMINAL.has(next.status)) return;
      } catch (e) {
        if (!stop) setError(e.message);
      }
      if (!stop) timer = setTimeout(tick, 500);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [runId]);

  useEffect(() => {
    if (!runId) {
      setFrameSeq(-1);
      return undefined;
    }
    let cancelled = false;
    async function probe() {
      let seq = 0;
      for (;;) {
        const res = await fetch(`/api/runs/${runId}/frames/${seq}`);
        if (!res.ok) break;
        seq += 1;
        if (cancelled) return;
      }
      if (!cancelled && seq > 0) setFrameSeq(seq - 1);
    }
    probe();
    return () => {
      cancelled = true;
    };
  }, [runId, run?.status, run?.currentNodeId]);

  const loadWorkflow = async (id) => {
    setError("");
    const doc = await api(`/api/workflows/${id}`);
    setWfId(doc.id);
    setWfName(doc.name);
    setNodes(toRfNodes(doc));
    setEdges(toRfEdges(doc));
    setSelectedId(null);
    requestAnimationFrame(() => rf?.fitView?.({ padding: 0.2 }));
  };

  const onConnect = useCallback((c) => setEdges((eds) => addEdge(c, eds)), [setEdges]);

  const addNode = (pfType) => {
    const id = `n_${Date.now().toString(36)}`;
    const y = 40 + nodes.length * 90;
    setNodes((nds) => [
      ...nds,
      {
        id,
        type: "phone",
        position: { x: 80, y },
        data: { pfType, params: { ...(DEFAULTS[pfType] || {}) } },
      },
    ]);
    setSelectedId(id);
  };

  const newWorkflow = () => {
    const id = `wf_${Date.now().toString(36)}`;
    setWfId(id);
    setWfName("Untitled");
    setNodes([
      {
        id: "n1",
        type: "phone",
        position: { x: 80, y: 40 },
        data: { pfType: "trigger.manual", params: {} },
      },
    ]);
    setEdges([]);
    setSelectedId("n1");
    setRunId(null);
    setRun(null);
    setError("");
  };

  const patchParams = (patch) => {
    if (!selected) return;
    setNodes((nds) =>
      nds.map((n) =>
        n.id === selected.id
          ? { ...n, data: { ...n.data, params: { ...n.data.params, ...patch } } }
          : n,
      ),
    );
  };

  const save = async () => {
    if (!wfId) throw new Error("no workflow");
    const doc = toDoc(wfId, wfName, nodes, edges);
    await api(`/api/workflows/${wfId}`, { method: "PUT", body: JSON.stringify(doc) });
    await refreshList();
  };

  const onSave = async () => {
    setError("");
    try {
      await save();
    } catch (e) {
      setError(e.message);
    }
  };

  const onRun = async () => {
    setError("");
    try {
      await save();
      const out = await api("/api/runs", {
        method: "POST",
        body: JSON.stringify({ workflowId: wfId }),
      });
      setRunId(out.runId);
      setRun({ id: out.runId, status: "queued" });
      setFrameSeq(-1);
    } catch (e) {
      setError(e.message);
    }
  };

  const onConfirm = async (decision) => {
    setError("");
    try {
      const next = await api(`/api/runs/${runId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ decision }),
      });
      setRun(next);
    } catch (e) {
      setError(e.message);
    }
  };

  const showConsole = () => {
    document.getElementById("run-console")?.scrollIntoView({ behavior: "smooth" });
  };

  const confirmPrompt = useMemo(() => {
    if (!run || run.status !== "awaiting_confirm") return "";
    const n = nodes.find((x) => x.id === run.currentNodeId);
    return n?.data?.params?.prompt || "Confirm this step?";
  }, [run, nodes]);

  const params = selected?.data?.params || {};
  const pfType = selected?.data?.pfType;

  return (
    <div className="app">
      <nav className="navbar">
        <span className="wordmark">PhoneFlow</span>
        <button className="nav-link" type="button" onClick={showConsole}>
          Runs
        </button>
      </nav>
      <div className={latchDown ? "banner" : "banner is-idle"}>Open Latch on the Mac</div>
      <div className="body">
        <aside className="rail">
          <div className="toolbar">
            <button className="btn" type="button" onClick={newWorkflow}>
              New
            </button>
            <button className="btn primary" type="button" onClick={onSave} disabled={!wfId}>
              Save
            </button>
          </div>
          <h2>Workflows</h2>
          <div className="list">
            {workflows.map((w) => (
              <button
                key={w.id}
                type="button"
                className={`item${w.id === wfId ? " active" : ""}`}
                onClick={() => loadWorkflow(w.id).catch((e) => setError(e.message))}
              >
                {w.name}
              </button>
            ))}
            {workflows.length === 0 && <div className="hint">No workflows yet</div>}
          </div>
          <h2>Palette</h2>
          <div className="palette">
            {NODE_CATALOG.map((t) => (
              <button key={t} type="button" onClick={() => addNode(t)}>
                {t}
              </button>
            ))}
          </div>
        </aside>
        <main className="canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={setRf}
            nodeTypes={nodeTypes}
            onSelectionChange={({ nodes: sel }) => setSelectedId(sel[0]?.id ?? null)}
            fitView
            colorMode="dark"
          >
            <Background color="rgba(44, 212, 195, 0.15)" gap={32} />
            <Controls />
          </ReactFlow>
        </main>
        <aside className="inspector">
          <h2>Inspector</h2>
          {wfId && (
            <label className="field">
              Name
              <input className="wf-name" value={wfName} onChange={(e) => setWfName(e.target.value)} />
            </label>
          )}
          {!selected && <p className="hint">Select a node</p>}
          {selected && (
            <>
              <div className="mono">{pfType}</div>
              {pfType === "auth.vaultUnlock" && (
                <Field
                  label="vaultItemId"
                  value={params.vaultItemId}
                  onChange={(v) => patchParams({ vaultItemId: v })}
                />
              )}
              {pfType === "phone.type" && (
                <>
                  <Field label="text" value={params.text} onChange={(v) => patchParams({ text: v })} />
                  <p className="hint">use VaultUnlock for secrets</p>
                </>
              )}
              {pfType === "phone.openApp" && (
                <Field label="app" value={params.app} onChange={(v) => patchParams({ app: v })} />
              )}
              {pfType === "phone.tap" && (
                <>
                  <Field label="label" value={params.label} onChange={(v) => patchParams({ label: v })} />
                  <Field label="x" type="number" value={params.x} onChange={(v) => patchParams({ x: v })} />
                  <Field label="y" type="number" value={params.y} onChange={(v) => patchParams({ y: v })} />
                </>
              )}
              {pfType === "phone.swipe" && (
                <>
                  <Field
                    label="from.x"
                    type="number"
                    value={params.from?.x}
                    onChange={(v) => patchParams({ from: { ...(params.from || {}), x: v } })}
                  />
                  <Field
                    label="from.y"
                    type="number"
                    value={params.from?.y}
                    onChange={(v) => patchParams({ from: { ...(params.from || {}), y: v } })}
                  />
                  <Field
                    label="to.x"
                    type="number"
                    value={params.to?.x}
                    onChange={(v) => patchParams({ to: { ...(params.to || {}), x: v } })}
                  />
                  <Field
                    label="to.y"
                    type="number"
                    value={params.to?.y}
                    onChange={(v) => patchParams({ to: { ...(params.to || {}), y: v } })}
                  />
                  <Field
                    label="durationMs"
                    type="number"
                    value={params.durationMs}
                    onChange={(v) => patchParams({ durationMs: v })}
                  />
                </>
              )}
              {pfType === "phone.wait" && (
                <Field label="ms" type="number" value={params.ms} onChange={(v) => patchParams({ ms: v })} />
              )}
              {pfType === "flow.if" && (
                <Field label="match" value={params.match} onChange={(v) => patchParams({ match: v })} />
              )}
              {pfType === "flow.confirm" && (
                <Field label="prompt" value={params.prompt} onChange={(v) => patchParams({ prompt: v })} />
              )}
              {pfType === "flow.stop" && (
                <Field label="reason" value={params.reason} onChange={(v) => patchParams({ reason: v })} />
              )}
            </>
          )}
        </aside>
      </div>
      <footer className="console" id="run-console">
        <div>
          <h3>Run</h3>
          <div className="toolbar">
            <button className="btn primary" type="button" onClick={onRun} disabled={!wfId}>
              Run
            </button>
          </div>
          <div className="status">{run ? `${run.status}${run.currentNodeId ? ` @ ${run.currentNodeId}` : ""}` : "idle"}</div>
          {errorText(run?.error) && <p className="error">{errorText(run?.error)}</p>}
          {error && <p className="error">{error}</p>}
          {run?.status === "awaiting_confirm" && (
            <div>
              <p className="hint">{confirmPrompt}</p>
              <div className="confirm-row">
                <button className="btn primary" type="button" onClick={() => onConfirm("approve")}>
                  Approve
                </button>
                <button className="btn" type="button" onClick={() => onConfirm("deny")}>
                  Deny
                </button>
              </div>
            </div>
          )}
        </div>
        {runId && frameSeq >= 0 ? (
          <img className="frame" alt="last frame" src={`/api/runs/${runId}/frames/${frameSeq}`} />
        ) : (
          <div className="frame" />
        )}
      </footer>
    </div>
  );
}
