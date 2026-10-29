from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bfmod.window_layer import TOPMOST_FLAGS, WindowLayerController


class WindowLayerTests(unittest.TestCase):
    def test_reasserts_topmost_without_activation_or_geometry_changes(self) -> None:
        layer = WindowLayerController()
        layer._user32 = Mock()
        layer._user32.SetWindowPos.return_value = True
        with patch.object(layer, "_foreground_matches", return_value=True):
            self.assertTrue(layer.maintain(1234))
        arguments = layer._user32.SetWindowPos.call_args.args
        self.assertEqual(arguments[0], 1234)
        self.assertEqual(arguments[1].value, ctypes.c_void_p(-1).value)
        self.assertEqual(arguments[2:6], (0, 0, 0, 0))
        self.assertEqual(arguments[6], TOPMOST_FLAGS)
        self.assertTrue(TOPMOST_FLAGS & 0x0010)  # NOACTIVATE
        self.assertFalse(TOPMOST_FLAGS & 0x0004)  # NOZORDER 会使层级恢复无效

    def test_leaves_unrelated_foreground_apps_alone(self) -> None:
        layer = WindowLayerController()
        layer._user32 = Mock()
        with patch.object(layer, "_foreground_matches", return_value=False):
            self.assertFalse(layer.maintain(1234))
            layer._user32.SetWindowPos.assert_not_called()
            self.assertTrue(layer.maintain(1234, force=True))

    def test_launcher_match_requires_full_path_and_closes_query_handle(self) -> None:
        layer = WindowLayerController()
        layer._user32 = Mock()
        layer._kernel32 = Mock()
        layer._user32.GetForegroundWindow.return_value = 123

        def get_pid(hwnd, pointer):  # type: ignore[no-untyped-def]
            pointer._obj.value = 987654
            return 1

        layer._user32.GetWindowThreadProcessId.side_effect = get_pid
        layer._kernel32.OpenProcess.return_value = 456
        layer.set_target_paths((Path("C:/game/Start-BaldrForce.exe"),))
        for path, expected in (
            ("C:/game/Start-BaldrForce.exe", True),
            ("C:/other/Start-BaldrForce.exe", False),
        ):

            def get_path(handle, flags, buffer, size, path=path):  # type: ignore[no-untyped-def]
                buffer.value = path
                return True

            layer._kernel32.QueryFullProcessImageNameW.side_effect = get_path
            self.assertEqual(layer._foreground_matches(), expected)
        self.assertEqual(layer._kernel32.CloseHandle.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "Windows-only native stacking regression")
    def test_native_two_topmost_windows_keep_foreground_and_restore_hud_above_game(self) -> None:
        helper = Path(__file__).parent / "helpers" / "window_layer_target.py"
        result = subprocess.run(
            [sys.executable, str(helper)],
            capture_output=True,
            text=True,
            timeout=20,
            env=os.environ | {"QT_QPA_PLATFORM": "windows"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("native stacking: PASS", result.stdout)
