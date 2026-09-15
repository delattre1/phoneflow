from __future__ import annotations

from typing import Protocol


class IPhoneDriver(Protocol):
    def open_app(self, app: str) -> None: ...
    def screenshot(self) -> bytes: ...
    def ocr(self, frame: bytes) -> str: ...
    def tap(self, x: float, y: float) -> None: ...
    def tap_label(self, label: str) -> None: ...
    def swipe(self, frm: dict, to: dict, duration_ms: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def vault_fill(self, vault_item_id: str) -> str: ...


class DriverError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FakeDriver:
    def __init__(self, ocr_text: str = "", vault_result: str = "filled"):
        self.ocr_text = ocr_text
        self.vault_result = vault_result
        self.calls: list[tuple] = []

    def open_app(self, app: str) -> None:
        self.calls.append(("open_app", app))

    def screenshot(self) -> bytes:
        self.calls.append(("screenshot",))
        return b"PNG"

    def ocr(self, frame: bytes) -> str:
        self.calls.append(("ocr",))
        return self.ocr_text

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
