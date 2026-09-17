from __future__ import annotations

from typing import Protocol


class IPhoneDriver(Protocol):
    def open_app(self, app: str) -> None: ...
    def screenshot(self) -> bytes: ...
    def ocr(self, frame: bytes) -> str: ...
    def ocr_items(self, frame: bytes) -> list[dict]: ...
    def frame_for_model(self) -> bytes: ...
    def window_crop(self, frame: bytes) -> tuple[bytes, dict]: ...
    def tap(self, x: float, y: float) -> None: ...
    def tap_point(self, x: float, y: float) -> None: ...
    def long_press(self, x: float, y: float, hold_ms: int = 800) -> None: ...
    def swipe_from(self, x: float, y: float, direction: str, distance: float = 0.5) -> None: ...
    def system_key(self, button: str) -> None: ...
    def back(self) -> None: ...
    def reset_app(self, app: str) -> None: ...
    def drag(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 600) -> None: ...
    def tap_label(self, label: str) -> None: ...
    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def vault_fill(self, vault_item_id: str) -> str: ...


class DriverError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FakeDriver:
    def __init__(self, ocr_text: str = "", vault_result: str = "filled", items: list | None = None):
        self.ocr_text = ocr_text
        self.vault_result = vault_result
        self.items = items or []
        self.calls: list[tuple] = []

    def open_app(self, app: str) -> None:
        self.calls.append(("open_app", app))

    def screenshot(self) -> bytes:
        self.calls.append(("screenshot",))
        return b"PNG"

    def ocr(self, frame: bytes) -> str:
        self.calls.append(("ocr",))
        return self.ocr_text

    def ocr_items(self, frame: bytes) -> list[dict]:
        self.calls.append(("ocr_items",))
        return list(self.items)

    def frame_for_model(self) -> bytes:
        self.calls.append(("frame_for_model",))
        return b"PNG"

    def window_crop(self, frame: bytes) -> tuple[bytes, dict]:
        self.calls.append(("window_crop",))
        return b"JPG", {"pos": (100, 100), "size": (300, 600), "px_w": 900, "px_h": 1800}

    def tap_point(self, x: float, y: float) -> None:
        self.calls.append(("tap_point", x, y))

    def long_press(self, x: float, y: float, hold_ms: int = 800) -> None:
        self.calls.append(("long_press", x, y))

    def swipe_from(self, x: float, y: float, direction: str, distance: float = 0.5) -> None:
        self.calls.append(("swipe_from", x, y, direction))

    def system_key(self, button: str) -> None:
        self.calls.append(("system_key", button))

    def back(self) -> None:
        self.calls.append(("back",))

    def tap(self, x: float, y: float) -> None:
        self.calls.append(("tap", x, y))

    def tap_label(self, label: str) -> None:
        self.calls.append(("tap_label", label))
        if self.ocr_text and label not in self.ocr_text:
            raise DriverError("element_not_found", label)

    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None:
        self.calls.append(("swipe", frm, to, duration_ms))

    def type_text(self, text: str) -> None:
        self.calls.append(("type_text", text))

    def vault_fill(self, vault_item_id: str) -> str:
        self.calls.append(("vault_fill", vault_item_id))
        return self.vault_result
