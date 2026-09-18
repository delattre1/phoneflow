"""Owner-editable settings: the LLM key, blocked apps, and per-app hints.

A one-click install has no compose.yml for the owner to edit and no shell to
export variables in, so everything a non-developer may need to change lives in
PHONEFLOW_HOME (the agent's persistent volume) and is reachable over the API —
which is what lets the owner change it by just texting the agent.

Precedence is always: what the owner saved here, then the environment, then the
built-in default. An owner's edit must win over whatever the image shipped.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# The only keys config.json may hold. `llmApiKey` is a secret: it is written
# 0600 and never echoed back by public_config().
_KEYS = {"llmApiKey", "llmBaseUrl", "agentModel", "blockedApps"}
_HINT_NAME = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,39}$")
MAX_HINT_BYTES = 8000


def home() -> Path | None:
    raw = os.environ.get("PHONEFLOW_HOME")
    return Path(raw) if raw else None


def _config_path(root: Path | None = None) -> Path | None:
    root = root or home()
    return root / "config.json" if root else None


def load_config(root: Path | None = None) -> dict:
    path = _config_path(root)
    if not path or not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items() if k in _KEYS} if isinstance(doc, dict) else {}


def save_config(root: Path, patch: dict) -> dict:
    """Merge `patch` into config.json. A null or empty value clears that key."""
    unknown = set(patch) - _KEYS
    if unknown:
        raise ValueError("unknown setting: " + ", ".join(sorted(unknown)))
    doc = load_config(root)
    for key, value in patch.items():
        if value in (None, "", []):
            doc.pop(key, None)
        elif key == "blockedApps":
            if isinstance(value, str):
                value = value.split(",")
            if not isinstance(value, list):
                raise ValueError("blockedApps must be a list of app names")
            doc[key] = [str(v).strip() for v in value if str(v).strip()]
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            doc[key] = value.strip()
    path = _config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return public_config(root)


def _own_key() -> str:
    """A key the owner supplied themselves (config, then environment)."""
    return (load_config().get("llmApiKey")
            or os.environ.get("PHONEFLOW_LLM_API_KEY")
            or os.environ.get("OLLAMA_API_KEY") or "")


def plow_llm() -> tuple[str, str] | None:
    """(base_url, token) of Plow's own OpenAI-compatible model gateway, when
    this container was provisioned as a Plow agent. It is the same endpoint the
    chat agent (Hermes) already talks to, billed to the agent's Plow credits,
    so a one-click install needs no key of its own."""
    token = os.environ.get("PLOW_AGENT_TOKEN")
    base = os.environ.get("PLOW_API_BASE")
    if token and base:
        return base.rstrip("/") + "/v1", token
    return None


def llm_provider() -> str:
    """'own' when the owner set a key, 'plow' when the agent's credits are
    used, 'none' when there is nothing to plan with."""
    if _own_key():
        return "own"
    if plow_llm():
        return "plow"
    return "none"


def llm_api_key() -> str:
    own = _own_key()
    if own:
        return own
    plow = plow_llm()
    return plow[1] if plow else ""


def llm_base_url() -> str:
    explicit = load_config().get("llmBaseUrl") or os.environ.get("PHONEFLOW_LLM_BASE_URL")
    if explicit:
        return explicit
    if not _own_key():
        plow = plow_llm()
        if plow:
            return plow[0]
    return "https://ollama.com/v1"


def grounder_model() -> str:
    return os.environ.get("PHONEFLOW_GROUNDER_MODEL", "qwen3.5:397b")


def list_models(client) -> list[str] | None:
    """Model ids the endpoint serves, or None when it cannot say."""
    try:
        return [m.id for m in client.models.list().data]
    except Exception:  # noqa: BLE001
        return None


def model_report(client=None) -> dict:
    """Which model each role will actually run on, against the live endpoint.

    {"provider", "baseUrl", "planner": {"wanted", "resolved", "found"},
     "grounder": {...}, "available": [...] | None}. `found` is None when the
    endpoint does not list its models; then the name is passed through as is.
    """
    if client is None:
        import openai  # lazy: the graph path needs no LLM at all
        key = llm_api_key()
        if not key:
            return {"provider": "none", "baseUrl": llm_base_url(), "available": None,
                    "planner": None, "grounder": None}
        client = openai.OpenAI(api_key=key, base_url=llm_base_url(), timeout=15, max_retries=0)
    available = list_models(client)

    def role(wanted: str) -> dict:
        if not wanted:
            return {"wanted": "", "resolved": "", "found": None}
        resolved = resolve_model(client, wanted, available)
        found = None if available is None else (resolved in available)
        return {"wanted": wanted, "resolved": resolved, "found": found}

    return {"provider": llm_provider(), "baseUrl": llm_base_url(), "available": available,
            "planner": role(agent_model()), "grounder": role(grounder_model())}


def resolve_model(client, wanted: str, available: list[str] | None = None) -> str:
    """Map a model name onto what the endpoint actually serves.

    Gateways disagree on spelling: the same GLM is `glm-5.3-flash` on one and
    `z-ai/glm-5.3-flash` or `glm-5.3:flash` on another. An exact match wins;
    otherwise the listed id whose normalised form contains the wanted one (or
    the reverse) is used; with no match at all the name is passed through and
    the endpoint's own error tells the owner.
    """
    if available is None:
        available = list_models(client)
        if available is None:  # listing is a nicety, not a requirement
            return wanted
    if wanted in available:
        return wanted

    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    w = norm(wanted)
    for cand in available:  # same name under a vendor prefix: z-ai/glm-5.3
        if norm(cand.split("/")[-1]) == w:
            return cand
    for cand in available:  # looser: glm-5.3 inside glm-5.3-flash
        if w in norm(cand):
            return cand
    for cand in available:
        if norm(cand) in w:
            return cand
    return wanted


def agent_model() -> str:
    return (load_config().get("agentModel")
            or os.environ.get("PHONEFLOW_AGENT_MODEL") or "glm-5.3")


def blocked_apps() -> list[str]:
    saved = load_config().get("blockedApps")
    if saved is None:
        saved = os.environ.get("PHONEFLOW_BLOCKED_APPS", "").split(",")
    return [a.strip().lower() for a in saved if a.strip()]


def public_config(root: Path | None = None) -> dict:
    """The settings, safe to show: the key is reported as set/unset, never echoed."""
    return {
        "llmProvider": llm_provider(),
        "llmApiKeySet": bool(llm_api_key()),
        "llmBaseUrl": llm_base_url(),
        "agentModel": agent_model(),
        "blockedApps": blocked_apps(),
    }


# --- app hints -------------------------------------------------------------

def bundled_hints_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "app_hints"


def user_hints_dir(root: Path | None = None) -> Path | None:
    root = root or home()
    return root / "app_hints" if root else None


def hint_dirs() -> list[Path]:
    """Where hints are read from, most specific first — the owner's own notes
    beat PHONEFLOW_APP_HINTS, which beats what the image bundled."""
    dirs = []
    user = user_hints_dir()
    if user:
        dirs.append(user)
    env = os.environ.get("PHONEFLOW_APP_HINTS")
    if env:
        dirs.append(Path(env))
    dirs.append(bundled_hints_dir())
    return dirs


def _hint_files() -> dict[str, tuple[Path, str]]:
    """name -> (path, source); the first directory to define a name wins."""
    user = user_hints_dir()
    found: dict[str, tuple[Path, str]] = {}
    for d in hint_dirs():
        for path in sorted(d.glob("*.md")) if d.is_dir() else []:
            name = path.stem.lower()
            if name not in found:
                found[name] = (path, "owner" if d == user else "bundled")
    return found


def list_hints() -> list[dict]:
    return [{"app": n, "source": src} for n, (_, src) in sorted(_hint_files().items())]


def read_hint(name: str) -> dict | None:
    hit = _hint_files().get((name or "").strip().lower())
    if not hit:
        return None
    try:
        return {"app": hit[0].stem.lower(), "source": hit[1],
                "text": hit[0].read_text(encoding="utf-8")}
    except OSError:
        return None


def check_hint_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not _HINT_NAME.match(name):
        raise ValueError("app name must be 1-40 chars of a-z, 0-9, space, '-' or '_'")
    return name


def write_hint(root: Path, name: str, text: str) -> dict:
    name = check_hint_name(name)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("hint text is empty")
    if len(text.encode("utf-8")) > MAX_HINT_BYTES:
        raise ValueError(f"hint is over {MAX_HINT_BYTES} bytes; keep it to what the agent needs")
    d = user_hints_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text.strip() + "\n", encoding="utf-8")
    return {"app": name, "source": "owner", "text": text.strip() + "\n"}


def delete_hint(root: Path, name: str) -> bool:
    """Remove the OWNER's hint. A bundled hint of the same name shows again."""
    path = user_hints_dir(root) / f"{check_hint_name(name)}.md"
    if not path.is_file():
        return False
    path.unlink()
    return True


def hint_for_app(name: str) -> str:
    key = (name or "").strip().lower()
    if not key:
        return ""
    for base, (path, _) in _hint_files().items():
        if base == key or base in key or key in base:
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    return ""


def hints_for_goal(goal: str) -> str:
    g = (goal or "").lower()
    out = []
    for base, (path, _) in _hint_files().items():
        if base in g:
            try:
                out.append(path.read_text(encoding="utf-8").strip())
            except OSError:
                continue
    return "\n\n".join(out)
