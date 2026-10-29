"""Windows RegisterHotKey：不注入、不轮询键盘、不抢游戏焦点。"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtWidgets import QApplication

HOTKEYS = {
    "heal": 0x75,
    "cool": 0x76,
    "fc": 0x77,
    "pause": 0x78,
    "hud": 0x79,
    "fc_lock": 0x7A,
    "stop": 0x7B,
}


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self, actions: dict[str, Callable[[], None]]) -> None:
        super().__init__()
        self._actions = actions
        self._registered: dict[int, str] = {}
        self._user32: Any = None
        if sys.platform == "win32":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.UINT,
            ]
            self._user32.RegisterHotKey.restype = wintypes.BOOL
            self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self._user32.UnregisterHotKey.restype = wintypes.BOOL

    def start(self) -> tuple[str, ...]:
        self.stop()
        if self._user32 is None:
            return ("当前系统不支持 Windows 全局快捷键",)
        failures = []
        for index, (name, key) in enumerate(HOTKEYS.items(), start=0x4BF0):
            # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
            if self._user32.RegisterHotKey(None, index, 0x4003, key):
                self._registered[index] = name
            else:
                failures.append(f"Ctrl+Alt+F{key - 0x6F} 被占用或注册失败")
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self)
        return tuple(failures)

    def stop(self) -> None:
        if self._user32 is not None:
            for identifier in self._registered:
                self._user32.UnregisterHotKey(None, identifier)
        self._registered.clear()
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:
        if self._registered and bytes(event_type) in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            msg = wintypes.MSG.from_address(int(message))
            name = self._registered.get(int(msg.wParam)) if msg.message == 0x0312 else None
            if name is not None:
                # 离开 native filter 后再运行 UI 和内存操作。
                QTimer.singleShot(0, self._actions[name])
                return True, 0
        return False, 0
