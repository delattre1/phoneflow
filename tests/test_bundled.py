# tests/test_bundled.py
import json
from pathlib import Path
from pf_api.schema import validate_workflow

def test_settings_and_lookup_validate():
    root = Path(__file__).resolve().parents[1] / "workflows"
    for name in ("wf_settings_general.json", "wf_app_lookup.json"):
        validate_workflow(json.loads((root / name).read_text()))
