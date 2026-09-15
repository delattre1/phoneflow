from pf_mirror.scripts.mirror import LOCK_RE, LatchDriver, click_script, window_script


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


def test_latch_driver_open_app_records_script_without_osascript():
    drv = LatchDriver()
    drv.open_app("Settings")
    assert ("open_app", "Settings") in drv.commands
    assert any("Settings" in src for src in drv.scripts)


def test_latch_driver_run_applescript_not_implemented():
    drv = LatchDriver()
    try:
        drv.run_applescript("tell application \"System Events\" to get name")
    except NotImplementedError:
        return
    raise AssertionError("run_applescript must raise NotImplementedError")


def test_latch_driver_does_not_set_ocr_text():
    drv = LatchDriver()
    assert not hasattr(drv, "ocr_text")
