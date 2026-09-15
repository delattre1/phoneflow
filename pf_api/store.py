from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path


def workflows_dir(home: Path) -> Path:
    return Path(home) / "workflows"


def runs_dir(home: Path) -> Path:
    return Path(home) / "runs"


def new_run_id() -> str:
    return "run_" + secrets.token_hex(6)


def ensure_dirs(home: Path) -> None:
    workflows_dir(home).mkdir(parents=True, exist_ok=True)
    runs_dir(home).mkdir(parents=True, exist_ok=True)


def ensure_seeded(home: Path) -> None:
    ensure_dirs(home)
    dest = workflows_dir(home)
    if any(dest.glob("*.json")):
        return
    src = Path("/opt/phoneflow/workflows")
    if not src.is_dir():
        src = Path(__file__).resolve().parents[1] / "workflows"
    if not src.is_dir():
        return
    for p in src.glob("*.json"):
        shutil.copy2(p, dest / p.name)


def save_workflow(home: Path, doc: dict) -> None:
    ensure_dirs(home)
    path = workflows_dir(home) / f"{doc['id']}.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def load_workflow(home: Path, wf_id: str) -> dict | None:
    path = workflows_dir(home) / f"{wf_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_workflows(home: Path) -> list[dict]:
    d = workflows_dir(home)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": doc["id"], "name": doc["name"]})
    return out


def delete_workflow(home: Path, wf_id: str) -> bool:
    path = workflows_dir(home) / f"{wf_id}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


def run_dir(home: Path, run_id: str) -> Path:
    return runs_dir(home) / run_id


def save_run(
    home: Path,
    run: dict,
    events: list[dict] | None = None,
    frames: list[bytes] | None = None,
) -> None:
    d = run_dir(home, run["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
    if events is not None:
        with (d / "events.jsonl").open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
    if frames is not None:
        fd = d / "frames"
        fd.mkdir(exist_ok=True)
        for i, blob in enumerate(frames):
            (fd / f"{i}.png").write_bytes(blob)


def load_run(home: Path, run_id: str) -> dict | None:
    path = run_dir(home, run_id) / "run.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(home: Path, run_id: str) -> list[dict]:
    path = run_dir(home, run_id) / "events.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def frame_path(home: Path, run_id: str, seq: int | str) -> Path:
    return run_dir(home, run_id) / "frames" / f"{seq}.png"
