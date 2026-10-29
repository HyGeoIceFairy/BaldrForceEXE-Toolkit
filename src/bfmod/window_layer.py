"""仅调整自己的 HUD 层级，不更改游戏窗口、不切换前台焦点。"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .process_memory import normalized_executable_path

TOPMOST_FLAGS = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE, NOMOVE, NOACTIVATE, NOOWNERZORDER


class WindowLayerController:
    def __init__(self) -> None:
        self._user32: Any = None
        self._kernel32: Any = None
        self._target_paths: frozenset[str] = frozenset()
        self.last_error = ""
        if sys.platform != "win32":
            return
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def set_target_paths(self, paths: tuple[Path, ...]) -> None:
        self._target_paths = frozenset(normalized_executable_path(path) for path in paths)

    def maintain(self, hud_handle: int, *, force: bool = False) -> bool:
        if self._user32 is None or not hud_handle:
            return False
        # 切到其他应用后不争抢层级；启动器可能拥有实际显示窗口，按完整路径识别。
        if not force and not self._foreground_matches():
            return False
        success = bool(
            self._user32.SetWindowPos(
                hud_handle,
                wintypes.HWND(-1),
                0,
                0,
                0,
                0,
                TOPMOST_FLAGS,
            )
        )
        self.last_error = "" if success else f"Windows 置顶失败（{ctypes.get_last_error()}）"
        return success

    def _foreground_matches(self) -> bool:
        foreground = self._user32.GetForegroundWindow()
        if not foreground:
            return False
        pid = wintypes.DWORD()
        if not self._user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid)):
            return False
        if pid.value == os.getpid():
            return True
        if not self._target_paths:
            return False
        handle = self._kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return False
            return normalized_executable_path(buffer.value) in self._target_paths
        finally:
            self._kernel32.CloseHandle(handle)
