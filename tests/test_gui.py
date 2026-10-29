from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from bfmod.backups import BackupFileInfo, BackupInfo  # noqa: E402
from bfmod.gui import (  # noqa: E402
    DEFAULT_APP_DATA,
    MainWindow,
    _app_data_path,
    build_restore_preview,
    create_application,
)
from bfmod.gui_pages import ForceCrashPage, RealtimePage  # noqa: E402
from bfmod.process_memory import ProcessInstance  # noqa: E402
from bfmod.realtime import (  # noqa: E402
    PlayerState,
    RealtimeEffectError,
    RealtimeFrameUnstableError,
    RealtimePlayerUnavailableError,
    RealtimeSnapshot,
    RealtimeWeaponState,
    WeaponSlotState,
)
from bfmod.realtime_profiles import (  # noqa: E402
    KNOWN_REALTIME_PROFILE,
    ProfileCapabilitySupport,
    RealtimeProfileDefinition,
    SelectedRealtimeProfile,
)
from bfmod.versioning import KNOWN_FINGERPRINTS, InstallationIdentity  # noqa: E402
from bfmod.workspace import GameWorkspace  # noqa: E402


class GuiStructureTests(unittest.TestCase):
    application: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or create_application(["bfmod-test"])

    def test_main_window_has_all_primary_pages_without_loading_a_game(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        try:
            self.assertEqual(window.pages.count(), 7)
            self.assertEqual(window.navigation.count(), 7)
            self.assertIsInstance(window.pages.widget(2), ForceCrashPage)
            self.assertIsInstance(window.pages.widget(3), RealtimePage)
            self.assertIs(window.force_crash_page, window.pages.widget(2))
            self.assertIs(window.realtime_page, window.pages.widget(3))
            self.assertEqual(window.weapon_table.columnCount(), 8)
            self.assertEqual(window.force_crash_table.columnCount(), 5)
            self.assertFalse(window.apply_button.isEnabled())
            self.assertFalse(window.force_crash_apply_button.isEnabled())
            self.assertEqual(window.realtime_status.text(), "尚未连接")
            self.assertTrue(window.realtime_auto_reconnect.isChecked())
            self.assertTrue(window.realtime_event_log.isReadOnly())
            self.assertIn("尚无实时事件", window.realtime_event_log.toPlainText())
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertFalse(window.realtime_reduce_heat_button.isEnabled())
            self.assertEqual(window.realtime_health_value.text(), "— / —")
            self.assertEqual(window.realtime_heat_value.text(), "— / —")
            self.assertIn("BALDR FORCE EXE", window.windowTitle())
            filter_items = [
                window.filter_combo.itemText(index) for index in range(window.filter_combo.count())
            ]
            self.assertIn("可开发", filter_items)
            self.assertEqual(window.selection_label.text(), "已选 0 / 当前 0")
        finally:
            window.close()

    def test_empty_configured_backup_path_falls_back_to_the_application_default(self) -> None:
        settings = Mock()
        settings.value.return_value = "   "

        with patch("bfmod.gui.QSettings", return_value=settings):
            path = _app_data_path()

        self.assertEqual(path, (DEFAULT_APP_DATA / "backups").absolute())

    def test_first_start_without_detected_game_does_not_force_a_directory_dialog(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        try:
            with (
                patch.object(window.settings, "value", return_value=""),
                patch("bfmod.gui.resolve_game_root", side_effect=ValueError("not found")),
                patch.object(window, "_choose_game_root") as choose_game_root,
            ):
                window._load_initial_workspace()

            choose_game_root.assert_not_called()
            self.assertIn("未找到游戏", window.installation_value.text())
            self.assertIn("选择", window.safety_text.text())
        finally:
            window.close()

    def test_context_shortcuts_operate_on_the_visible_editor(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        try:
            with (
                patch.object(window, "_undo") as undo_weapon,
                patch.object(window, "_undo_force_crash") as undo_force_crash,
                patch.object(window, "_redo") as redo_weapon,
                patch.object(window, "_redo_force_crash") as redo_force_crash,
            ):
                window.navigation.setCurrentRow(2)
                window._undo_current_page()
                window._redo_current_page()
                undo_force_crash.assert_called_once_with()
                redo_force_crash.assert_called_once_with()
                undo_weapon.assert_not_called()
                redo_weapon.assert_not_called()

                window.navigation.setCurrentRow(1)
                window._undo_current_page()
                window._redo_current_page()
                undo_weapon.assert_called_once_with()
                redo_weapon.assert_called_once_with()

                window.navigation.setCurrentRow(0)
                window._undo_current_page()
                window._redo_current_page()
                undo_weapon.assert_called_once_with()
                redo_weapon.assert_called_once_with()
                undo_force_crash.assert_called_once_with()
                redo_force_crash.assert_called_once_with()

            window.navigation.setCurrentRow(2)
            window.force_crash_search.setText("测试")
            window._focus_current_search()
            self.assertEqual(window.navigation.currentRow(), 2)
            self.assertTrue(window.force_crash_search.hasSelectedText())
        finally:
            window.close()

    def test_window_stays_busy_until_every_background_worker_finishes(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        first = Mock()
        second = Mock()
        window.workers.update((first, second))
        window.setEnabled(False)
        try:
            window._worker_finished(first)
            self.assertFalse(window.isEnabled())

            window._worker_finished(second)
            self.assertTrue(window.isEnabled())
        finally:
            window.workers.clear()
            window.setEnabled(True)
            window.close()

    def test_navigation_refreshes_only_backups_and_diagnostics_pages(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        try:
            with (
                patch.object(window, "_refresh_backups") as refresh_backups,
                patch.object(window, "_refresh_diagnostics") as refresh_diagnostics,
            ):
                window._navigate(3)
                refresh_backups.assert_not_called()
                refresh_diagnostics.assert_not_called()

                window._navigate(4)
                refresh_backups.assert_called_once_with()
                refresh_diagnostics.assert_not_called()

                window._navigate(5)
                refresh_diagnostics.assert_called_once_with()
        finally:
            window.close()

    def test_reload_does_not_discard_a_pending_draft_without_confirmation(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.workspace = Mock(
            game_root=Path("C:/Game/Baldr Force Exe"),
            identity=_known_identity(),
        )
        window.session = Mock(changes=(object(),))
        try:
            with (
                patch(
                    "bfmod.gui.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.No,
                ),
                patch.object(window, "_load_workspace") as load_workspace,
            ):
                window._reload_workspace()

            load_workspace.assert_not_called()
        finally:
            window.session = None
            window.close()

    def test_realtime_connection_is_polled_and_disconnect_stops_timer(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.workspace = Mock(
            game_root=Path("C:/Game/Baldr Force Exe"),
            identity=_known_identity(),
        )
        instance = ProcessInstance(
            123,
            456,
            Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
            "x86",
            0x400000,
        )
        connected = RealtimeSnapshot(
            "connected_read_only",
            instance,
            True,
            "只读连接正常",
        )
        disconnected = RealtimeSnapshot(
            "disconnected",
            None,
            False,
            "目标进程实例已退出，已停用实时会话",
        )
        monitor = Mock()
        monitor.connect.return_value = connected
        monitor.tick_effects.return_value = disconnected
        try:
            window.realtime_auto_reconnect.setChecked(True)
            window.realtime_page.health_maintenance.blockSignals(True)
            window.realtime_page.health_maintenance.setChecked(True)
            window.realtime_page.health_maintenance.blockSignals(False)
            window.realtime_page.heat_maintenance.blockSignals(True)
            window.realtime_page.heat_maintenance.setChecked(True)
            window.realtime_page.heat_maintenance.blockSignals(False)
            window.realtime_page.show_active_effects(("health", "heat"))
            with (
                patch("bfmod.gui.WindowsProcessMemoryAdapter"),
                patch("bfmod.gui.RealtimeMonitor", return_value=monitor),
            ):
                window._connect_realtime_monitor()

            self.assertTrue(window.realtime_poll_timer.isActive())
            window._poll_realtime_monitor()
            monitor.tick_effects.assert_called_once_with()
            self.assertIsNone(window.realtime_monitor)
            self.assertFalse(window.realtime_poll_timer.isActive())
            self.assertTrue(window.realtime_reconnect_timer.isActive())
            self.assertIn("已退出", window.realtime_status.text())
            self.assertIn("已退出", window.realtime_last_stop_reason.text())
            self.assertIn("自动重连", window.realtime_event_log.toPlainText())
            self.assertFalse(window.realtime_page.health_maintenance.isChecked())
            self.assertFalse(window.realtime_page.heat_maintenance.isChecked())
            self.assertIn("没有启用", window.realtime_effect_status.text())
        finally:
            window.realtime_monitor = None
            window.realtime_reconnect_timer.stop()
            window.close()

    def test_emergency_stop_disarms_automatic_reconnect(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.disconnect.return_value = RealtimeSnapshot(
            "disconnected",
            None,
            False,
            "用户执行紧急全停",
        )
        window.realtime_monitor = monitor
        window.realtime_auto_reconnect.setChecked(True)
        window._realtime_connection_requested = True
        window.realtime_poll_timer.start()
        window.realtime_reconnect_timer.start()
        try:
            window._stop_realtime_monitor()

            monitor.disconnect.assert_called_once_with("用户执行紧急全停")
            self.assertFalse(window._realtime_connection_requested)
            self.assertFalse(window.realtime_poll_timer.isActive())
            self.assertFalse(window.realtime_reconnect_timer.isActive())
            self.assertIn("紧急全停", window.realtime_last_stop_reason.text())
            self.assertIn("自动重连已解除", window.realtime_event_log.toPlainText())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_poll_updates_hud_and_ticks_enabled_effects(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        instance = ProcessInstance(
            123,
            456,
            Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
            "x86",
            0x400000,
        )
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot(
            "connected_read_only",
            instance,
            True,
            "只读连接正常",
            ("health",),
        )
        monitor.tick_effects.return_value = monitor.snapshot
        monitor.read_player_state.return_value = PlayerState(
            72,
            100,
            35,
            200,
            7,
            7,
            1,
            7500,
            10000,
        )
        monitor.read_weapon_state.return_value = RealtimeWeaponState(
            tuple(WeaponSlotState(slot, slot + 10, slot in (0, 3)) for slot in range(13)),
            3,
            13,
            True,
            3,
            (10, 13, 10),
        )
        window.realtime_monitor = monitor
        try:
            with (
                patch.object(window, "_realtime_hud_available", return_value=True),
                patch.object(window, "_realtime_weapon_state_available", return_value=True),
            ):
                window._poll_realtime_monitor()

            monitor.tick_effects.assert_called_once_with()
            monitor.read_player_state.assert_called_once_with()
            self.assertEqual(window.realtime_health_value.text(), "72 / 100")
            self.assertEqual(window.realtime_heat_value.text(), "35 / 200")
            self.assertEqual(window.realtime_force_crash_value.text(), "7500 / 10000")
            self.assertEqual(window.realtime_health_bar.value(), 72)
            self.assertEqual(window.realtime_heat_bar.value(), 18)
            self.assertEqual(window.realtime_force_crash_bar.value(), 75)
            self.assertIn("血量维持", window.realtime_effect_status.text())
            self.assertIn("普通战斗", window.realtime_page.hud_status.text())
            self.assertIn("槽 3", window.realtime_page.weapon_recent.text())
            self.assertIn("ID 13", window.realtime_page.weapon_recent.text())
            self.assertIn("10 → 13 → 10", window.realtime_page.weapon_history.text())
            self.assertEqual(window.realtime_poll_timer.interval(), 100)
        finally:
            window.realtime_monitor = None
            window.close()

    def test_practice_preset_enables_three_effects_and_syncs_controls(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        instance = ProcessInstance(123, 456, Path("C:/game/BaldrForce.exe"), "x86", 0x400000)
        monitor.snapshot = RealtimeSnapshot("connected_read_only", instance, True, "只读")

        def enable_combo() -> PlayerState:
            monitor.snapshot = RealtimeSnapshot(
                "connected_read_only", instance, True, "已启用", ("health", "heat", "weapon_repeat")
            )
            return PlayerState(72, 100, 35, 130, 7, 7, 3, scene_name="练习战斗")

        monitor.enable_practice_effects.side_effect = enable_combo
        window.realtime_monitor = monitor
        try:
            window._enable_practice_preset()
            monitor.enable_practice_effects.assert_not_called()
            self.assertTrue(window.battle_plan.practice_only)
            self.assertTrue(window.realtime_page.health_maintenance.isChecked())
            self.assertTrue(window.realtime_page.heat_maintenance.isChecked())
            self.assertTrue(window.realtime_page.weapon_repeat.isChecked())
            self.assertEqual(window.realtime_poll_timer.interval(), 100)
        finally:
            window.realtime_monitor = None
            window.close()

    def test_practice_preset_rejects_story_combat_and_rolls_back_partial_setup(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        instance = ProcessInstance(123, 456, Path("C:/game/BaldrForce.exe"), "x86", 0x400000)
        monitor.snapshot = RealtimeSnapshot("connected_read_only", instance, True, "只读")
        window.realtime_monitor = monitor
        try:
            monitor.enable_practice_effects.side_effect = RealtimeEffectError(
                "练习组合仅限练习战斗"
            )
            window._enable_practice_preset()
            monitor.enable_practice_effects.assert_not_called()
            self.assertFalse(
                window.battle_plan.resume(
                    monitor, PlayerState(50, 100, 30, 130, 7, 7, 0, scene_name="普通战斗")
                )
            )
            monitor.enable_practice_effects.side_effect = RealtimeEffectError("门禁变化")
            with self.assertRaises(RealtimeEffectError):
                window.battle_plan.resume(
                    monitor, PlayerState(50, 100, 30, 130, 7, 7, 3, scene_name="练习战斗")
                )
            monitor.stop_effects.assert_called()
            self.assertEqual(monitor.enable_practice_effects.call_count, 1)
            self.assertTrue(window.realtime_page.health_maintenance.isChecked())
            self.assertTrue(window.realtime_page.weapon_repeat.isChecked())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_weapon_hud_is_cleared_when_player_becomes_unavailable(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.tick_effects.return_value = RealtimeSnapshot(
            "connected_read_only",
            ProcessInstance(123, 456, Path("C:/game/BaldrForce.exe"), "x86", 0x400000),
            True,
            "只读连接正常",
        )
        monitor.read_player_state.side_effect = RealtimePlayerUnavailableError("已离开战斗")
        window.realtime_monitor = monitor
        try:
            window.realtime_page.show_weapon_state(((0, 69, True),), 0, 69, True, (69,))
            with patch.object(window, "_realtime_hud_available", return_value=True):
                window._poll_realtime_monitor()

            self.assertEqual(window.realtime_page.weapon_recent.text(), "最近发动：—")
            self.assertEqual(window.realtime_page.weapon_history.text(), "当前连段历史：—")
            self.assertEqual(
                window.realtime_page.weapon_configuration.text(),
                "配置：等待进入战斗后读取。",
            )
        finally:
            window.realtime_monitor = None
            window.close()

    def test_transient_player_sample_preserves_connection_and_enabled_effects(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        instance = ProcessInstance(123, 456, Path("C:/game/BaldrForce.exe"), "x86", 0x400000)
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot(
            "connected_read_only", instance, True, "战斗数据正在更新", ("weapon_repeat",)
        )
        monitor.tick_effects.return_value = monitor.snapshot
        monitor.read_player_state.side_effect = RealtimeFrameUnstableError("分段状态变化")
        window.realtime_monitor = monitor
        window.realtime_page.weapon_repeat.blockSignals(True)
        window.realtime_page.weapon_repeat.setChecked(True)
        window.realtime_page.weapon_repeat.blockSignals(False)
        try:
            with patch.object(window, "_realtime_hud_available", return_value=True):
                window._poll_realtime_monitor()
            self.assertIs(window.realtime_monitor, monitor)
            self.assertTrue(window.realtime_page.weapon_repeat.isChecked())
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertIn("等待下一帧", window.realtime_page.hud_status.text())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_transient_weapon_sample_preserves_player_hud(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        instance = ProcessInstance(123, 456, Path("C:/game/BaldrForce.exe"), "x86", 0x400000)
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot("connected_read_only", instance, True, "监控正常")
        monitor.tick_effects.return_value = monitor.snapshot
        monitor.read_player_state.return_value = PlayerState(72, 100, 35, 130, 7, 7, 1)
        monitor.read_weapon_state.side_effect = RealtimeFrameUnstableError("武器状态变化")
        window.realtime_monitor = monitor
        try:
            with (
                patch.object(window, "_realtime_hud_available", return_value=True),
                patch.object(window, "_realtime_weapon_state_available", return_value=True),
            ):
                window._poll_realtime_monitor()
            self.assertIs(window.realtime_monitor, monitor)
            self.assertEqual(window.realtime_health_value.text(), "72 / 100")
            self.assertEqual(window.realtime_page.weapon_recent.text(), "最近发动：—")
        finally:
            window.realtime_monitor = None
            window.close()

    def test_weapon_hud_and_repeat_toggle_are_cleared_on_disconnect(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.tick_effects.return_value = RealtimeSnapshot(
            "disconnected", None, False, "进程已退出"
        )
        window.realtime_monitor = monitor
        try:
            window.realtime_page.show_weapon_state(((0, 69, True),), 0, 69, True, (69,))
            window.realtime_page.weapon_repeat.blockSignals(True)
            window.realtime_page.weapon_repeat.setChecked(True)
            window.realtime_page.weapon_repeat.blockSignals(False)
            window._poll_realtime_monitor()

            self.assertEqual(window.realtime_page.weapon_recent.text(), "最近发动：—")
            self.assertEqual(window.realtime_page.weapon_history.text(), "当前连段历史：—")
            self.assertFalse(window.realtime_page.weapon_repeat.isChecked())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_expired_weapon_repeat_is_unchecked_without_rearming_on_next_battle(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.tick_effects.return_value = RealtimeSnapshot(
            "connected_read_only",
            ProcessInstance(123, 456, Path("C:/game/BaldrForce.exe"), "x86", 0x400000),
            True,
            "等待进入战斗",
            (),
        )
        monitor.read_player_state.side_effect = RealtimePlayerUnavailableError("已离开战斗")
        window.realtime_monitor = monitor
        try:
            window.realtime_page.weapon_repeat.blockSignals(True)
            window.realtime_page.weapon_repeat.setChecked(True)
            window.realtime_page.weapon_repeat.blockSignals(False)
            with patch.object(window, "_realtime_hud_available", return_value=True):
                window._poll_realtime_monitor()

            self.assertFalse(window.realtime_page.weapon_repeat.isChecked())
            monitor.enable_weapon_repeat.assert_not_called()
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_poll_returns_to_idle_interval_after_effects_stop(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot(
            "connected_read_only",
            ProcessInstance(
                123,
                456,
                Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
                "x86",
                0x400000,
            ),
            True,
            "只读连接正常",
            ("heat",),
        )
        window.realtime_monitor = monitor
        try:
            window._sync_realtime_poll_interval(monitor.snapshot)
            self.assertEqual(window.realtime_poll_timer.interval(), 100)

            monitor.snapshot = RealtimeSnapshot(
                "connected_read_only",
                monitor.snapshot.instance,
                True,
                "只读连接正常",
                (),
            )
            window._sync_realtime_poll_interval(monitor.snapshot)

            self.assertEqual(window.realtime_poll_timer.interval(), 1000)
        finally:
            window.realtime_monitor = None
            window.close()

    def test_weapon_repeat_uses_fast_polling_and_independent_toggle(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot(
            "connected_read_only",
            ProcessInstance(
                123,
                456,
                Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
                "x86",
                0x400000,
            ),
            True,
            "只读连接正常",
            (),
        )
        active = RealtimeSnapshot(
            "connected_read_only",
            monitor.snapshot.instance,
            True,
            "同连段武器重复已启用",
            ("weapon_repeat",),
        )
        monitor.enable_weapon_repeat.side_effect = lambda: setattr(monitor, "snapshot", active)
        window.realtime_monitor = monitor
        try:
            window.realtime_page.set_effect_capabilities(
                hud=True,
                health_restore=False,
                health_maintenance=False,
                heat_reduce=False,
                heat_maintenance=False,
                weapon_repeat=True,
            )
            window.realtime_page.show_player_state(72, 100, 35, 200, 1)

            window.realtime_page.weapon_repeat.setChecked(True)

            monitor.enable_weapon_repeat.assert_not_called()
            self.assertTrue(window.battle_plan.weapon_repeat)
            window.battle_plan.resume(monitor, PlayerState(72, 100, 35, 200, 7, 7, 1))
            window._sync_realtime_poll_interval(monitor.snapshot)
            self.assertEqual(window.realtime_poll_timer.interval(), 20)
            self.assertTrue(window.realtime_page.weapon_repeat.isChecked())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_action_error_is_shown_without_crashing_gui(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.restore_health_once.side_effect = RealtimeEffectError("一次回血未授权")
        window.realtime_monitor = monitor
        try:
            window._restore_realtime_health()

            self.assertIn("一次回血未授权", window.realtime_status.text())
            self.assertIn("一次回血失败", window.realtime_event_log.toPlainText())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_write_failure_immediately_resets_disconnected_session(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.restore_health_once.side_effect = RealtimeEffectError("指令指纹失效")
        monitor.snapshot = RealtimeSnapshot(
            "disconnected",
            None,
            False,
            "health 写入失败，已全部停写：指令指纹失效",
        )
        window.realtime_monitor = monitor
        window.realtime_poll_timer.start()
        window.realtime_page.health_maintenance.blockSignals(True)
        window.realtime_page.health_maintenance.setChecked(True)
        window.realtime_page.health_maintenance.blockSignals(False)
        try:
            window._restore_realtime_health()

            self.assertIsNone(window.realtime_monitor)
            self.assertFalse(window.realtime_poll_timer.isActive())
            self.assertFalse(window.realtime_page.health_maintenance.isChecked())
            self.assertIn("指令指纹失效", window.realtime_last_stop_reason.text())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_clearing_realtime_events_clears_the_underlying_history(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        try:
            window._append_realtime_event("旧事件")
            window.realtime_page.clear_events_button.click()
            window._append_realtime_event("新事件")

            text = window.realtime_event_log.toPlainText()
            self.assertNotIn("旧事件", text)
            self.assertIn("新事件", text)
        finally:
            window.close()

    def test_starting_workspace_load_immediately_stops_realtime_session(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        window.realtime_monitor = monitor
        window._realtime_connection_requested = True
        window.realtime_poll_timer.start()
        window.realtime_reconnect_timer.start()
        try:
            with patch.object(window, "_run_async") as run_async:
                window._load_workspace(Path("C:/Game/Other Baldr Force Exe"))

            run_async.assert_called_once()
            monitor.disconnect.assert_called_once_with("正在切换游戏工作区")
            self.assertIsNone(window.realtime_monitor)
            self.assertFalse(window._realtime_connection_requested)
            self.assertFalse(window.realtime_poll_timer.isActive())
            self.assertFalse(window.realtime_reconnect_timer.isActive())
            self.assertIn("正在切换", window.realtime_last_stop_reason.text())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_page_uses_versioned_profile_and_shows_capability_reasons(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.workspace = Mock(
            game_root=Path("C:/Game/Baldr Force Exe"),
            identity=_known_identity(),
        )
        instance = ProcessInstance(
            123,
            456,
            Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
            "x86",
            0x400000,
        )
        monitor = Mock()
        monitor.connect.return_value = RealtimeSnapshot(
            "connected_read_only",
            instance,
            True,
            "只读连接正常",
        )
        try:
            with (
                patch("bfmod.gui.WindowsProcessMemoryAdapter"),
                patch("bfmod.gui.RealtimeMonitor", return_value=monitor) as monitor_type,
            ):
                window._connect_realtime_monitor()

            profile = monitor_type.call_args.args[1]
            self.assertEqual(profile.fingerprints, KNOWN_REALTIME_PROFILE.instruction_fingerprints)
            self.assertIsNotNone(profile.player_state)
            self.assertIn("玩家战斗仪表", window.realtime_capabilities.text())
            self.assertIn("一次回血", window.realtime_capabilities.text())
            self.assertIn("Force Crash 一次补充", window.realtime_capabilities.text())
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertFalse(window.realtime_reduce_heat_button.isEnabled())
            self.assertTrue(window.realtime_page.health_maintenance.isEnabled())
            self.assertTrue(window.realtime_page.heat_maintenance.isEnabled())
            self.assertNotIn("验证", window.realtime_capabilities.text())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_actions_wait_for_valid_player_state_and_initialize_safe_targets(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        try:
            window.realtime_page.set_effect_capabilities(
                hud=True,
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
                force_crash_refill=True,
            )
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertFalse(window.realtime_reduce_heat_button.isEnabled())
            self.assertTrue(window.realtime_page.health_maintenance.isEnabled())
            self.assertTrue(window.realtime_page.heat_maintenance.isEnabled())

            window.realtime_page.show_player_state(72, 100, 35, 200, 1, 7500, 10000)
            self.assertTrue(window.realtime_restore_health_button.isEnabled())
            self.assertTrue(window.realtime_reduce_heat_button.isEnabled())
            self.assertTrue(window.realtime_page.health_maintenance.isEnabled())
            self.assertTrue(window.realtime_page.heat_maintenance.isEnabled())
            self.assertTrue(window.realtime_page.refill_force_crash_button.isEnabled())
            self.assertIn(
                "一次充满",
                window.realtime_force_crash_bar.toolTip(),
            )
            self.assertEqual(window.realtime_page.health_target.value(), 0)
            self.assertEqual(window.realtime_page.heat_once_target.value(), 0)
            self.assertEqual(window.realtime_page.heat_target.value(), 0)

            window.realtime_page.show_player_state(
                72,
                100,
                35,
                200,
                1,
                7500,
                10000,
                True,
            )
            self.assertIn("发动中", window.realtime_page.hud_status.text())
            self.assertEqual(window.realtime_force_crash_value.text(), "7500 / 10000")
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertFalse(window.realtime_reduce_heat_button.isEnabled())
            self.assertFalse(window.realtime_page.refill_force_crash_button.isEnabled())
            self.assertTrue(window.realtime_page.health_maintenance.isEnabled())
            self.assertTrue(window.realtime_page.heat_maintenance.isEnabled())

            window.realtime_page.show_player_state(72, 100, 35, 200, 1, 7500, 10000)
            self.assertTrue(window.realtime_restore_health_button.isEnabled())
            self.assertTrue(window.realtime_page.refill_force_crash_button.isEnabled())

            window.realtime_page.health_target.setValue(80)
            window.realtime_page.heat_once_target.setValue(40)
            window.realtime_page.heat_target.setValue(30)
            window.realtime_page.show_player_state(65, 100, 50, 200, 1)
            self.assertEqual(window.realtime_page.health_target.value(), 80)
            self.assertEqual(window.realtime_page.heat_once_target.value(), 40)
            self.assertEqual(window.realtime_page.heat_target.value(), 30)

            window.realtime_page.clear_player_state("场景已失效")
            self.assertEqual(window.realtime_force_crash_value.text(), "— / —")
            self.assertEqual(window.realtime_force_crash_bar.value(), 0)
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertTrue(window.realtime_page.health_maintenance.isEnabled())
            self.assertFalse(window.realtime_page.refill_force_crash_button.isEnabled())
            window.realtime_page.show_player_state(120, 150, 10, 200, 1)
            self.assertEqual(window.realtime_page.health_target.value(), 80)
            self.assertEqual(window.realtime_page.heat_once_target.value(), 40)
            self.assertEqual(window.realtime_page.heat_target.value(), 30)
        finally:
            window.close()

    def test_changing_active_maintenance_target_updates_the_monitor(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot(
            "connected_read_only",
            ProcessInstance(
                123,
                456,
                Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
                "x86",
                0x400000,
            ),
            True,
            "只读连接正常",
            ("health", "heat"),
        )
        window.realtime_monitor = monitor
        try:
            window.realtime_page.set_effect_capabilities(
                hud=True,
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
            )
            window.realtime_page.show_player_state(72, 100, 35, 200, 1)
            window.realtime_page.health_maintenance.setChecked(True)
            window.realtime_page.heat_maintenance.setChecked(True)
            monitor.enable_health_maintenance.reset_mock()
            monitor.enable_heat_maintenance.reset_mock()

            window.realtime_page.health_target.setValue(80)
            window.realtime_page.heat_target.setValue(20)

            monitor.enable_health_maintenance.assert_not_called()
            self.assertEqual(window.battle_plan.health, 80)
            monitor.enable_heat_maintenance.assert_not_called()
            self.assertEqual(window.battle_plan.heat, 20)
            monitor.stop_effects.assert_called()
            self.assertIn("方案已更新", window.realtime_event_log.toPlainText())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_failed_active_target_update_stops_the_old_effect(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.enable_health_maintenance.side_effect = RealtimeEffectError("场景正在切换")
        active_snapshot = RealtimeSnapshot(
            "connected_read_only",
            ProcessInstance(
                123,
                456,
                Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
                "x86",
                0x400000,
            ),
            True,
            "只读连接正常",
            ("health",),
        )
        idle_snapshot = RealtimeSnapshot(
            "connected_read_only",
            active_snapshot.instance,
            True,
            "血量维持已停用",
            (),
        )
        monitor.snapshot = active_snapshot
        monitor.stop_health_maintenance.side_effect = lambda: (
            setattr(monitor, "snapshot", idle_snapshot) or idle_snapshot
        )
        window.realtime_monitor = monitor
        window._sync_realtime_poll_interval(active_snapshot)
        try:
            window.realtime_page.set_effect_capabilities(
                hud=True,
                health_restore=True,
                health_maintenance=True,
                heat_reduce=False,
                heat_maintenance=False,
            )
            window.realtime_page.show_player_state(72, 100, 35, 200, 1)
            window.realtime_page.health_maintenance.blockSignals(True)
            window.realtime_page.health_maintenance.setChecked(True)
            window.realtime_page.health_maintenance.blockSignals(False)

            window.realtime_page.health_target.setValue(80)

            monitor.stop_effects.assert_called_once_with()
            monitor.snapshot = idle_snapshot
            monitor.tick_effects.return_value = idle_snapshot
            monitor.read_player_state.side_effect = RealtimeEffectError("场景正在切换")
            with patch.object(window, "_realtime_hud_available", return_value=True):
                window._poll_realtime_monitor()
            self.assertFalse(window.realtime_page.health_maintenance.isChecked())
            self.assertIn("场景正在切换", window.realtime_status.text())
            self.assertFalse(window.battle_plan.effects)
        finally:
            window.realtime_monitor = None
            window.close()

    def test_realtime_waits_for_supported_battle_without_disconnect(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        instance = ProcessInstance(
            123,
            456,
            Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
            "x86",
            0x400000,
        )
        monitor = Mock()
        monitor.tick_effects.return_value = RealtimeSnapshot(
            "connected_read_only",
            instance,
            True,
            "只读连接正常",
        )
        monitor.read_player_state.side_effect = RealtimePlayerUnavailableError(
            "当前不在普通或练习战斗中"
        )
        monitor.snapshot = monitor.tick_effects.return_value
        window.realtime_monitor = monitor
        window.realtime_poll_timer.start()
        try:
            with patch.object(window, "_realtime_hud_available", return_value=True):
                window._poll_realtime_monitor()

            self.assertIs(window.realtime_monitor, monitor)
            self.assertTrue(window.realtime_poll_timer.isActive())
            self.assertIn("等待进入", window.realtime_page.hud_status.text())
            self.assertIn("等待进入战斗", window.realtime_status.text())
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_force_crash_refill_action_updates_hud_and_event_log(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        monitor.refill_force_crash_once.return_value = PlayerState(
            72,
            100,
            35,
            200,
            7,
            7,
            1,
            10000,
            10000,
        )
        window.realtime_monitor = monitor
        try:
            window._refill_realtime_force_crash()

            monitor.refill_force_crash_once.assert_called_once_with()
            self.assertEqual(window.realtime_force_crash_value.text(), "10000 / 10000")
            self.assertEqual(window.realtime_force_crash_bar.value(), 100)
            self.assertIn("Force Crash 补充完成", window.realtime_event_log.toPlainText())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_experimental_writes_are_visible_but_not_enabled_in_normal_gui(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.workspace = Mock(
            game_root=Path("C:/Game/Baldr Force Exe"),
            identity=_known_identity(),
        )
        experimental = RealtimeProfileDefinition(
            id="experimental-test",
            display_name="实验测试 Profile",
            required_installation_fingerprints=tuple(KNOWN_FINGERPRINTS.items()),
            architecture="x86",
            instruction_fingerprints=KNOWN_REALTIME_PROFILE.instruction_fingerprints,
            supported_capabilities=(
                ProfileCapabilitySupport("realtime_connection", "read_only"),
                ProfileCapabilitySupport("realtime_player_hud", "read_only"),
                ProfileCapabilitySupport("realtime_health_restore", "experimental_write"),
                ProfileCapabilitySupport("realtime_heat_reduce", "verified_write"),
            ),
            evidence_source="测试",
            verified_date="2026-09-21",
            read_validation="测试只读证据",
            write_validation="测试写入证据",
            known_failures=(),
        )
        selected = SelectedRealtimeProfile(experimental, Mock(player_state=object()))
        try:
            with patch("bfmod.gui.select_realtime_profile", return_value=selected):
                window._refresh_realtime_capabilities()

            self.assertNotIn("一次回血", window.realtime_capabilities.text())
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertFalse(window.realtime_reduce_heat_button.isEnabled())
            window.realtime_page.show_player_state(72, 100, 35, 200, 1)
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertTrue(window.realtime_reduce_heat_button.isEnabled())
        finally:
            window.close()

    def test_realtime_connection_is_disabled_without_matching_profile(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.workspace = Mock(
            game_root=Path("C:/Game/Baldr Force Exe"),
            identity=_unknown_identity(),
        )
        try:
            with patch("bfmod.gui.RealtimeMonitor") as monitor_type:
                window._connect_realtime_monitor()

            monitor_type.assert_not_called()
            self.assertFalse(window.realtime_poll_timer.isActive())
            self.assertIn("不支持实时战斗", window.realtime_status.text())
        finally:
            window.close()

    def test_reconnect_invalidates_the_previous_player_snapshot_before_connecting(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.workspace = Mock(
            game_root=Path("C:/Game/Baldr Force Exe"),
            identity=_known_identity(),
        )
        monitor = Mock()
        monitor.connect.side_effect = RealtimeEffectError("游戏尚未启动")
        try:
            window._refresh_realtime_capabilities()
            window.realtime_page.show_player_state(72, 100, 35, 200, 1)
            self.assertTrue(window.realtime_restore_health_button.isEnabled())

            with (
                patch("bfmod.gui.WindowsProcessMemoryAdapter"),
                patch("bfmod.gui.RealtimeMonitor", return_value=monitor),
            ):
                window._connect_realtime_monitor()

            self.assertEqual(window.realtime_health_value.text(), "— / —")
            self.assertEqual(window.realtime_heat_value.text(), "— / —")
            self.assertFalse(window.realtime_restore_health_button.isEnabled())
            self.assertFalse(window.realtime_reduce_heat_button.isEnabled())
        finally:
            window.close()

    def test_loading_another_workspace_disconnects_existing_realtime_session(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        monitor = Mock()
        window.realtime_monitor = monitor
        window.realtime_poll_timer.start()
        new_workspace = GameWorkspace(
            game_root=Path("C:/Game/Other Baldr Force Exe"),
            reference_table=Path("C:/Game/Other Baldr Force Exe/BFWT.htm"),
            identity=_unknown_identity(),
            catalog=(),
            force_crash_catalog=(),
            waz=Mock(),
            slots=(),
            slot_failures=(),
        )
        try:
            with (
                patch.object(window.settings, "setValue"),
                patch.object(window.game_root_edit, "setText"),
                patch.object(window, "_refresh_backups"),
                patch.object(window, "_refresh_diagnostics"),
            ):
                window._workspace_loaded(new_workspace)

            monitor.disconnect.assert_called_once_with("游戏工作区已切换")
            self.assertIsNone(window.realtime_monitor)
            self.assertFalse(window.realtime_poll_timer.isActive())
            self.assertFalse(window.realtime_connect_button.isEnabled())
            self.assertIn("游戏工作区已切换", window.realtime_last_stop_reason.text())
        finally:
            window.realtime_monitor = None
            window.close()

    def test_restore_preview_identifies_different_current_and_backup_files(self) -> None:
        text = build_restore_preview(
            slot_name="Save002.dat",
            current_size=1234,
            current_sha256="A" * 64,
            backup_size=1200,
            backup_sha256="B" * 64,
        )

        self.assertIn("Save002.dat", text)
        self.assertIn("当前槽位：1,234 B", text)
        self.assertIn("备份槽位：1,200 B", text)
        self.assertIn("A" * 64, text)
        self.assertIn("B" * 64, text)
        self.assertIn("不同", text)
        self.assertIn("整个 Save", text)
        self.assertIn("只覆盖 Save002.dat", text)

    def test_restore_preview_identifies_identical_files(self) -> None:
        text = build_restore_preview(
            slot_name="Save001.dat",
            current_size=100,
            current_sha256="c" * 64,
            backup_size=100,
            backup_sha256="C" * 64,
        )

        self.assertIn("完全相同", text)

    def test_restore_uses_the_backup_hash_confirmed_in_the_preview(self) -> None:
        with TemporaryDirectory() as temporary, patch.object(MainWindow, "_load_initial_workspace"):
            root = Path(temporary)
            slot_path = root / "Save002.dat"
            slot_path.write_bytes(b"current")
            slot = Mock(path=slot_path)
            workspace = Mock(slots=(slot,), game_root=root)
            backup = BackupInfo(root / "backup", "now", "Save", 1, "", "test")
            backup_file = BackupFileInfo(slot_path.name, 6, "B" * 64)
            window = MainWindow()
            window.workspace = workspace
            window.restore_slot_combo.clear()
            window.restore_slot_combo.addItem(slot_path.name, 0)

            try:
                with (
                    patch.object(window, "_selected_backup", return_value=backup),
                    patch("bfmod.gui.list_backup_files", return_value=(backup_file,)),
                    patch(
                        "bfmod.gui.QMessageBox.warning",
                        return_value=QMessageBox.StandardButton.Yes,
                    ),
                    patch("bfmod.gui.restore_slot") as restore,
                    patch.object(
                        window,
                        "_run_async",
                        side_effect=lambda _message, operation, _success: operation(),
                    ),
                ):
                    window._restore_backup()

                self.assertEqual(
                    restore.call_args.kwargs["expected_backup_sha256"],
                    backup_file.sha256,
                )
            finally:
                window.close()


def _known_identity() -> InstallationIdentity:
    return InstallationIdentity(
        "known",
        dict(KNOWN_FINGERPRINTS),
        True,
        True,
        True,
        ("matched",),
    )


def _unknown_identity() -> InstallationIdentity:
    return InstallationIdentity(
        "unknown",
        {"BaldrForce.exe": "0" * 64},
        False,
        True,
        False,
        ("文件指纹不匹配",),
    )


if __name__ == "__main__":
    unittest.main()
