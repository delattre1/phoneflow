import os
from pathlib import Path
from pf_api.server import make_server
from pf_api.driver import FakeDriver  # driver selected via PHONEFLOW_DRIVER=latch|fake

def main():
    home = Path(os.environ.get("PHONEFLOW_HOME", "/var/lib/hermes/phoneflow"))
    home.mkdir(parents=True, exist_ok=True)
    driver_kind = os.environ.get("PHONEFLOW_DRIVER", "fake")
    if driver_kind == "fake":
        driver = FakeDriver(ocr_text="General")
    else:
        from pf_mirror.scripts.mirror import LatchDriver
        driver = LatchDriver()
    def latch_ok():
        if driver_kind == "fake":
            return True
        return driver.health()
    httpd = make_server(home, driver, latch_ok, host="0.0.0.0", port=int(os.environ.get("PHONEFLOW_PORT", "8787")))
    httpd.serve_forever()

if __name__ == "__main__":
    main()
