"""发行包自检：隔离设置，不载入游戏，不注册真实快捷键。"""

from __future__ import annotations

import os
from tempfile import TemporaryDirectory


def run_smoke_test() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings, Qt

    from .gui import MainWindow, create_application

    class SmokeWindow(MainWindow):
        def _load_initial_workspace(self) -> None:
            pass

    with TemporaryDirectory(prefix="bfmod-smoke-") as temporary:
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, temporary)
        app = create_application(["bfmod-smoke"])
        app.setOrganizationName("BaldrForceModifierSmoke")
        window = SmokeWindow()
        try:
            window.show()
            window.realtime_page.show_hud.setChecked(True)
            app.processEvents()
            if window.pages.count() != 7 or not window.battle_hud.isVisible():
                return 3
            if not window.battle_hud.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus:
                return 4
            if window.realtime_monitor is not None or window.battle_plan.effects:
                return 5
            return 0
        finally:
            window.close()
            app.processEvents()
