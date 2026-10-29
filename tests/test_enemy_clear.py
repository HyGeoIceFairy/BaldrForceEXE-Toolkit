from __future__ import annotations

import json
import struct
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from bfmod.battle_plan import BattlePlan
from bfmod.combat import EnemyClearLayout
from bfmod.process_memory import FakeProcessMemoryAdapter, ProcessMemoryError
from bfmod.realtime import (
    AddressPath,
    BattleSceneSignature,
    EffectPermissions,
    InstructionFingerprint,
    IntegerField,
    PlayerStateLayout,
    RealtimeEffectError,
    RealtimeMonitor,
    RealtimeProfile,
    SegmentedHealthLayout,
)


class CombatFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FakeProcessMemoryAdapter()
        self.raw = bytearray(0x20000)
        self.raw[:4] = b"code"
        struct.pack_into("<I", self.raw, 0x180, 0x400200)
        for slot, team in enumerate((0, 0, 1, 2, 3, 1)):
            root = 0x1000 + slot * 0x4000
            struct.pack_into("<I", self.raw, 0x100 + slot * 4, 0x400000 + root)
            self.raw[root : root + 2] = b"\x01\x05"
            struct.pack_into("<I", self.raw, root + 0x38, 0x401000 + root)
            struct.pack_into("<I", self.raw, root + 0x68, 0x402000 + root)
            struct.pack_into("<3h", self.raw, root + 0x1016, 2080, 2, 2)
            struct.pack_into("<2h", self.raw, root + 0x1020, 0, 130)
            self.raw[root + 0x2003] = team
            struct.pack_into("<2I", self.raw, root + 0x2004, slot, slot * 10)
        self.instance = self.adapter.start_process("C:/test/BaldrForce.exe", bytes(self.raw))
        self.layout = PlayerStateLayout(
            health=IntegerField(0x16, 2, 0, 2080),
            max_health=IntegerField(0x1A, 2, 0, 31),
            heat=IntegerField(0x20, 2, 0, 130),
            max_heat=IntegerField(0x22, 2, 130, 130),
            player_identity=IntegerField(4, 4, 0, 0, path=AddressPath(0x100, (0x68, 0))),
            object_identity=IntegerField(8, 4, 0, 0, path=AddressPath(0x100, (0x68, 0))),
            battle_state=IntegerField(0x40, 1, 0, 3, path=AddressPath()),
            battle_scenes=(
                BattleSceneSignature("普通战斗", 0),
                BattleSceneSignature("练习战斗", 3),
            ),
            root=AddressPath(0x100, (0x38, 0)),
            segmented_health=SegmentedHealthLayout(IntegerField(0x18, 2, -1, 31), 2080),
            write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),),
        )
        self.profile = RealtimeProfile(
            Path("C:/test/BaldrForce.exe"),
            "x86",
            (InstructionFingerprint("code", 0, b"code"),),
            self.layout,
            permissions=EffectPermissions(enemy_clear=True, enemy_clear_maintenance=True),
            enemy_clear=EnemyClearLayout(0x100, 0x180),
        )
        self.monitor = RealtimeMonitor(self.adapter, self.profile)
        self.monitor.connect()

    def timer_offset(self, slot: int) -> int:
        return 0x214E + slot * 0x4000

    @property
    def memory(self) -> bytearray:
        return self.adapter._state(self.instance).memory


class EnemyClearTests(CombatFixture):

    def test_only_hostile_native_timer_bytes_change(self) -> None:
        before = bytes(self.memory)
        result = self.monitor.clear_enemies_once()
        self.assertEqual((result.requested, result.protected), (2, 0))
        changed = {
            index for index, (a, b) in enumerate(zip(before, self.memory, strict=True)) if a != b
        }
        self.assertEqual(changed, {self.timer_offset(2), self.timer_offset(5)})
        self.assertEqual(self.monitor.snapshot.state, "connected_read_only")

    def test_script_protected_target_is_not_changed(self) -> None:
        self.memory[0x201] = 3
        struct.pack_into("<I", self.memory, 0x204, 2)
        result = self.monitor.clear_enemies_once()
        self.assertEqual((result.requested, result.protected), (1, 1))
        self.assertEqual(self.memory[self.timer_offset(2)], 0)

    def test_dead_inactive_or_already_pending_enemies_are_skipped(self) -> None:
        for condition in ("dead", "inactive", "pending"):
            with self.subTest(condition=condition):
                self.memory[:] = self.raw
                if condition == "dead":
                    struct.pack_into("<2h", self.memory, 0xA016, 0, -1)
                elif condition == "inactive":
                    self.memory[0x9000] = 0
                else:
                    struct.pack_into("<h", self.memory, self.timer_offset(2), 10)
                self.assertEqual(self.monitor.clear_enemies_once().requested, 1)

    def test_practice_menu_death_and_fc_inhibitor_never_write(self) -> None:
        for scene, inhibited, dead in ((3, 0, False), (1, 0, False), (0, 1, False), (0, 0, True)):
            with self.subTest(scene=scene, inhibited=inhibited, dead=dead):
                self.memory[:] = self.raw
                self.memory[0x40] = scene
                self.memory[0x41] = inhibited
                if dead:
                    struct.pack_into("<2h", self.memory, 0x2016, 0, -1)
                with self.assertRaises(RealtimeEffectError):
                    self.monitor.clear_enemies_once()
                self.assertEqual(self.memory[self.timer_offset(2)], 0)

    def test_permissions_are_independent_and_cannot_be_inferred_from_layout(self) -> None:
        for permissions, layout in (
            (EffectPermissions(enemy_clear=True), None),
            (EffectPermissions(), EnemyClearLayout()),
        ):
            with self.assertRaises(ValueError):
                replace(self.profile, permissions=permissions, enemy_clear=layout)
        with self.assertRaises(ValueError):
            EffectPermissions(enemy_clear_maintenance=True)
        monitor = RealtimeMonitor(
            self.adapter, replace(self.profile, permissions=EffectPermissions(), enemy_clear=None)
        )
        monitor.connect()
        with self.assertRaisesRegex(RealtimeEffectError, "未授权"):
            monitor.clear_enemies_once()

    def test_invalid_identity_shared_components_or_pointer_stops_writes(self) -> None:
        for offset, value in ((0xB004, 4), (0x9038, 0x402000), (0x9068, 12)):
            with self.subTest(offset=offset):
                self.memory[:] = self.raw
                self.monitor.connect()
                struct.pack_into("<I", self.memory, offset, value)
                with self.assertRaises(RealtimeEffectError):
                    self.monitor.clear_enemies_once()
                self.assertEqual(self.monitor.snapshot.state, "disconnected")
                self.assertEqual(self.memory[self.timer_offset(5)], 0)

    def test_writer_revalidates_team_and_script_protection(self) -> None:
        original = self.adapter.open_process
        for mutation in ("team", "event", "scene", "signature"):
            with self.subTest(mutation=mutation):
                self.memory[:] = self.raw
                self.monitor.connect()

                def changed(instance, *, writable=False, mutation=mutation):
                    if writable:
                        if mutation == "team":
                            self.memory[0xB003] = 0
                        elif mutation == "event":
                            self.memory[0x201] = 3
                            struct.pack_into("<I", self.memory, 0x204, 2)
                        elif mutation == "scene":
                            self.memory[0x40] = 1
                        else:
                            self.memory[0] = 0
                    return original(instance, writable=writable)

                with patch.object(self.adapter, "open_process", changed):
                    try:
                        self.monitor.clear_enemies_once()
                    except RealtimeEffectError:
                        self.assertIn(mutation, ("scene", "signature"))
                self.assertEqual(self.memory[self.timer_offset(2)], 0)

    def test_native_removal_can_free_object_before_readback(self) -> None:
        original = self.adapter.open_process

        def opened(instance, *, writable=False):
            handle = original(instance, writable=writable)
            if writable:
                write = handle.write

                def native_removal(address, data):
                    write(address, data)
                    slot = 2 if address == 0x400000 + self.timer_offset(2) else 5
                    struct.pack_into("<I", self.memory, 0x100 + slot * 4, 0)
                    root = 0x1000 + slot * 0x4000
                    self.memory[root : root + 0x3000] = b"\xff" * 0x3000

                handle.write = native_removal
            return handle

        with patch.object(self.adapter, "open_process", opened):
            self.assertEqual(self.monitor.clear_enemies_once().requested, 2)
        self.assertEqual(self.monitor.snapshot.state, "connected_read_only")

    def test_write_failure_revokes_entire_plan(self) -> None:
        self.monitor.enable_enemy_clear_maintenance()
        self.adapter._state(self.instance).writable = False
        self.monitor.tick_effects()
        self.assertEqual(self.monitor.snapshot.state, "disconnected")
        self.assertFalse(self.monitor.snapshot.active_effects)

    def test_new_enemy_is_found_by_slot_and_emergency_stop_prevents_more_writes(self) -> None:
        self.monitor.enable_enemy_clear_maintenance()
        self.monitor.tick_effects()
        struct.pack_into("<h", self.memory, self.timer_offset(2), 0)
        self.monitor.tick_effects()
        self.assertEqual(self.memory[self.timer_offset(2)], 1)
        self.monitor.stop_effects()
        struct.pack_into("<h", self.memory, self.timer_offset(2), 0)
        self.monitor.tick_effects()
        self.assertEqual(self.memory[self.timer_offset(2)], 0)

    def test_process_exit_or_pid_reuse_cannot_reuse_old_addresses(self) -> None:
        self.monitor.enable_enemy_clear_maintenance()
        self.adapter.terminate(self.instance)
        new = self.adapter.start_process(
            "C:/test/BaldrForce.exe", bytes(self.raw), pid=self.instance.pid
        )
        self.monitor.tick_effects()
        self.assertFalse(self.monitor.snapshot.active_effects)
        self.assertEqual(self.adapter._state(new).memory[self.timer_offset(2)], 0)

    def test_short_read_is_not_interpreted_as_empty_battle(self) -> None:
        self.adapter._state(self.instance).maximum_transfer = 4
        with self.assertRaises(RealtimeEffectError):
            self.monitor.clear_enemies_once()
        self.assertEqual(self.memory[self.timer_offset(2)], 0)

    def test_only_manual_saved_plan_load_can_enable_new_option(self) -> None:
        old = json.loads(BattlePlan(heat=0).to_json())
        old.pop("enemy_clear")
        self.assertFalse(BattlePlan.from_json(json.dumps(old)).enemy_clear)
        player = self.monitor.read_player_state()
        plan = BattlePlan(enemy_clear=True)
        self.assertTrue(plan.resume(self.monitor, player))
        self.assertIn("enemy_clear", self.monitor.snapshot.active_effects)
        monitor = Mock(snapshot=self.monitor.snapshot)
        self.assertFalse(plan.resume(monitor, replace(player, scene_name="练习战斗")))
        monitor.enable_enemy_clear_maintenance.assert_not_called()

    def test_partial_write_error_does_not_continue_to_next_target(self) -> None:
        original = self.adapter.open_process

        def opened(instance, *, writable=False):
            handle = original(instance, writable=writable)
            if writable:

                def failed(address, data):
                    self.memory[address - 0x400000] = 1
                    raise ProcessMemoryError("short write")

                handle.write = failed
            return handle

        with (
            patch.object(self.adapter, "open_process", opened),
            self.assertRaises(RealtimeEffectError),
        ):
            self.monitor.clear_enemies_once()
        self.assertEqual(self.memory[self.timer_offset(5)], 0)
        self.assertEqual(self.monitor.snapshot.state, "disconnected")


if __name__ == "__main__":
    unittest.main()
