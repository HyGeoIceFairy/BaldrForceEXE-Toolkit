"""在屏幕外创建自己拥有的两个真实窗口，复现游戏重新置顶后的 HUD 层级竞争。"""

from __future__ import annotations

from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from bfmod.window_layer import TOPMOST_FLAGS, WindowLayerController

app = QApplication(["window-layer-contract"])
layer = WindowLayerController()
native = layer._user32
native.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
native.GetWindow.restype = wintypes.HWND
foreground = native.GetForegroundWindow()
windows = []
try:
    for title in ("BFMOD-owned game fixture", "BFMOD-owned HUD fixture"):
        window = QWidget(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.setWindowTitle(title)
        window.setGeometry(-30000, -30000, 100, 100)
        window.show()
        windows.append(window)
    app.processEvents()
    game, hud = (int(window.winId()) for window in windows)
    native.SetWindowPos(game, wintypes.HWND(-1), 0, 0, 0, 0, TOPMOST_FLAGS)

    def is_above(upper: int, lower: int) -> bool:
        current = native.GetWindow(lower, 3)  # GW_HWNDPREV
        for _ in range(10000):
            if not current:
                return False
            if current == upper:
                return True
            current = native.GetWindow(current, 3)
        raise AssertionError("unexpected window chain")

    assert is_above(game, hud), "fixture failed to reproduce topmost occlusion"
    assert layer.maintain(hud, force=True), layer.last_error
    assert is_above(hud, game), "HUD did not recover above another topmost window"
    assert native.GetForegroundWindow() == foreground, "foreground focus changed"
    print("native stacking: PASS; foreground unchanged")
finally:
    for window in windows:
        window.close()
    app.processEvents()
