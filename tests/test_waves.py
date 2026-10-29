from __future__ import annotations

import struct
from dataclasses import replace
from unittest.mock import patch

from test_enemy_clear import CombatFixture

from bfmod.realtime import (
    EffectPermissions,
    RealtimeEffectError,
    RealtimeFrameUnstableError,
    RealtimeMonitor,
)
from bfmod.waves import WaveSkipLayout


class WaveSkipTests(CombatFixture):
    def setUp(self) -> None:
        super().setUp()
        self.monitor.disconnect()
        # 场上只保留玩家和友军；其他现有对象由各用例按需重新放入。
        self.memory[0x108:0x180] = bytes(0x78)
        self.memory[0x42] = 255
        struct.pack_into("<2I", self.memory, 0x44, 123, 456)
        struct.pack_into("<I", self.memory, 0x184, 0x419000)
        self.memory[0x19000:0x19002] = b"\x01\x0a"
        struct.pack_into("<I", self.memory, 0x19038, 0x41A000)
        struct.pack_into("<3i", self.memory, 0x1A000, 2, 12, 0)
        for index, time in enumerate((500, 800)):
            struct.pack_into("<8i", self.memory, 0x1A00C + 32 * index, 1, time, 2, 1, 11, 0, -1, -1)
        self.profile = replace(
            self.profile,
            permissions=EffectPermissions(
                enemy_clear=True, enemy_clear_maintenance=True, wave_skip=True
            ),
            wave_skip=WaveSkipLayout(0x184, 0x42, 0x43),
        )
        self.monitor = RealtimeMonitor(self.adapter, self.profile)
        self.monitor.connect()

    def test_only_independent_clock_changes_not_elapsed_time_results_records_or_hp(self) -> None:
        expected = bytearray(self.memory)
        struct.pack_into("<i", expected, 0x1A004, 500)
        self.assertEqual(self.monitor.advance_enemy_wave_once(), 488)
        self.assertEqual(self.memory, expected)
        self.assertEqual(self.monitor.snapshot.state, "connected_read_only")

    def test_permissions_require_auto_clear_and_dedicated_layout(self) -> None:
        with self.assertRaises(ValueError):
            EffectPermissions(wave_skip=True)
        with self.assertRaises(ValueError):
            replace(self.profile, wave_skip=None)
        with self.assertRaises(ValueError):
            replace(
                self.profile,
                permissions=EffectPermissions(enemy_clear=True, enemy_clear_maintenance=True),
            )

    def test_live_enemy_even_if_already_retiring_blocks_clock_jump(self) -> None:
        struct.pack_into("<I", self.memory, 0x108, 0x409000)
        struct.pack_into("<h", self.memory, self.timer_offset(2), 1)
        with patch.object(self.adapter, "open_process", wraps=self.adapter.open_process) as opened:
            self.assertEqual(self.monitor.advance_enemy_wave_once(), 0)
        opened.assert_not_called()

    def test_protected_plot_slot_blocks_jump(self) -> None:
        self.memory[0x201] = 3
        struct.pack_into("<I", self.memory, 0x204, 2)
        self.assertEqual(self.monitor.advance_enemy_wave_once(), 0)
        self.assertEqual(struct.unpack_from("<i", self.memory, 0x1A004)[0], 12)

    def test_repeat_paused_unknown_team_pending_result_and_finish_are_not_accelerated(self) -> None:
        baseline = bytes(self.memory)
        mutations = (
            (0x1A008, struct.pack("<i", 1)),
            (0x1903C, b"\x01"),
            (0x19001, b"\x05"),
            (0x1A018, struct.pack("<i", 2)),
            (0x42, b"\x00"),
            (0x43, b"\x01"),
            (0x184, bytes(4)),
        )
        for offset, value in mutations:
            with self.subTest(offset=offset):
                self.memory[:] = baseline
                self.memory[offset : offset + len(value)] = value
                self.assertEqual(self.monitor.advance_enemy_wave_once(), 0)
                self.assertEqual(struct.unpack_from("<i", self.memory, 0x1A004)[0], 12)

    def test_due_or_empty_queue_does_not_open_writer(self) -> None:
        struct.pack_into("<i", self.memory, 0x1A004, 500)
        with patch.object(self.adapter, "open_process", wraps=self.adapter.open_process) as opened:
            self.assertEqual(self.monitor.advance_enemy_wave_once(), 0)
        opened.assert_not_called()
        self.memory[0x1A000:0x1A80C] = bytes(0x80C)
        self.assertEqual(self.monitor.advance_enemy_wave_once(), 0)

    def test_inconsistent_queue_skips_frame_and_invalid_queue_stops_writes(self) -> None:
        struct.pack_into("<i", self.memory, 0x1A000, 3)
        with self.assertRaises(RealtimeFrameUnstableError):
            self.monitor.advance_enemy_wave_once()
        self.assertEqual(self.monitor.snapshot.state, "connected_read_only")
        struct.pack_into("<i", self.memory, 0x1A000, 2)
        struct.pack_into("<i", self.memory, 0x1A014, 0)
        with self.assertRaises(RealtimeEffectError):
            self.monitor.advance_enemy_wave_once()
        self.assertEqual(self.monitor.snapshot.state, "disconnected")

    def test_writer_revalidates_queue_controller_and_new_enemy(self) -> None:
        original = self.adapter.open_process
        baseline = bytes(self.memory)
        for mutation in ("enemy", "queue", "controller", "scene", "fc", "fingerprint"):
            with self.subTest(mutation=mutation):
                self.memory[:] = baseline
                self.monitor.connect()

                def opened(instance, *, writable=False, mutation=mutation):
                    if writable:
                        if mutation == "enemy":
                            struct.pack_into("<I", self.memory, 0x108, 0x409000)
                        elif mutation == "queue":
                            struct.pack_into("<i", self.memory, 0x1A010, 600)
                        elif mutation == "controller":
                            struct.pack_into("<I", self.memory, 0x184, 0)
                        elif mutation == "scene":
                            self.memory[0x40] = 1
                        elif mutation == "fc":
                            self.memory[0x41] = 1
                        else:
                            self.memory[0] = 0
                    return original(instance, writable=writable)

                with patch.object(self.adapter, "open_process", opened):
                    try:
                        self.assertEqual(self.monitor.advance_enemy_wave_once(), 0)
                    except RealtimeEffectError:
                        self.assertIn(mutation, ("scene", "fc", "fingerprint"))
                self.assertEqual(struct.unpack_from("<i", self.memory, 0x1A004)[0], 12)

    def test_native_consumption_after_jump_is_accepted_without_rewriting_records(self) -> None:
        original = self.adapter.open_process

        def opened(instance, *, writable=False):
            handle = original(instance, writable=writable)
            if writable:
                write = handle.write

                def native_spawn(address, data):
                    write(address, data)
                    struct.pack_into("<2i", self.memory, 0x1A000, 1, 501)
                    self.memory[0x1A00C] = 2

                handle.write = native_spawn
            return handle

        with patch.object(self.adapter, "open_process", opened):
            self.assertEqual(self.monitor.advance_enemy_wave_once(), 488)
        self.assertEqual(self.monitor.snapshot.state, "connected_read_only")

    def test_auto_clear_skips_wait_and_stop_prevents_any_further_clock_change(self) -> None:
        self.monitor.enable_enemy_clear_maintenance()
        result = self.monitor.tick_effects()
        self.assertEqual(result.active_effects, ("enemy_clear",))
        self.assertIn("488", result.message)
        self.assertEqual(struct.unpack_from("<i", self.memory, 0x1A004)[0], 500)
        self.monitor.stop_effects()
        struct.pack_into("<i", self.memory, 0x1A004, 12)
        self.monitor.tick_effects()
        self.assertEqual(struct.unpack_from("<i", self.memory, 0x1A004)[0], 12)

    def test_write_failure_or_process_reuse_cannot_keep_advancing(self) -> None:
        self.monitor.enable_enemy_clear_maintenance()
        self.adapter._state(self.instance).writable = False
        self.assertEqual(self.monitor.tick_effects().state, "disconnected")
        self.assertFalse(self.monitor.snapshot.active_effects)
        self.adapter.terminate(self.instance)
        new = self.adapter.start_process(
            "C:/test/BaldrForce.exe", bytes(self.memory), pid=self.instance.pid
        )
        self.monitor.tick_effects()
        self.assertEqual(struct.unpack_from("<i", self.adapter._state(new).memory, 0x1A004)[0], 12)
