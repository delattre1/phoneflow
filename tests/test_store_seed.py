from pathlib import Path

from pf_api.store import ensure_seeded, list_workflows


def test_ensure_seeded_copies_bundled(tmp_path: Path):
    ensure_seeded(tmp_path)
    ids = {w["id"] for w in list_workflows(tmp_path)}
    assert "wf_settings_general" in ids
    assert "wf_app_lookup" in ids
