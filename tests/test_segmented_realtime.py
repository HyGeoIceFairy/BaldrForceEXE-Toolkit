from __future__ import annotations

import struct
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bfmod.battle_plan import BattlePlan
from bfmod.process_memory import FakeProcessMemoryAdapter, ProcessMemoryError
from bfmod.realtime import (
    AddressPath,
    BattleSceneSignature,
    EffectPermissions,
    ForceCrashWriteLayout,
    HeatWriteLayout,
    InstructionFingerprint,
    IntegerField,
    PlayerStateLayout,
    RealtimeEffectError,
    RealtimeFrameUnstableError,
    RealtimeMonitor,
    RealtimePlayerUnavailableError,
    RealtimeProfile,
    SegmentedHealthLayout,
    SegmentedHealthWriteLayout,
    WeaponRepeatWriteLayout,
    WeaponStateLayout,
)


class SegmentedRealtimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FakeProcessMemoryAdapter()
        raw = bytearray(0x700)
        raw[:4] = b"code"
        struct.pack_into("<I", raw, 0x80, 0x400100)
        struct.pack_into("<I", raw, 0x110, 0x400200)
        struct.pack_into("<I", raw, 0x114, 0x400300)
        struct.pack_into("<3h", raw, 0x200, 100, 2, 2)
        struct.pack_into("<2h", raw, 0x210, 30, 130)
        struct.pack_into("<h", raw, 0x23A, 7500)
        struct.pack_into("<2i", raw, 0x300, 7, 7)
        raw[0x23C] = 1
        raw[0x25C:0x269] = bytes((1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1))
        raw[0x308] = 3
        struct.pack_into("<25h", raw, 0x314, 0, 69, 0, *([-999] * 22))
        raw[0x346] = 3
        struct.pack_into("<I", raw, 0x364, 0x400400)
        struct.pack_into("<I", raw, 0x508, 0x400600)
        struct.pack_into("<13h", raw, 0x600, 0, 1, 2, 69, 70, 71, 6, 7, 8, 96, 97, 98, 120)
        raw[0x40] = 1
        self.instance = self.adapter.start_process("C:/game/BaldrForce.exe", bytes(raw))
        self.layout = PlayerStateLayout(
            health=IntegerField(0, 2, 0, 100),
            max_health=IntegerField(4, 2, 0, 10),
            heat=IntegerField(0x10, 2, 0, 130),
            max_heat=IntegerField(0x12, 2, 130, 130),
            player_identity=IntegerField(0, 4, 7, 7, path=AddressPath(0x80, (0x14, 0))),
            object_identity=IntegerField(4, 4, 0, 100, path=AddressPath(0x80, (0x14, 0))),
            battle_state=IntegerField(0x40, 1, 0, 1, path=AddressPath()),
            battle_scenes=(BattleSceneSignature("test", 1),),
            root=AddressPath(0x80, (0x10, 0)),
            segmented_health=SegmentedHealthLayout(IntegerField(2, 2, -1, 10), 100),
            force_crash=IntegerField(0x3A, 2, 0, 10000),
        )
        self.profile = RealtimeProfile(
            Path("C:/game/BaldrForce.exe"),
            "x86",
            (InstructionFingerprint("test", 0, b"code"),),
            self.layout,
        )
        self.weapon_layout = WeaponStateLayout(
            combo_active=IntegerField(0x3C, 1, 0, 1, signed=False),
            slot_locks_relative_address=0x5C,
            recent_slot=IntegerField(0x108, 1, 0, 255, signed=False),
            history_relative_address=0x114,
            history_count=IntegerField(0x146, 1, 0, 255, signed=False),
            configuration_pointer_relative_address=0x164,
            weapon_ids_pointer_relative_address=0x108,
            weapon_id_minimum=0,
            weapon_id_maximum=255,
        )

    def set_life(self, segment: int, remaining: int, maximum: int = 2) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400200, struct.pack("<3h", segment, remaining, maximum))
        finally:
            writer.close()

    def simulate_natural_heat_cooldown(self) -> bool:
        """模拟游戏只在自然冷却跨到零时恢复本连段已用武器。"""

        reader = self.adapter.open_process(self.instance)
        try:
            before = struct.unpack("<h", reader.read(0x400210, 2))[0]
        finally:
            reader.close()
        if before <= 0:
            return False
        after = max(0, before - 8)
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400210, struct.pack("<h", after))
        finally:
            writer.close()
        return after == 0

    def test_full_damaged_cross_segment_and_death_totals(self) -> None:
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()
        for segment, remaining, expected in ((100, 2, 300), (10, 2, 210), (90, 1, 190), (0, -1, 0)):
            with self.subTest(segment=segment, remaining=remaining):
                self.set_life(segment, remaining)
                state = monitor.read_player_state()
                self.assertEqual((state.health, state.max_health), (expected, 300))
                self.assertEqual((state.heat, state.max_heat), (30, 130))
                self.assertEqual((state.force_crash, state.max_force_crash), (7500, 10000))

    def test_weapon_state_reports_configuration_recent_weapon_and_combo_history(self) -> None:
        monitor = RealtimeMonitor(
            self.adapter,
            replace(self.profile, weapon_state=self.weapon_layout),
        )
        monitor.connect()

        state = monitor.read_weapon_state()

        self.assertEqual(
            tuple((slot.slot, slot.weapon_id, slot.used_in_combo) for slot in state.slots),
            (
                (0, 0, True),
                (1, 1, False),
                (2, 2, False),
                (3, 69, True),
                (4, 70, False),
                (5, 71, False),
                (6, 6, False),
                (7, 7, False),
                (8, 8, False),
                (9, 96, False),
                (10, 97, False),
                (11, 98, False),
                (12, 120, True),
            ),
        )
        self.assertTrue(state.combo_active)
        self.assertEqual((state.recent_slot, state.recent_weapon_id), (3, 69))
        self.assertEqual((state.history_count, state.history), (3, (0, 69, 0)))

    def test_weapon_state_accepts_zero_count_without_reading_an_empty_buffer(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400346, b"\x00")
        finally:
            writer.close()
        monitor = RealtimeMonitor(
            self.adapter,
            replace(self.profile, weapon_state=self.weapon_layout),
        )
        monitor.connect()

        state = monitor.read_weapon_state()

        self.assertEqual((state.history_count, state.history), (0, ()))

    def test_weapon_state_accepts_two_byte_aligned_actual_weapon_id_table(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400508, struct.pack("<I", 0x400602))
            writer.write(
                0x400602,
                struct.pack("<13h", 0, 3, 12, 69, 99, 168, 6, 78, 51, 96, 102, 135, 192),
            )
        finally:
            writer.close()
        monitor = RealtimeMonitor(
            self.adapter,
            replace(self.profile, weapon_state=self.weapon_layout),
        )
        monitor.connect()

        state = monitor.read_weapon_state()

        self.assertEqual(
            tuple(slot.weapon_id for slot in state.slots),
            (
                0,
                3,
                12,
                69,
                99,
                168,
                6,
                78,
                51,
                96,
                102,
                135,
                192,
            ),
        )

    def test_weapon_state_rejects_player_owner_replaced_during_weapon_sample(self) -> None:
        monitor = RealtimeMonitor(
            self.adapter,
            replace(self.profile, weapon_state=self.weapon_layout),
        )
        monitor.connect()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        writer = self.adapter.open_process(self.instance, writable=True)
        replaced = False

        def racing_read(address: int, size: int) -> bytes:
            nonlocal replaced
            raw = original_read(address, size)
            if address == 0x40025C and not replaced:
                writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
                writer.write(0x400080, struct.pack("<I", 0x400180))
                replaced = True
            return raw

        try:
            with patch.object(connection, "read", side_effect=racing_read):
                with self.assertRaisesRegex(RealtimeEffectError, "对象|地址链"):
                    monitor.read_weapon_state()
            self.assertEqual(monitor.snapshot.state, "connected_read_only")
            self.assertEqual(monitor.snapshot.active_effects, ())
        finally:
            writer.close()

    def test_weapon_sample_scene_exit_disarms_repeat_without_reconnecting(self) -> None:
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(weapon_repeat=True),
                weapon_state=self.weapon_layout,
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
            ),
        )
        monitor.connect()
        monitor.enable_weapon_repeat()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        writer = self.adapter.open_process(self.instance, writable=True)
        changed = False

        def racing_read(address: int, size: int) -> bytes:
            nonlocal changed
            raw = original_read(address, size)
            if address == 0x40025C and not changed:
                writer.write(0x400040, b"\x00")
                changed = True
            return raw

        try:
            with patch.object(connection, "read", side_effect=racing_read):
                with self.assertRaises(RealtimePlayerUnavailableError):
                    monitor.read_weapon_state()
            self.assertEqual(monitor.snapshot.state, "connected_read_only")
            self.assertEqual(monitor.snapshot.active_effects, ())
        finally:
            writer.close()

    def test_weapon_repeat_clears_only_ordinary_slot_locks_during_combo(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(weapon_repeat=True),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()

        monitor.enable_weapon_repeat()
        snapshot = monitor.tick_effects()

        reader = self.adapter.open_process(self.instance)
        try:
            locks = reader.read(0x40025C, 13)
        finally:
            reader.close()
        self.assertEqual(locks, bytes(12) + b"\x01")
        self.assertEqual(snapshot.active_effects, ("weapon_repeat",))

    def test_weapon_repeat_rolls_full_history_back_without_touching_following_bytes(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400314, struct.pack("<25h", *range(25)))
            writer.write(0x400346, b"\x18\xa5\x5a\xc3")
        finally:
            writer.close()
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(weapon_repeat=True),
                weapon_state=self.weapon_layout,
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
            ),
        )
        monitor.connect()

        monitor.enable_weapon_repeat()
        monitor.tick_effects()

        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(reader.read(0x400346, 4), b"\x17\xa5\x5a\xc3")
            self.assertEqual(struct.unpack("<25h", reader.read(0x400314, 50)), tuple(range(25)))
        finally:
            reader.close()

    def test_weapon_repeat_waits_for_combo_and_stop_prevents_later_writes(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x40023C, b"\x00")
            writer.write(0x40025C, bytes((1,) * 13))
        finally:
            writer.close()
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(weapon_repeat=True),
                weapon_state=self.weapon_layout,
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
            ),
        )
        monitor.connect()
        monitor.enable_weapon_repeat()

        monitor.tick_effects()
        monitor.stop_weapon_repeat()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x40023C, b"\x01")
        finally:
            writer.close()
        monitor.tick_effects()

        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(reader.read(0x40025C, 13), bytes((1,) * 13))
        finally:
            reader.close()
        self.assertEqual(monitor.snapshot.active_effects, ())

    def test_weapon_repeat_pauses_during_force_crash_transient(self) -> None:
        layout = replace(
            self.layout,
            write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),),
        )
        profile = replace(
            self.profile,
            player_state=layout,
            permissions=EffectPermissions(weapon_repeat=True),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_weapon_repeat()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400041, b"\x01")
        finally:
            writer.close()

        snapshot = monitor.tick_effects()

        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(reader.read(0x40025C, 4), bytes((1, 0, 0, 1)))
        finally:
            reader.close()
        self.assertEqual(snapshot.active_effects, ("weapon_repeat",))
        self.assertIn("暂停", snapshot.message)

    def test_weapon_attack_between_validation_and_write_skips_entire_frame(self) -> None:
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(weapon_repeat=True),
                weapon_state=self.weapon_layout,
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
            ),
        )
        monitor.connect()
        monitor.enable_weapon_repeat()
        original_reader = monitor._read_weapon_state_at
        writer = self.adapter.open_process(self.instance, writable=True)
        reads = 0

        def attack_between_reads(*args: object) -> object:
            nonlocal reads
            reads += 1
            if reads == 2:
                writer.write(0x40025D, b"\x01")
            return original_reader(*args)

        try:
            with patch.object(monitor, "_read_weapon_state_at", side_effect=attack_between_reads):
                snapshot = monitor.tick_effects()
            self.assertEqual(snapshot.state, "connected_read_only")
            self.assertEqual(snapshot.active_effects, ("weapon_repeat",))
            reader = self.adapter.open_process(self.instance)
            try:
                self.assertEqual(reader.read(0x40025C, 2), b"\x01\x01")
            finally:
                reader.close()
        finally:
            writer.close()

    def test_weapon_repeat_fails_closed_after_history_passes_safe_boundary(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400346, b"\x19")
        finally:
            writer.close()
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(weapon_repeat=True),
                weapon_state=self.weapon_layout,
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
            ),
        )
        monitor.connect()
        monitor.enable_weapon_repeat()

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "disconnected")
        self.assertIn("历史", snapshot.message)

    def test_valid_full_combo_history_skips_one_tick_without_disconnect(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400314, struct.pack("<25h", *range(25)))
            writer.write(0x400346, b"\x19")
        finally:
            writer.close()
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(weapon_repeat=True),
                weapon_state=self.weapon_layout,
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
            ),
        )
        monitor.connect()
        monitor.enable_weapon_repeat()

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ("weapon_repeat",))
        self.assertIn("跳过", snapshot.message)
        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(reader.read(0x400346, 1), b"\x19")
            self.assertEqual(reader.read(0x40025C, 1), b"\x01")
        finally:
            reader.close()

        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400346, b"\x17")
        finally:
            writer.close()
        self.assertEqual(monitor.tick_effects().active_effects, ("weapon_repeat",))
        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(reader.read(0x40025C, 1), b"\x00")
        finally:
            reader.close()

    def test_weapon_repeat_stops_after_player_owner_is_replaced(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(weapon_repeat=True),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_weapon_repeat()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
            writer.write(0x400080, struct.pack("<I", 0x400180))
        finally:
            writer.close()

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(
                reader.read(0x40025C, 13),
                bytes((1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1)),
            )
        finally:
            reader.close()

    def test_weapon_repeat_does_not_resume_after_scene_exit_and_reconnect(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(weapon_repeat=True),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_weapon_repeat()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x00")
        finally:
            writer.close()

        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.active_effects, ())
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x01")
        finally:
            writer.close()
        self.assertEqual(monitor.tick_effects().active_effects, ())
        monitor.connect()
        self.assertEqual(monitor.tick_effects().active_effects, ())
        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(
                reader.read(0x40025C, 13),
                bytes((1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1)),
            )
        finally:
            reader.close()

    def test_layout_without_force_crash_field_remains_compatible(self) -> None:
        layout = replace(self.layout, force_crash=None)
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()

        state = monitor.read_player_state()

        self.assertIsNone(state.force_crash)
        self.assertIsNone(state.max_force_crash)

    def test_force_crash_meter_outside_verified_range_fails_closed(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x40023A, struct.pack("<h", -1))
        finally:
            writer.close()
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "0x3a|范围"):
            monitor.read_player_state()

        self.assertEqual(monitor.snapshot.state, "disconnected")

    def test_verified_battle_state_alternatives_are_accepted(self) -> None:
        layout = replace(
            self.layout,
            battle_state=IntegerField(0x40, 1, 0, 3, path=AddressPath()),
            battle_scenes=(
                BattleSceneSignature("normal", 1),
                BattleSceneSignature("practice", 3),
            ),
        )
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x03")
        finally:
            writer.close()

        state = monitor.read_player_state()

        self.assertEqual(state.battle_state, 3)
        self.assertEqual(state.scene_name, "practice")
        self.assertEqual(monitor.snapshot.state, "connected_read_only")

    def test_unverified_battle_state_is_still_rejected(self) -> None:
        layout = replace(
            self.layout,
            battle_state=IntegerField(0x40, 1, 0, 3, path=AddressPath()),
            battle_scenes=(
                BattleSceneSignature("normal", 1),
                BattleSceneSignature("practice", 3),
            ),
        )
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x02")
        finally:
            writer.close()

        with self.assertRaisesRegex(RealtimeEffectError, "普通或练习战斗"):
            monitor.read_player_state()

        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        self.assertEqual(monitor.snapshot.active_effects, ())

        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x03")
        finally:
            writer.close()
        self.assertEqual(monitor.read_player_state().battle_state, 3)

    def test_missing_player_object_is_transient_and_keeps_read_connection(self) -> None:
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400080, struct.pack("<I", 0))
        finally:
            writer.close()

        with self.assertRaisesRegex(RealtimeEffectError, "没有活动的玩家对象"):
            monitor.read_player_state()

        self.assertEqual(monitor.snapshot.state, "connected_read_only")

    def test_verified_scene_signatures_distinguish_normal_and_practice_battle(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400041, b"\x00")
        finally:
            writer.close()
        layout = replace(
            self.layout,
            battle_state=IntegerField(0x40, 1, 0, 3, path=AddressPath()),
            scene_guards=(IntegerField(0x41, 1, 0, 1, path=AddressPath()),),
            battle_scenes=(
                BattleSceneSignature("normal", 1, (0,)),
                BattleSceneSignature("practice", 3, (1,)),
            ),
        )
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()

        self.assertEqual(monitor.read_player_state().scene_guard_values, (0,))

        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x03\x01")
        finally:
            writer.close()
        self.assertEqual(monitor.read_player_state().scene_guard_values, (1,))

    def _practice_monitor(self) -> RealtimeMonitor:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x03\x01")
        finally:
            writer.close()
        layout = replace(
            self.layout,
            battle_state=IntegerField(0x40, 1, 0, 3, path=AddressPath()),
            scene_guards=(IntegerField(0x41, 1, 0, 1, path=AddressPath()),),
            battle_scenes=(
                BattleSceneSignature("普通战斗", 1, (0,)),
                BattleSceneSignature("练习战斗", 3, (1,)),
            ),
        )
        profile = replace(
            self.profile,
            player_state=layout,
            permissions=EffectPermissions(
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
                weapon_repeat=True,
            ),
            segmented_health_write=SegmentedHealthWriteLayout(),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        return monitor

    def test_practice_combo_enables_all_three_and_stays_with_practice_scene(self) -> None:
        monitor = self._practice_monitor()
        state = monitor.enable_practice_effects()
        self.assertEqual(state.scene_name, "练习战斗")
        self.assertEqual(monitor.snapshot.active_effects, ("health", "heat", "weapon_repeat"))
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\x01\x00")
        finally:
            writer.close()
        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())

    def test_practice_combo_rolls_back_if_scene_changes_during_enable(self) -> None:
        monitor = self._practice_monitor()
        original = monitor.enable_heat_maintenance

        def switch_scene(target: int) -> None:
            original(target)
            writer = self.adapter.open_process(self.instance, writable=True)
            try:
                writer.write(0x400040, b"\x01\x00")
            finally:
                writer.close()

        with patch.object(monitor, "enable_heat_maintenance", side_effect=switch_scene):
            with self.assertRaisesRegex(RealtimeEffectError, "练习"):
                monitor.enable_practice_effects()
        self.assertEqual(monitor.snapshot.active_effects, ())

    def test_practice_combo_rolls_back_if_last_permission_missing(self) -> None:
        monitor = self._practice_monitor()
        monitor._profile = replace(
            monitor._profile,
            permissions=EffectPermissions(
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
            ),
            weapon_repeat_write=None,
        )
        with self.assertRaisesRegex(RealtimeEffectError, "授权"):
            monitor.enable_practice_effects()
        self.assertEqual(monitor.snapshot.active_effects, ())

    def test_practice_combo_stops_before_writing_if_scene_changes_on_writable_handle(self) -> None:
        adapter = _WritableOpenMutationAdapter()
        source = self.adapter.open_process(self.instance)
        try:
            image = source.read(0x400000, 0x700)
        finally:
            source.close()
        instance = adapter.start_process("C:/game/BaldrForce.exe", image)
        original = self.adapter
        self.adapter = adapter
        try:
            monitor = self._practice_monitor()
        finally:
            self.adapter = original
        monitor.enable_practice_effects()
        writer = adapter.open_process(instance, writable=True)
        try:
            writer.write(0x400210, struct.pack("<h", 60))
        finally:
            writer.close()
        adapter.mutation_address = 0x400040
        adapter.mutation_data = b"\x01\x00"
        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        reader = adapter.open_process(instance)
        try:
            self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 60)
        finally:
            reader.close()

    def test_mixed_unverified_scene_signature_is_rejected(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400041, b"\x01")
        finally:
            writer.close()
        layout = replace(
            self.layout,
            battle_state=IntegerField(0x40, 1, 0, 3, path=AddressPath()),
            scene_guards=(IntegerField(0x41, 1, 0, 1, path=AddressPath()),),
            battle_scenes=(
                BattleSceneSignature("normal", 1, (0,)),
                BattleSceneSignature("practice", 3, (1,)),
            ),
        )
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "普通或练习战斗"):
            monitor.read_player_state()

        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        self.assertEqual(monitor.snapshot.active_effects, ())

    def test_force_crash_transient_keeps_read_session_but_pauses_writes(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400041, b"\x01")
        finally:
            writer.close()
        layout = replace(
            self.layout,
            write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),),
        )
        profile = replace(
            self.profile,
            player_state=layout,
            permissions=EffectPermissions(force_crash_refill=True),
            force_crash_write=ForceCrashWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()

        state = monitor.read_player_state()

        self.assertTrue(state.write_inhibited)
        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        with self.assertRaisesRegex(RealtimeEffectError, "暂停"):
            monitor.refill_force_crash_once()
        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x40023A, 2))[0], 7500)

    def test_force_crash_transient_pauses_and_then_resumes_continuous_writes(self) -> None:
        layout = replace(
            self.layout,
            write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),),
        )
        profile = replace(
            self.profile,
            player_state=layout,
            permissions=EffectPermissions(heat_reduce=True, heat_maintenance=True),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_heat_maintenance(0)
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400041, b"\x01")
        finally:
            writer.close()

        paused = monitor.tick_effects()

        self.assertEqual(paused.state, "connected_read_only")
        self.assertIn("暂停", paused.message)
        self.assertEqual(paused.active_effects, ("heat",))
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 30)
        reader.close()

        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400041, b"\x00")
        finally:
            writer.close()
        resumed = monitor.tick_effects()

        self.assertEqual(resumed.state, "connected_read_only")
        self.assertEqual(resumed.active_effects, ("heat",))
        reader = self.adapter.open_process(self.instance)
        try:
            self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 0)
        finally:
            reader.close()

    def test_changing_force_crash_transient_sample_is_rejected(self) -> None:
        layout = replace(
            self.layout,
            write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),),
        )
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        writer = self.adapter.open_process(self.instance, writable=True)
        inhibitor_reads = 0

        def changing_inhibitor_read(address: int, size: int) -> bytes:
            nonlocal inhibitor_reads
            raw = original_read(address, size)
            if address == 0x400041:
                inhibitor_reads += 1
                if inhibitor_reads == 1:
                    writer.write(0x400041, b"\x01")
            return raw

        try:
            with patch.object(connection, "read", side_effect=changing_inhibitor_read):
                with self.assertRaisesRegex(RealtimeEffectError, "写入抑制状态"):
                    monitor.read_player_state()
        finally:
            writer.close()

        self.assertEqual(monitor.snapshot.state, "connected_read_only")

    def test_inconsistent_segment_count_or_death_marker_fails_closed(self) -> None:
        for segment, remaining in ((100, 3), (5, -1)):
            with self.subTest(segment=segment, remaining=remaining):
                self.set_life(segment, remaining)
                monitor = RealtimeMonitor(self.adapter, self.profile)
                monitor.connect()
                with self.assertRaises(RealtimeEffectError):
                    monitor.read_player_state()
                self.assertEqual(monitor.snapshot.state, "disconnected")

    def test_read_rechecks_instruction_fingerprints_without_separate_poll(self) -> None:
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400000, b"gone")
        writer.close()
        with self.assertRaisesRegex(RealtimeEffectError, "指纹"):
            monitor.read_player_state()
        self.assertEqual(monitor.snapshot.state, "disconnected")

    def test_zero_heat_maximum_is_not_sent_to_gui(self) -> None:
        layout = replace(self.layout, max_heat=IntegerField(0x12, 2, 0, 130))
        monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
        monitor.connect()
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400210, struct.pack("<2h", 0, 0))
        writer.close()
        with self.assertRaisesRegex(RealtimeEffectError, "上限"):
            monitor.read_player_state()

    def test_multi_field_layout_requires_dedicated_root_component_writes(self) -> None:
        with self.assertRaisesRegex(ValueError, "分段回血写入布局"):
            replace(self.profile, permissions=EffectPermissions(health_restore=True))
        with self.assertRaisesRegex(ValueError, "专用热量写入布局"):
            replace(self.profile, permissions=EffectPermissions(heat_reduce=True))
        with self.assertRaisesRegex(ValueError, "专用 Force Crash 写入布局"):
            replace(self.profile, permissions=EffectPermissions(force_crash_refill=True))
        with self.assertRaisesRegex(ValueError, "生命字段必须位于玩家属性根组件"):
            replace(
                self.profile,
                player_state=replace(
                    self.layout,
                    health=replace(self.layout.health, path=AddressPath()),
                ),
                permissions=EffectPermissions(health_restore=True),
                segmented_health_write=SegmentedHealthWriteLayout(),
            )
        with self.assertRaisesRegex(ValueError, "容量"):
            SegmentedHealthLayout(IntegerField(2, 2, -1, 10), 0)

    def test_force_crash_refill_requires_explicit_permission_and_write_layout(self) -> None:
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()
        with self.assertRaisesRegex(RealtimeEffectError, "未授权"):
            monitor.refill_force_crash_once()

        profile = replace(
            self.profile,
            permissions=EffectPermissions(force_crash_refill=True),
            force_crash_write=ForceCrashWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()

        state = monitor.refill_force_crash_once()

        self.assertEqual((state.force_crash, state.max_force_crash), (10000, 10000))
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x40023A, 2))[0], 10000)

    def test_force_crash_refill_revalidates_scene_guards_on_writable_handle(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = bytearray(source.read(0x400000, 0x500))
        finally:
            source.close()
        image[0x41] = 1
        adapter = _WritableOpenMutationAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", bytes(image))
        adapter.mutation_address = 0x400041
        adapter.mutation_data = b"\0"
        layout = replace(
            self.layout,
            guards=(IntegerField(0x41, 1, 1, 1, path=AddressPath()),),
        )
        profile = replace(
            self.profile,
            player_state=layout,
            permissions=EffectPermissions(force_crash_refill=True),
            force_crash_write=ForceCrashWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "0x41|门禁"):
            monitor.refill_force_crash_once()

        self.assertEqual(monitor.snapshot.state, "disconnected")
        reader = adapter.open_process(instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x40023A, 2))[0], 7500)

    def test_force_crash_refill_rejects_death_on_writable_handle(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = source.read(0x400000, 0x500)
        finally:
            source.close()
        adapter = _WritableOpenMutationAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", image)
        adapter.mutation_address = 0x400200
        adapter.mutation_data = struct.pack("<2h", 0, -1)
        profile = replace(
            self.profile,
            permissions=EffectPermissions(force_crash_refill=True),
            force_crash_write=ForceCrashWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "死亡"):
            monitor.refill_force_crash_once()

        self.assertEqual(monitor.snapshot.state, "disconnected")
        reader = adapter.open_process(instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x40023A, 2))[0], 7500)

    def test_segmented_health_restore_and_heat_reduce_require_dedicated_write_layouts(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(health_restore=True, heat_reduce=True),
            segmented_health_write=SegmentedHealthWriteLayout(),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        self.set_life(10, 1)

        healed = monitor.restore_health_once()
        cooled = monitor.reduce_heat_once(0)

        self.assertEqual((healed.health, healed.max_health), (300, 300))
        self.assertEqual(cooled.heat, 0)
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (100, 2, 2))
        self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 0)

    def test_zero_heat_reduction_preserves_natural_combo_reset_transition(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()

        monitor.reduce_heat_once(0)

        self.assertTrue(
            self.simulate_natural_heat_cooldown(),
            "直接把热量写成零会绕过游戏的自然归零事件，导致本连段已用武器不恢复",
        )

    def test_zero_heat_maintenance_preserves_natural_combo_reset_transition(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True, heat_maintenance=True),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_heat_maintenance(0)

        monitor.tick_effects()

        self.assertTrue(
            self.simulate_natural_heat_cooldown(),
            "锁热写零会绕过游戏的自然归零事件，最终耗尽本连段全部可用武器",
        )

    def test_zero_heat_maintenance_recovers_an_already_stuck_combo_once(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400210, struct.pack("<h", 0))
        finally:
            writer.close()
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True, heat_maintenance=True),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_heat_maintenance(0)

        monitor.tick_effects()
        self.assertTrue(self.simulate_natural_heat_cooldown())
        monitor.tick_effects()

        self.assertEqual(monitor.read_player_state().heat, 0)

    def test_natural_zero_during_post_write_check_is_a_successful_heat_write(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = source.read(0x400000, 0x500)
        finally:
            source.close()
        adapter = _NaturalHeatCooldownAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", image)
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        state = monitor.reduce_heat_once(0)

        self.assertEqual(state.heat, 0)
        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        reader = adapter.open_process(instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 0)

    def test_attack_after_heat_write_does_not_disconnect_or_cancel_continuous_effects(self) -> None:
        adapter = _AttackAfterHeatWriteAdapter()
        source = self.adapter.open_process(self.instance)
        try:
            image = source.read(0x400000, 0x700)
        finally:
            source.close()
        instance = adapter.start_process("C:/game/BaldrForce.exe", image)
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True, heat_maintenance=True),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()
        monitor.enable_heat_maintenance(0)
        for _ in range(5):
            snapshot = monitor.tick_effects()
            self.assertEqual(snapshot.state, "connected_read_only")
            self.assertEqual(snapshot.active_effects, ("heat",))
        reader = adapter.open_process(instance)
        try:
            self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 85)
        finally:
            reader.close()

    def test_heat_postwrite_torn_frame_retries_on_same_writable_handle(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True, heat_maintenance=True),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_heat_maintenance(0)
        original_reader = monitor._read_player_state
        reads = 0

        def torn_once(*args: object, **kwargs: object) -> object:
            nonlocal reads
            reads += 1
            if reads == 3:
                raise RealtimeFrameUnstableError("正常攻击与复核重叠")
            return original_reader(*args, **kwargs)

        with patch.object(monitor, "_read_player_state", side_effect=torn_once):
            snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ("heat",))
        self.assertGreaterEqual(reads, 4)

    def test_segmented_continuous_health_and_heat_effects_share_object_watchdog(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
            ),
            segmented_health_write=SegmentedHealthWriteLayout(),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_health_maintenance(250)
        monitor.enable_heat_maintenance(20)
        self.set_life(10, 1)

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.active_effects, ("health", "heat"))
        state = monitor.read_player_state()
        self.assertEqual((state.health, state.heat), (250, 20))

        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400220, struct.pack("<3h", 100, 2, 2))
        writer.write(0x400230, struct.pack("<2h", 20, 130))
        writer.write(0x400110, struct.pack("<I", 0x400220))
        writer.close()
        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("对象", snapshot.message)

    def test_segmented_maintenance_writes_exact_total_target_without_reviving(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(health_restore=True, health_maintenance=True),
            segmented_health_write=SegmentedHealthWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_health_maintenance(250)
        self.set_life(10, 1)

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(monitor.read_player_state().health, 250)
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (50, 2, 2))

        self.set_life(0, -1)
        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("死亡", snapshot.message)

    def test_segmented_restore_fails_closed_when_atomic_transaction_write_fails(self) -> None:
        adapter = _FailSegmentTransactionAdapter()
        adapter.start_process(
            "C:/game/BaldrForce.exe",
            self.adapter.open_process(self.instance).read(0x400000, 0x500),
        )
        profile = replace(
            self.profile,
            permissions=EffectPermissions(health_restore=True),
            segmented_health_write=SegmentedHealthWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()
        writer = adapter.open_process(adapter.list_processes("BaldrForce.exe")[0], writable=True)
        writer.write(0x400200, struct.pack("<3h", 10, 1, 2))
        writer.close()
        adapter.fail_address = 0x400200

        with self.assertRaisesRegex(RealtimeEffectError, "injected transaction write failure"):
            monitor.restore_health_once()

        reader = adapter.open_process(adapter.list_processes("BaldrForce.exe")[0])
        self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (10, 1, 2))
        self.assertEqual(monitor.snapshot.state, "disconnected")

    def test_continuous_effect_rejects_new_owner_even_when_components_are_reused(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(health_restore=True, health_maintenance=True),
            segmented_health_write=SegmentedHealthWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_health_maintenance(250)
        self.set_life(10, 1)
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
        writer.write(0x400080, struct.pack("<I", 0x400180))
        writer.close()

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("对象", snapshot.message)
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (10, 1, 2))

    def test_continuous_writes_reject_owner_swap_after_tick_validation(self) -> None:
        for effect in ("health", "heat"):
            with self.subTest(effect=effect):
                self.setUp()
                profile = replace(
                    self.profile,
                    permissions=EffectPermissions(
                        health_restore=True,
                        health_maintenance=True,
                        heat_reduce=True,
                        heat_maintenance=True,
                    ),
                    segmented_health_write=SegmentedHealthWriteLayout(),
                    heat_write=HeatWriteLayout(),
                )
                monitor = RealtimeMonitor(self.adapter, profile)
                monitor.connect()
                self.set_life(10, 1)
                if effect == "health":
                    monitor.enable_health_maintenance(250)
                    method_name = "_write_segmented_health"
                else:
                    monitor.enable_heat_maintenance(0)
                    method_name = "_write_player_field"

                writer = self.adapter.open_process(self.instance, writable=True)
                writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
                writer.close()
                original_write = getattr(monitor, method_name)
                owner_swapped = False

                def swap_owner_before_write(
                    *args: object,
                    _original_write: Callable[..., object] = original_write,
                    **kwargs: object,
                ) -> object:
                    nonlocal owner_swapped
                    if not owner_swapped:
                        mutation_writer = self.adapter.open_process(
                            self.instance,
                            writable=True,
                        )
                        try:
                            mutation_writer.write(0x400080, struct.pack("<I", 0x400180))
                        finally:
                            mutation_writer.close()
                        owner_swapped = True
                    return _original_write(*args, **kwargs)

                with patch.object(monitor, method_name, side_effect=swap_owner_before_write):
                    snapshot = monitor.tick_effects()

                self.assertEqual(snapshot.state, "connected_read_only")
                self.assertIn("对象", snapshot.message)
                reader = self.adapter.open_process(self.instance)
                self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (10, 1, 2))
                self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 30)

    def test_one_shot_writes_reject_new_owner_reusing_components_between_handles(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = bytearray(source.read(0x400000, 0x500))
        finally:
            source.close()
        struct.pack_into("<2I", image, 0x190, 0x400200, 0x400300)
        profile = replace(
            self.profile,
            permissions=EffectPermissions(
                health_restore=True,
                heat_reduce=True,
                force_crash_refill=True,
            ),
            segmented_health_write=SegmentedHealthWriteLayout(),
            heat_write=HeatWriteLayout(),
            force_crash_write=ForceCrashWriteLayout(),
        )

        for action in ("health", "heat", "force_crash"):
            with self.subTest(action=action):
                adapter = _WritableOpenMutationAdapter()
                instance = adapter.start_process(
                    "C:/game/BaldrForce.exe",
                    bytes(image),
                )
                adapter.mutation_address = 0x400080
                adapter.mutation_data = struct.pack("<I", 0x400180)
                monitor = RealtimeMonitor(adapter, profile)
                monitor.connect()

                with self.assertRaisesRegex(RealtimeEffectError, "对象"):
                    if action == "health":
                        monitor.restore_health_once()
                    elif action == "heat":
                        monitor.reduce_heat_once(0)
                    else:
                        monitor.refill_force_crash_once()

                self.assertEqual(monitor.snapshot.state, "connected_read_only")
                reader = adapter.open_process(instance)
                self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (100, 2, 2))
                self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 30)
                self.assertEqual(struct.unpack("<h", reader.read(0x40023A, 2))[0], 7500)

    def test_one_shot_write_rejects_replaced_identity_component_with_same_values(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = bytearray(source.read(0x400000, 0x500))
        finally:
            source.close()
        struct.pack_into("<2i", image, 0x320, 7, 7)
        adapter = _WritableOpenMutationAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", bytes(image))
        adapter.mutation_address = 0x400114
        adapter.mutation_data = struct.pack("<I", 0x400320)
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "对象"):
            monitor.reduce_heat_once(0)

        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        reader = adapter.open_process(instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 30)

    def test_one_shot_restore_uses_object_token_from_same_stable_sample(self) -> None:
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
        writer.write(0x400200, struct.pack("<3h", 10, 1, 2))
        writer.close()
        profile = replace(
            self.profile,
            permissions=EffectPermissions(health_restore=True),
            segmented_health_write=SegmentedHealthWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        mutation_writer = self.adapter.open_process(self.instance, writable=True)
        fingerprint_reads = 0

        def replace_owner_after_stable_sample(address: int, size: int) -> bytes:
            nonlocal fingerprint_reads
            raw = original_read(address, size)
            if address == 0x400000 and size == 4:
                fingerprint_reads += 1
                if fingerprint_reads == 2:
                    mutation_writer.write(0x400080, struct.pack("<I", 0x400180))
            return raw

        try:
            with patch.object(connection, "read", side_effect=replace_owner_after_stable_sample):
                with self.assertRaisesRegex(RealtimeEffectError, "对象"):
                    monitor.restore_health_once()
        finally:
            mutation_writer.close()

        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        reader = self.adapter.open_process(self.instance)
        self.assertEqual(struct.unpack("<3h", reader.read(0x400200, 6)), (10, 1, 2))

    def test_one_shot_heat_revalidates_scene_guards_on_writable_handle(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = bytearray(source.read(0x400000, 0x500))
        finally:
            source.close()
        image[0x41] = 1
        adapter = _WritableOpenMutationAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", bytes(image))
        adapter.mutation_address = 0x400041
        adapter.mutation_data = b"\0"
        layout = replace(
            self.layout,
            guards=(IntegerField(0x41, 1, 1, 1, path=AddressPath()),),
        )
        profile = replace(
            self.profile,
            player_state=layout,
            permissions=EffectPermissions(heat_reduce=True),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "0x41|门禁"):
            monitor.reduce_heat_once(0)

        self.assertEqual(monitor.snapshot.state, "disconnected")
        reader = adapter.open_process(instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 30)

    def test_one_shot_heat_rejects_death_on_writable_handle(self) -> None:
        source = self.adapter.open_process(self.instance)
        try:
            image = source.read(0x400000, 0x500)
        finally:
            source.close()
        adapter = _WritableOpenMutationAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", image)
        adapter.mutation_address = 0x400200
        adapter.mutation_data = struct.pack("<2h", 0, -1)
        profile = replace(
            self.profile,
            permissions=EffectPermissions(heat_reduce=True),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "死亡"):
            monitor.reduce_heat_once(0)

        self.assertEqual(monitor.snapshot.state, "disconnected")
        reader = adapter.open_process(instance)
        self.assertEqual(struct.unpack("<h", reader.read(0x400210, 2))[0], 30)

    def test_scene_and_identity_fields_can_use_independent_paths(self) -> None:
        for address, data in ((0x400040, b"\0"), (0x400304, struct.pack("<i", 8))):
            with self.subTest(address=address):
                self.setUp()
                monitor = RealtimeMonitor(self.adapter, self.profile)
                monitor.connect()
                writer = self.adapter.open_process(self.instance, writable=True)
                writer.write(address, data)
                writer.close()
                with self.assertRaises(RealtimeEffectError):
                    monitor.read_player_state()

    def test_intermediate_pointer_change_is_detected_even_when_leaf_is_reused(self) -> None:
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        writer = self.adapter.open_process(self.instance, writable=True)

        def racing_read(address: int, size: int) -> bytes:
            raw = original_read(address, size)
            if address == 0x400200:
                writer.write(0x400080, struct.pack("<I", 0x400180))
                writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
            return raw

        try:
            with patch.object(connection, "read", side_effect=racing_read):
                with self.assertRaisesRegex(RealtimeEffectError, "地址链"):
                    monitor.read_player_state()
        finally:
            writer.close()

    def test_guard_change_and_torn_segment_sample_are_rejected(self) -> None:
        for changing_address, data in ((0x400200, struct.pack("<h", 90)), (0x400040, b"\0")):
            with self.subTest(changing_address=changing_address):
                self.setUp()
                layout = replace(
                    self.layout, guards=(IntegerField(0x40, 1, 1, 1, path=AddressPath()),)
                )
                monitor = RealtimeMonitor(self.adapter, replace(self.profile, player_state=layout))
                monitor.connect()
                connection = monitor._connection
                assert connection is not None
                original_read = connection.read
                writer = self.adapter.open_process(self.instance, writable=True)

                def racing_read(
                    address: int,
                    size: int,
                    original_read=original_read,
                    writer=writer,
                    changing_address=changing_address,
                    data=data,
                ) -> bytes:
                    raw = original_read(address, size)
                    if address == 0x400304:
                        writer.write(changing_address, data)
                    return raw

                try:
                    with patch.object(connection, "read", side_effect=racing_read):
                        with self.assertRaises(RealtimeEffectError):
                            monitor.read_player_state()
                    self.assertEqual(
                        monitor.snapshot.state,
                        "connected_read_only" if changing_address == 0x400200 else "disconnected",
                    )
                finally:
                    writer.close()

    def test_segment_boundary_torn_first_sample_recovers_without_disconnect(self) -> None:
        monitor = RealtimeMonitor(self.adapter, self.profile)
        monitor.connect()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        writer = self.adapter.open_process(self.instance, writable=True)
        changed = False

        def change_segment_between_fields(address: int, size: int) -> bytes:
            nonlocal changed
            raw = original_read(address, size)
            if address == 0x400200 and not changed:
                writer.write(0x400200, struct.pack("<2h", 0, -1))
                changed = True
            return raw

        try:
            with patch.object(connection, "read", side_effect=change_segment_between_fields):
                with self.assertRaises(RealtimeFrameUnstableError):
                    monitor.read_player_state()
            self.assertEqual(monitor.snapshot.state, "connected_read_only")
            self.assertEqual(monitor.read_player_state().health, 0)
        finally:
            writer.close()

    def test_attack_progress_during_player_and_weapon_sampling_keeps_connection(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(
                health_restore=True, health_maintenance=True, weapon_repeat=True
            ),
            segmented_health_write=SegmentedHealthWriteLayout(),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_health_maintenance(250)
        monitor.enable_weapon_repeat()
        connection = monitor._connection
        assert connection is not None
        original_read = connection.read
        writer = self.adapter.open_process(self.instance, writable=True)
        changed = False

        def attack_during_player_read(address: int, size: int) -> bytes:
            nonlocal changed
            raw = original_read(address, size)
            if address == 0x400200 and not changed:
                writer.write(0x400200, struct.pack("<h", 90))
                changed = True
            return raw

        try:
            with patch.object(connection, "read", side_effect=attack_during_player_read):
                snapshot = monitor.tick_effects()
            self.assertEqual(snapshot.state, "connected_read_only")
            self.assertEqual(snapshot.active_effects, ("health", "weapon_repeat"))
            self.assertEqual(struct.unpack("<h", original_read(0x400200, 2))[0], 90)
            self.assertEqual(monitor.read_player_state().health, 290)

            self.set_life(10, 1)
            self.assertEqual(monitor.tick_effects().active_effects, ("health", "weapon_repeat"))
            self.assertEqual(monitor.read_player_state().health, 250)

            changed = False

            def attack_during_weapon_read(address: int, size: int) -> bytes:
                nonlocal changed
                raw = original_read(address, size)
                if address == 0x40025C and not changed:
                    writer.write(0x40025C, bytes((1,)))
                    changed = True
                return raw

            with patch.object(connection, "read", side_effect=attack_during_weapon_read):
                with self.assertRaises(RealtimeEffectError):
                    monitor.read_weapon_state()
            self.assertEqual(monitor.snapshot.state, "connected_read_only")
            self.assertEqual(monitor.snapshot.active_effects, ("health", "weapon_repeat"))
            self.assertEqual(monitor.read_weapon_state().slots[0].used_in_combo, True)
        finally:
            writer.close()

    def test_write_postcheck_race_disconnects_instead_of_resuming_effects(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(weapon_repeat=True),
            weapon_state=self.weapon_layout,
            weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_weapon_repeat()
        original_read = monitor._read_player_state
        reads = 0

        def postcheck_race(*args: object, **kwargs: object) -> object:
            nonlocal reads
            reads += 1
            if reads == 3:
                raise RealtimeFrameUnstableError("写入后竞争")
            return original_read(*args, **kwargs)

        with patch.object(monitor, "_read_player_state", side_effect=postcheck_race):
            snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "disconnected")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("写入后", snapshot.message)

    def test_battle_plan_resumes_after_scene_exit_and_new_player_object(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
            ),
            segmented_health_write=SegmentedHealthWriteLayout(),
            heat_write=HeatWriteLayout(natural_zero_transition=1),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        plan = BattlePlan(health=300, full_health=True, heat=0)
        self.assertTrue(plan.resume(monitor, monitor.read_player_state()))
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            writer.write(0x400040, b"\0")
            self.assertEqual(monitor.tick_effects().active_effects, ())
            self.assertEqual(monitor.snapshot.state, "connected_read_only")
            # 下一场重建生命组件，最大 HP 变为 400；上一场地址不能再写。
            writer.write(0x400280, struct.pack("<3h", 10, 1, 3))
            writer.write(0x400290, struct.pack("<2h", 80, 130))
            writer.write(0x4002BA, struct.pack("<h", 0))
            writer.write(0x400110, struct.pack("<I", 0x400280))
            writer.write(0x400040, b"\1")
            self.assertTrue(plan.resume(monitor, monitor.read_player_state()))
            self.assertEqual(monitor.tick_effects().active_effects, ("health", "heat"))
            self.assertEqual(monitor.read_player_state().health, 400)
            self.assertEqual(writer.read(0x400200, 6), struct.pack("<3h", 100, 2, 2))
            self.assertEqual(writer.read(0x400290, 2), struct.pack("<h", 1))
            self.assertFalse(plan.resume(monitor, monitor.read_player_state()))
        finally:
            writer.close()

    def test_exit_between_poll_and_sample_preserves_reconnect_reason(self) -> None:
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(health_restore=True, health_maintenance=True),
                segmented_health_write=SegmentedHealthWriteLayout(),
            ),
        )
        monitor.connect()
        monitor.enable_health_maintenance(300)

        def exit_during_sample(*args: object) -> object:
            self.adapter.terminate(self.instance)
            raise ProcessMemoryError("进程在采样时退出")

        with patch.object(monitor, "_read_player_state", side_effect=exit_during_sample):
            snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "disconnected")
        self.assertTrue(snapshot.recoverable)
        self.assertEqual(snapshot.active_effects, ())

    def test_force_crash_starts_between_tick_and_write_pauses_without_disconnect(self) -> None:
        profile = replace(
            self.profile,
            permissions=EffectPermissions(health_restore=True, health_maintenance=True),
            segmented_health_write=SegmentedHealthWriteLayout(),
            player_state=replace(
                self.layout, write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),)
            ),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        monitor.enable_health_maintenance(300)
        self.set_life(10, 1)
        original_open = self.adapter.open_process

        def begin_force_crash(instance, *, writable=False):  # type: ignore[no-untyped-def]
            if writable:
                connection = original_open(instance, writable=True)
                connection.write(0x400041, b"\1")
                connection.close()
            return original_open(instance, writable=writable)

        with patch.object(self.adapter, "open_process", side_effect=begin_force_crash):
            snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ("health",))
        self.assertEqual(monitor.read_player_state().health, 110)

    def _fc_lock_monitor(self) -> RealtimeMonitor:
        monitor = RealtimeMonitor(
            self.adapter,
            replace(
                self.profile,
                permissions=EffectPermissions(
                    force_crash_refill=True, force_crash_maintenance=True
                ),
                force_crash_write=ForceCrashWriteLayout(),
                player_state=replace(
                    self.layout,
                    write_inhibitors=(IntegerField(0x41, 1, 0, 31, path=AddressPath()),),
                ),
            ),
        )
        monitor.connect()
        return monitor

    def test_fc_lock_refills_only_gauge_and_waits_until_cast_finishes(self) -> None:
        monitor = self._fc_lock_monitor()
        monitor.enable_force_crash_maintenance()
        reader = self.adapter.open_process(self.instance)
        writer = self.adapter.open_process(self.instance, writable=True)
        try:
            before = reader.read(0x400000, 0x700)
            self.assertEqual(monitor.tick_effects().active_effects, ("force_crash",))
            expected = bytearray(before)
            expected[0x23A:0x23C] = struct.pack("<h", 10000)
            self.assertEqual(reader.read(0x400000, 0x700), bytes(expected))
            writer.write(0x400041, b"\1")
            writer.write(0x40023A, struct.pack("<h", 0))
            self.assertEqual(monitor.tick_effects().active_effects, ("force_crash",))
            self.assertEqual(monitor.read_player_state().force_crash, 0)
            writer.write(0x400041, b"\0")
            monitor.tick_effects()
            self.assertEqual(monitor.read_player_state().force_crash, 10000)
            monitor.stop_effects()
            writer.write(0x40023A, struct.pack("<h", 500))
            monitor.tick_effects()
            self.assertEqual(monitor.read_player_state().force_crash, 500)
        finally:
            reader.close()
            writer.close()

    def test_fc_lock_revokes_on_object_replacement_and_on_write_failure(self) -> None:
        monitor = self._fc_lock_monitor()
        monitor.enable_force_crash_maintenance()
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400190, struct.pack("<2I", 0x400200, 0x400300))
        writer.write(0x400080, struct.pack("<I", 0x400180))
        writer.close()
        self.assertEqual(monitor.tick_effects().active_effects, ())
        self.assertEqual(monitor.read_player_state().force_crash, 7500)
        monitor.enable_force_crash_maintenance()
        original_open = self.adapter.open_process

        def deny_write(instance, *, writable=False):  # type: ignore[no-untyped-def]
            if writable:
                raise ProcessMemoryError("injected FC write denied")
            return original_open(instance, writable=False)

        with patch.object(self.adapter, "open_process", side_effect=deny_write):
            snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "disconnected")
        self.assertFalse(snapshot.active_effects)

    def test_fc_lock_does_not_revive_or_write_in_menus(self) -> None:
        with self.assertRaisesRegex(ValueError, "一次补充"):
            EffectPermissions(force_crash_maintenance=True)
        monitor = self._fc_lock_monitor()
        monitor.enable_force_crash_maintenance()
        self.set_life(0, -1)
        self.assertFalse(monitor.tick_effects().active_effects)
        self.assertEqual(monitor.read_player_state().force_crash, 7500)
        self.set_life(100, 2)
        monitor.enable_force_crash_maintenance()
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400040, b"\0")
        writer.close()
        self.assertFalse(monitor.tick_effects().active_effects)

    def test_prebattle_gui_applies_saved_settings_on_first_valid_poll(self) -> None:
        from PySide6.QtWidgets import QApplication

        from bfmod.gui import MainWindow, create_application

        application = QApplication.instance() or create_application(["prebattle-test"])
        assert application is not None
        profile = replace(
            self.profile,
            permissions=EffectPermissions(
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
                force_crash_refill=True,
                force_crash_maintenance=True,
            ),
            segmented_health_write=SegmentedHealthWriteLayout(),
            heat_write=HeatWriteLayout(),
            force_crash_write=ForceCrashWriteLayout(),
        )
        monitor = RealtimeMonitor(self.adapter, profile)
        monitor.connect()
        writer = self.adapter.open_process(self.instance, writable=True)
        writer.write(0x400040, b"\0")
        with patch.object(MainWindow, "_load_initial_workspace"):
            window = MainWindow()
        window.realtime_monitor = monitor
        page = window.realtime_page
        page.remember_effects.setChecked(True)
        try:
            page.set_effect_capabilities(
                hud=True,
                health_restore=True,
                health_maintenance=True,
                heat_reduce=True,
                heat_maintenance=True,
                force_crash_refill=True,
                force_crash_maintenance=True,
            )
            self.assertTrue(page.health_maintenance.isEnabled())
            self.assertFalse(page.restore_health_button.isEnabled())
            page.health_maintenance.setChecked(True)
            page.heat_target.setValue(20)
            page.heat_maintenance.setChecked(True)
            page.force_crash_maintenance.setChecked(True)
            self.assertTrue(window.battle_plan.full_health)
            self.assertEqual(monitor.snapshot.active_effects, ())
            self.assertEqual(writer.read(0x40023A, 2), struct.pack("<h", 7500))
            with (
                patch.object(window, "_realtime_hud_available", return_value=True),
                patch.object(window, "_realtime_weapon_state_available", return_value=False),
            ):
                window._poll_realtime_monitor()
                self.assertEqual(page.heat_target.value(), 20)
                writer.write(0x400040, b"\1")
                window._poll_realtime_monitor()
                player = monitor.read_player_state()
                self.assertEqual((player.health, player.heat, player.force_crash), (300, 20, 10000))
                self.assertEqual(monitor.snapshot.active_effects, ("health", "heat", "force_crash"))
                page.remember_effects.setChecked(False)
                writer.write(0x400040, b"\0")
                window._poll_realtime_monitor()
                self.assertFalse(window.battle_plan.effects)
                writer.write(0x400040, b"\1")
                window._poll_realtime_monitor()
                self.assertEqual(monitor.snapshot.active_effects, ())
        finally:
            writer.close()
            window.close()


class _FailSegmentTransactionAdapter(FakeProcessMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fail_address = 0

    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        connection = super().open_process(instance, writable=writable)
        if writable and self.fail_address:
            return _FailSegmentTransactionMemory(connection, self.fail_address)
        return connection


class _NaturalHeatCooldownAdapter(FakeProcessMemoryAdapter):
    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        connection = super().open_process(instance, writable=writable)
        if not writable:
            return connection
        return _NaturalHeatCooldownMemory(connection, 0x400210)


class _AttackAfterHeatWriteAdapter(FakeProcessMemoryAdapter):
    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        connection = super().open_process(instance, writable=writable)
        if writable:
            return _AttackAfterHeatWriteMemory(connection)
        return connection


class _AttackAfterHeatWriteMemory:
    def __init__(self, connection):  # type: ignore[no-untyped-def]
        self._connection = connection

    @property
    def instance(self):  # type: ignore[no-untyped-def]
        return self._connection.instance

    @property
    def writable(self):  # type: ignore[no-untyped-def]
        return self._connection.writable

    def read(self, address: int, size: int) -> bytes:
        return self._connection.read(address, size)

    def write(self, address: int, data: bytes) -> None:
        self._connection.write(address, data)
        if address == 0x400210:
            self._connection.write(address, struct.pack("<h", 85))

    def is_alive(self) -> bool:
        return self._connection.is_alive()

    def close(self) -> None:
        self._connection.close()


class _WritableOpenMutationAdapter(FakeProcessMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.mutation_address = 0
        self.mutation_data = b""

    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        if writable and self.mutation_address:
            writer = super().open_process(instance, writable=True)
            try:
                writer.write(self.mutation_address, self.mutation_data)
            finally:
                writer.close()
            self.mutation_address = 0
        return super().open_process(instance, writable=writable)


class _FailSegmentTransactionMemory:
    def __init__(self, connection, fail_address: int):  # type: ignore[no-untyped-def]
        self._connection = connection
        self._fail_address = fail_address
        self._failed = False

    @property
    def instance(self):  # type: ignore[no-untyped-def]
        return self._connection.instance

    @property
    def writable(self):  # type: ignore[no-untyped-def]
        return self._connection.writable

    def read(self, address: int, size: int) -> bytes:
        return self._connection.read(address, size)

    def write(self, address: int, data: bytes) -> None:
        if address == self._fail_address and not self._failed:
            self._failed = True
            raise ProcessMemoryError("injected transaction write failure")
        self._connection.write(address, data)

    def is_alive(self) -> bool:
        return self._connection.is_alive()

    def close(self) -> None:
        self._connection.close()


class _NaturalHeatCooldownMemory:
    def __init__(self, connection, heat_address: int):  # type: ignore[no-untyped-def]
        self._connection = connection
        self._heat_address = heat_address

    @property
    def instance(self):  # type: ignore[no-untyped-def]
        return self._connection.instance

    @property
    def writable(self):  # type: ignore[no-untyped-def]
        return self._connection.writable

    def read(self, address: int, size: int) -> bytes:
        return self._connection.read(address, size)

    def write(self, address: int, data: bytes) -> None:
        self._connection.write(address, data)
        if address == self._heat_address and data == struct.pack("<h", 1):
            self._connection.write(address, struct.pack("<h", 0))

    def is_alive(self) -> bool:
        return self._connection.is_alive()

    def close(self) -> None:
        self._connection.close()


if __name__ == "__main__":
    unittest.main()
