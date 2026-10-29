from __future__ import annotations

import ctypes
import json
import unittest
from ctypes import wintypes
from dataclasses import replace
from unittest.mock import Mock, patch

from bfmod.battle_plan import BattlePlan
from bfmod.global_hotkeys import GlobalHotkeys
from bfmod.gui import MainWindow, create_application
from bfmod.realtime import PlayerState, RealtimeEffectError, RealtimeSnapshot


class BattlePlanTests(unittest.TestCase):
    def test_saved_plan_validates_types_ranges_and_unknown_keys(self) -> None:
        plan = BattlePlan(health=100, full_health=True, heat=0, weapon_repeat=True)
        self.assertEqual(BattlePlan.from_json(plan.to_json()), plan)
        for change in (
            {"health": True},
            {"heat": -1},
            {"weapon_repeat": "yes"},
            {"unknown": 1},
            {"health": 1_000_001},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                BattlePlan.from_json(json.dumps(json.loads(plan.to_json()) | change))

    def test_paused_dead_or_other_scene_does_not_rearm_practice_plan(self) -> None:
        monitor = Mock()
        plan = BattlePlan(health=100, full_health=True, practice_only=True)
        player = PlayerState(50, 100, 30, 130, 7, 7, 3, scene_name="练习战斗")
        for state in (
            replace(player, health=0),
            replace(player, write_inhibited=True),
            replace(player, scene_name="普通战斗"),
        ):
            self.assertFalse(plan.resume(monitor, state))
        monitor.enable_practice_effects.assert_not_called()

    def test_partial_plan_failure_revokes_already_enabled_effects(self) -> None:
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot("connected_read_only", None, True, "", ())
        monitor.enable_heat_maintenance.side_effect = RealtimeEffectError("scene changed")
        with self.assertRaises(RealtimeEffectError):
            BattlePlan(health=100, heat=0).resume(monitor, PlayerState(50, 100, 30, 130, 7, 7, 1))
        monitor.enable_health_maintenance.assert_called_once_with(100)
        monitor.stop_effects.assert_called_once_with()

    def test_old_saved_plan_loads_without_silently_enabling_fc_lock(self) -> None:
        old = json.loads(BattlePlan(heat=0).to_json())
        old.pop("force_crash")
        self.assertFalse(BattlePlan.from_json(json.dumps(old)).force_crash)
        old["force_crash"] = "yes"
        with self.assertRaises(ValueError):
            BattlePlan.from_json(json.dumps(old))


class BattleAssistantGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or create_application(["assistant-test"])

    def setUp(self) -> None:
        with patch.object(MainWindow, "_load_initial_workspace"):
            self.window = MainWindow()
        self.window.realtime_page.remember_effects.setChecked(True)

    def tearDown(self) -> None:
        self.window.realtime_monitor = None
        self.window.close()

    def test_plan_survives_transition_then_rearms_but_emergency_stop_revokes_it(self) -> None:
        window = self.window
        window.battle_plan = BattlePlan(health=100, full_health=True, heat=25)
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot("connected_read_only", Mock(pid=42), True, "", ())
        monitor.tick_effects.return_value = monitor.snapshot
        monitor.read_player_state.return_value = PlayerState(150, 200, 10, 130, 7, 7, 1)
        window.realtime_monitor = monitor
        with (
            patch.object(window, "_realtime_hud_available", return_value=True),
            patch.object(window, "_realtime_weapon_state_available", return_value=False),
        ):
            window._poll_realtime_monitor()
        monitor.enable_health_maintenance.assert_called_once_with(200)
        monitor.enable_heat_maintenance.assert_called_once_with(25)
        self.assertTrue(window.realtime_page.health_maintenance.isChecked())
        self.assertEqual(window.realtime_page.heat_target.value(), 25)
        window._toggle_battle_pause()
        self.assertTrue(window._battle_plan_paused)
        self.assertEqual(window.battle_plan.effects, ("health", "heat"))
        window._stop_realtime_monitor()
        self.assertFalse(window.battle_plan.effects)
        self.assertFalse(window.realtime_reconnect_timer.isActive())

    def test_hud_mirrors_disabled_actions_without_stealing_focus(self) -> None:
        from PySide6.QtCore import Qt

        window = self.window
        self.assertTrue(window.battle_hud.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        window.realtime_page.show_player_state(50, 100, 30, 130, 1)
        window._sync_battle_assistant()
        self.assertEqual(window.battle_hud.bars["health"].value(), 50)
        self.assertFalse(window.battle_hud.action_buttons["heal"].isEnabled())
        window.realtime_page.show_hud.setChecked(True)
        self.assertTrue(window.battle_hud.isVisible())
        window.battle_hud.close()
        self.assertFalse(window.realtime_page.show_hud.isChecked())

    def test_only_normal_process_exit_preserves_armed_plan(self) -> None:
        for recoverable in (True, False):
            self.window.battle_plan = BattlePlan(heat=0)
            monitor = Mock()
            snapshot = RealtimeSnapshot("disconnected", None, False, "exit", (), recoverable)
            self.window._synchronize_realtime_disconnect(monitor, snapshot)
            self.assertEqual(bool(self.window.battle_plan.effects), recoverable)

    def _enable_battle_controls(self) -> Mock:
        page = self.window.realtime_page
        page.set_effect_capabilities(
            hud=True,
            health_restore=True,
            health_maintenance=True,
            heat_reduce=True,
            heat_maintenance=True,
            weapon_repeat=True,
        )
        page.show_player_state(50, 100, 30, 130, 1)
        monitor = Mock()
        monitor.snapshot = RealtimeSnapshot("connected_read_only", None, True, "", ())
        self.window.realtime_monitor = monitor
        self.window._sync_battle_assistant()
        return monitor

    def test_hud_toggles_and_targets_share_main_page_actions_without_feedback(self) -> None:
        monitor = self._enable_battle_controls()
        hud = self.window.battle_hud
        hud.effect_checks["health"].click()
        monitor.enable_health_maintenance.assert_not_called()
        self.assertTrue(self.window.battle_plan.full_health)
        self.assertTrue(self.window.realtime_page.health_maintenance.isChecked())
        hud.target_controls["health"].setValue(80)
        self.assertEqual(self.window.battle_plan.health, 80)
        self.assertFalse(self.window.battle_plan.full_health)
        monitor.enable_health_maintenance.assert_not_called()
        self.window._sync_battle_assistant()
        self.window._sync_battle_assistant()
        monitor.enable_health_maintenance.assert_not_called()
        hud.effect_checks["weapon_repeat"].click()
        monitor.enable_weapon_repeat.assert_not_called()
        self.assertTrue(self.window.battle_plan.weapon_repeat)
        hud.target_controls["heat_once"].setValue(25)
        self.assertEqual(self.window.realtime_page.heat_once_target.value(), 25)

    def test_paused_hud_edits_change_plan_without_enabling_any_effect(self) -> None:
        monitor = self._enable_battle_controls()
        self.window.battle_plan = BattlePlan(health=100, full_health=True, heat=0)
        self.window._battle_plan_paused = True
        self.window._sync_battle_assistant()
        hud = self.window.battle_hud
        hud.target_controls["health"].setValue(70)
        hud.effect_checks["heat"].click()
        hud.effect_checks["weapon_repeat"].click()
        self.assertTrue(self.window._battle_plan_paused)
        self.assertEqual(self.window.battle_plan.health, 70)
        self.assertIsNone(self.window.battle_plan.heat)
        self.assertTrue(self.window.battle_plan.weapon_repeat)
        monitor.enable_health_maintenance.assert_not_called()
        monitor.enable_heat_maintenance.assert_not_called()
        monitor.enable_weapon_repeat.assert_not_called()

    def test_hidden_hud_stops_native_layer_timer(self) -> None:
        hud = self.window.battle_hud
        self.window.realtime_page.show_hud.setChecked(True)
        self.assertTrue(hud._layer_timer.isActive())
        hud.close()
        self.assertFalse(hud._layer_timer.isActive())

    def test_no_focus_hud_spin_button_can_adjust_target(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox

        self._enable_battle_controls()
        hud = self.window.battle_hud
        self.window.realtime_page.show_hud.setChecked(True)
        target = hud.target_controls["health"]
        target.setValue(50)
        option = QStyleOptionSpinBox()
        target.initStyleOption(option)
        rectangle = target.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox,
            option,
            QStyle.SubControl.SC_SpinBoxUp,
            target,
        )
        QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=rectangle.center())
        self.assertEqual(target.value(), 150)
        self.assertEqual(self.window.realtime_page.health_target.value(), 150)
        self.assertEqual(target.focusPolicy(), Qt.FocusPolicy.NoFocus)


class GlobalHotkeyTests(unittest.TestCase):
    def test_conflict_dispatch_and_unregister_only_owned_shortcuts(self) -> None:
        actions = {key: Mock() for key in ("heal", "cool", "fc", "pause", "hud", "fc_lock", "stop")}
        hotkeys = GlobalHotkeys(actions)
        backend = Mock()
        backend.RegisterHotKey.side_effect = (True, False, True, True, True, True, True)
        hotkeys._user32 = backend
        try:
            failures = hotkeys.start()
            self.assertEqual(len(failures), 1)
            self.assertIn("F7", failures[0])
            message = wintypes.MSG()
            message.message = 0x0312
            message.wParam = 0x4BF0
            with patch(
                "bfmod.global_hotkeys.QTimer.singleShot", side_effect=lambda _, call: call()
            ):
                self.assertEqual(
                    hotkeys.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)),
                    (True, 0),
                )
            actions["heal"].assert_called_once_with()
            message.wParam = 0x4BF5
            with patch(
                "bfmod.global_hotkeys.QTimer.singleShot", side_effect=lambda _, call: call()
            ):
                self.assertEqual(
                    hotkeys.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)),
                    (True, 0),
                )
            actions["fc_lock"].assert_called_once_with()
        finally:
            hotkeys.stop()
        self.assertEqual(backend.UnregisterHotKey.call_count, 6)
