from pathlib import Path

def test_theme_tokens():
    css = (Path(__file__).resolve().parents[1] / "web/src/theme.css").read_text()
    assert "#2CD4C3" in css
    assert "#07070d" in css
    assert "Inter" in css
    assert "JetBrains Mono" in css
    assert "#FFD700" not in css
