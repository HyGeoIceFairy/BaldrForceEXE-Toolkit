from __future__ import annotations

import unittest
from pathlib import Path

from bfmod.process_memory import (
    FakeProcessMemoryAdapter,
    ProcessMemoryError,
    normalized_executable_path,
)
from bfmod.realtime import (
    AddressPath,
    BattleSceneSignature,
    EffectPermissions,
    HeatWriteLayout,
    InstructionFingerprint,
    IntegerField,
    PlayerStateLayout,
    RealtimeConnectionError,
    RealtimeEffectError,
    RealtimeMonitor,
    RealtimeProfile,
)


class FakeProcessMemoryAdapterTests(unittest.TestCase):
    def test_read_only_connection_cannot_write(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        instance = adapter.start_process(
            Path("C:/Game/Baldr Force Exe/BaldrForce.exe"),
            b"\x01\x02\x03\x04",
        )
        connection = adapter.open_process(instance)

        self.assertFalse(connection.writable)
        self.assertEqual(connection.read(instance.image_base, 4), b"\x01\x02\x03\x04")
        with self.assertRaisesRegex(ProcessMemoryError, "只读"):
            connection.write(instance.image_base, b"\x05")

    def test_writable_connection_performs_exact_write(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        instance = adapter.start_process("C:/game/BaldrForce.exe", b"\0" * 8)
        connection = adapter.open_process(instance, writable=True)

        connection.write(instance.image_base + 2, b"\xaa\xbb")

        self.assertEqual(connection.read(instance.image_base, 8), b"\0\0\xaa\xbb\0\0\0\0")

    def test_short_transfer_and_permission_failure_are_errors(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        short_instance = adapter.start_process(
            "C:/game/BaldrForce.exe",
            b"\0" * 8,
            maximum_transfer=2,
        )
        short_connection = adapter.open_process(short_instance, writable=True)
        with self.assertRaisesRegex(ProcessMemoryError, "短读"):
            short_connection.read(short_instance.image_base, 4)
        with self.assertRaisesRegex(ProcessMemoryError, "短写"):
            short_connection.write(short_instance.image_base, b"1234")

        denied_instance = adapter.start_process(
            "C:/game/BaldrForce.exe",
            b"\0" * 8,
            writable=False,
        )
        with self.assertRaisesRegex(ProcessMemoryError, "权限"):
            adapter.open_process(denied_instance, writable=True)

    def test_exit_invalidates_existing_connection_and_pid_reuse_is_distinct(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        first = adapter.start_process("C:/game/BaldrForce.exe", b"\0" * 8, pid=42)
        connection = adapter.open_process(first)
        adapter.terminate(first)

        self.assertFalse(connection.is_alive())
        with self.assertRaisesRegex(ProcessMemoryError, "退出"):
            connection.read(first.image_base, 1)

        second = adapter.start_process("C:/game/BaldrForce.exe", b"\0" * 8, pid=42)
        self.assertNotEqual(first.instance_key, second.instance_key)
        self.assertEqual(adapter.list_processes("BALDRFORCE.EXE"), (second,))
        with self.assertRaisesRegex(ProcessMemoryError, "退出"):
            adapter.open_process(first)

    def test_normalized_path_is_stable_for_equivalent_paths(self) -> None:
        direct = normalized_executable_path("C:/game/BaldrForce.exe")
        equivalent = normalized_executable_path("C:/game/sub/../BaldrForce.exe")
        self.assertEqual(direct, equivalent)


class RealtimeMonitorTests(unittest.TestCase):
    def test_rejects_unsafe_address_paths_and_continuous_permission_gaps(self) -> None:
        with self.assertRaisesRegex(ValueError, "对齐"):
            AddressPath(2, (0,))
        with self.assertRaisesRegex(ValueError, "八级"):
            AddressPath(0x80, (0,) * 9)
        with self.assertRaisesRegex(ValueError, "一次回血"):
            EffectPermissions(health_maintenance=True)
        with self.assertRaisesRegex(ValueError, "一次降热"):
            EffectPermissions(heat_maintenance=True)

    def test_connect_requires_unique_expected_x86_process_and_fingerprints(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        expected_path = Path("C:/game/BaldrForce.exe")
        first = adapter.start_process(expected_path, b"\x90" * 32)
        adapter.start_process(expected_path, b"\x90" * 32)
        profile = RealtimeProfile(
            executable_path=expected_path,
            architecture="x86",
            fingerprints=(InstructionFingerprint("probe", 4, b"\x90\x90"),),
        )
        monitor = RealtimeMonitor(adapter, profile)

        with self.assertRaisesRegex(RealtimeConnectionError, "多个"):
            monitor.connect()

        for candidate in adapter.list_processes("BaldrForce.exe"):
            if candidate != first:
                adapter.terminate(candidate)
        snapshot = monitor.connect()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.instance, first)
        self.assertTrue(snapshot.fingerprints_valid)

    def test_connect_rejects_wrong_path_architecture_and_unexpanded_image(self) -> None:
        expected_path = Path("C:/game/BaldrForce.exe")
        profile = RealtimeProfile(
            executable_path=expected_path,
            architecture="x86",
            fingerprints=(InstructionFingerprint("probe", 4, b"\xcc\xcc"),),
        )

        wrong_path_adapter = FakeProcessMemoryAdapter()
        wrong_path_adapter.start_process("D:/other/BaldrForce.exe", b"\xcc" * 16)
        with self.assertRaisesRegex(RealtimeConnectionError, "路径"):
            RealtimeMonitor(wrong_path_adapter, profile).connect()

        wrong_arch_adapter = FakeProcessMemoryAdapter()
        wrong_arch_adapter.start_process(expected_path, b"\xcc" * 16, architecture="x64")
        with self.assertRaisesRegex(RealtimeConnectionError, "架构"):
            RealtimeMonitor(wrong_arch_adapter, profile).connect()

        unexpanded_adapter = FakeProcessMemoryAdapter()
        unexpanded_adapter.start_process(expected_path, b"\0" * 16)
        with self.assertRaisesRegex(RealtimeConnectionError, "指令指纹"):
            RealtimeMonitor(unexpanded_adapter, profile).connect()

    def test_poll_invalidates_connection_after_exit_or_fingerprint_change(self) -> None:
        expected_path = Path("C:/game/BaldrForce.exe")
        profile = RealtimeProfile(
            executable_path=expected_path,
            architecture="x86",
            fingerprints=(InstructionFingerprint("probe", 4, b"\xaa\xbb"),),
        )

        exit_adapter = FakeProcessMemoryAdapter()
        instance = exit_adapter.start_process(
            expected_path,
            b"\0\0\0\0\xaa\xbb\0\0",
        )
        exit_monitor = RealtimeMonitor(exit_adapter, profile)
        exit_monitor.connect()
        exit_adapter.terminate(instance)
        snapshot = exit_monitor.poll()
        self.assertEqual(snapshot.state, "disconnected")
        self.assertIn("退出", snapshot.message)

        changed_adapter = FakeProcessMemoryAdapter()
        changed_instance = changed_adapter.start_process(
            expected_path,
            b"\0\0\0\0\xaa\xbb\0\0",
        )
        changed_monitor = RealtimeMonitor(changed_adapter, profile)
        changed_monitor.connect()
        writer = changed_adapter.open_process(changed_instance, writable=True)
        writer.write(changed_instance.image_base + 4, b"\x90\x90")
        snapshot = changed_monitor.poll()
        self.assertEqual(snapshot.state, "disconnected")
        self.assertIn("指令指纹", snapshot.message)

    def test_effect_tick_keeps_read_only_profile_connected_without_player_layout(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        adapter.start_process(path, b"\0\0\0\0\xaa\xbb\0\0")
        monitor = RealtimeMonitor(
            adapter,
            RealtimeProfile(
                executable_path=path,
                architecture="x86",
                fingerprints=(InstructionFingerprint("probe", 4, b"\xaa\xbb"),),
            ),
        )
        monitor.connect()

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("只读连接正常", snapshot.message)

    def test_reads_valid_player_state_and_applies_one_shot_effects(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image = bytearray(64)
        image[4:6] = b"\xaa\xbb"
        image[16:20] = (40).to_bytes(4, "little", signed=True)
        image[20:24] = (100).to_bytes(4, "little", signed=True)
        image[24:28] = (80).to_bytes(4, "little", signed=True)
        image[28:32] = (200).to_bytes(4, "little", signed=True)
        image[32:36] = (7).to_bytes(4, "little", signed=True)
        image[36:40] = (7).to_bytes(4, "little", signed=True)
        image[40:44] = (1).to_bytes(4, "little", signed=True)
        instance = adapter.start_process(path, bytes(image))
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()

        state = monitor.read_player_state()
        self.assertEqual((state.health, state.max_health), (40, 100))
        self.assertEqual((state.heat, state.max_heat), (80, 200))

        healed = monitor.restore_health_once()
        cooled = monitor.reduce_heat_once(25)
        reader = adapter.open_process(instance)
        self.assertEqual(healed.health, 100)
        self.assertEqual(cooled.heat, 25)
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 16, 4), "little", signed=True),
            100,
        )
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 24, 4), "little", signed=True),
            25,
        )

    def test_resolves_heap_player_object_through_module_pointer_chain(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image_base = 0x400000
        image = bytearray(0x300)
        image[4:6] = b"\xaa\xbb"
        image[0x80:0x84] = (image_base + 0x100).to_bytes(4, "little")
        image[0x108:0x10C] = (image_base + 0x180).to_bytes(4, "little")
        image[0x190:0x194] = (40).to_bytes(4, "little", signed=False)
        image[0x194:0x198] = (100).to_bytes(4, "little", signed=False)
        image[0x198:0x19C] = (80).to_bytes(4, "little", signed=False)
        image[0x19C:0x1A0] = (200).to_bytes(4, "little", signed=False)
        image[0x1A0:0x1A4] = (7).to_bytes(4, "little", signed=False)
        image[0x1A4:0x1A8] = (7).to_bytes(4, "little", signed=False)
        image[0x1A8:0x1AC] = (1).to_bytes(4, "little", signed=False)
        instance = adapter.start_process(path, bytes(image), image_base=image_base)
        profile = RealtimeProfile(
            executable_path=path,
            architecture="x86",
            fingerprints=(InstructionFingerprint("probe", 4, b"\xaa\xbb"),),
            player_state=PlayerStateLayout(
                root=AddressPath(0x80, (8, 0)),
                health=IntegerField(0x10, 4, 0, 1_000_000, signed=False),
                max_health=IntegerField(0x14, 4, 1, 1_000_000, signed=False),
                heat=IntegerField(0x18, 4, 0, 1_000_000, signed=False),
                max_heat=IntegerField(0x1C, 4, 1, 1_000_000, signed=False),
                player_identity=IntegerField(0x20, 4, 0, 1_000_000, signed=False),
                object_identity=IntegerField(0x24, 4, 0, 1_000_000, signed=False),
                battle_state=IntegerField(0x28, 4, 0, 10, signed=False),
                battle_scenes=(BattleSceneSignature("test", 1),),
            ),
            permissions=EffectPermissions(health_restore=True, heat_reduce=True),
            heat_write=HeatWriteLayout(),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        state = monitor.read_player_state()
        healed = monitor.restore_health_once()
        cooled = monitor.reduce_heat_once(25)

        self.assertEqual((state.health, state.heat), (40, 80))
        self.assertEqual((healed.health, cooled.heat), (100, 25))
        reader = adapter.open_process(instance)
        self.assertEqual(
            int.from_bytes(reader.read(image_base + 0x190, 4), "little", signed=False),
            100,
        )
        self.assertEqual(
            int.from_bytes(reader.read(image_base + 0x198, 4), "little", signed=False),
            25,
        )

    def test_pointer_chain_change_between_validation_and_write_fails_closed(self) -> None:
        adapter = _PointerSwapAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image_base = 0x400000
        image = bytearray(0x300)
        image[4:6] = b"\xaa\xbb"
        image[0x80:0x84] = (image_base + 0x180).to_bytes(4, "little")
        for object_offset in (0x180, 0x200):
            image[object_offset + 0x10 : object_offset + 0x14] = (40).to_bytes(4, "little")
            image[object_offset + 0x14 : object_offset + 0x18] = (100).to_bytes(4, "little")
            image[object_offset + 0x18 : object_offset + 0x1C] = (80).to_bytes(4, "little")
            image[object_offset + 0x1C : object_offset + 0x20] = (200).to_bytes(4, "little")
            image[object_offset + 0x20 : object_offset + 0x24] = (7).to_bytes(4, "little")
            image[object_offset + 0x24 : object_offset + 0x28] = (7).to_bytes(4, "little")
            image[object_offset + 0x28 : object_offset + 0x2C] = (1).to_bytes(4, "little")
        instance = adapter.start_process(path, bytes(image), image_base=image_base)
        adapter.swap_address = image_base + 0x80
        adapter.swap_value = image_base + 0x200
        profile = RealtimeProfile(
            executable_path=path,
            architecture="x86",
            fingerprints=(InstructionFingerprint("probe", 4, b"\xaa\xbb"),),
            player_state=PlayerStateLayout(
                root=AddressPath(0x80, (0,)),
                health=IntegerField(0x10, 4, 0, 1_000_000),
                max_health=IntegerField(0x14, 4, 1, 1_000_000),
                heat=IntegerField(0x18, 4, 0, 1_000_000),
                max_heat=IntegerField(0x1C, 4, 1, 1_000_000),
                player_identity=IntegerField(0x20, 4, 0, 1_000_000),
                object_identity=IntegerField(0x24, 4, 0, 1_000_000),
                battle_state=IntegerField(0x28, 4, 0, 10),
                battle_scenes=(BattleSceneSignature("test", 1),),
            ),
            permissions=EffectPermissions(health_restore=True),
        )
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "地址链|对象"):
            monitor.restore_health_once()

        self.assertEqual(monitor.snapshot.state, "connected_read_only")
        reader = adapter.open_process(instance)
        self.assertEqual(int.from_bytes(reader.read(image_base + 0x190, 4), "little"), 40)
        self.assertEqual(int.from_bytes(reader.read(image_base + 0x210, 4), "little"), 40)

    def test_write_revalidates_fingerprint_and_disconnects_before_writing(self) -> None:
        adapter = _FingerprintSwapAdapter()
        path = Path("C:/game/BaldrForce.exe")
        instance = adapter.start_process(path, _player_image())
        adapter.fingerprint_address = instance.image_base + 4
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "指令指纹"):
            monitor.restore_health_once()

        self.assertEqual(monitor.snapshot.state, "disconnected")
        reader = adapter.open_process(instance)
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 16, 4), "little", signed=True),
            50,
        )

    def test_write_disconnects_when_identity_changes_during_post_write_check(self) -> None:
        adapter = _PostWriteIdentitySwapAdapter()
        path = Path("C:/game/BaldrForce.exe")
        instance = adapter.start_process(path, _player_image())
        adapter.trigger_address = instance.image_base + 16
        adapter.identity_address = instance.image_base + 36
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "写入后发生变化"):
            monitor.restore_health_once()

        self.assertEqual(monitor.snapshot.state, "disconnected")
        self.assertEqual(monitor.snapshot.active_effects, ())

    def test_write_rechecks_fingerprint_after_the_memory_write(self) -> None:
        adapter = _PostWriteFingerprintSwapAdapter()
        path = Path("C:/game/BaldrForce.exe")
        instance = adapter.start_process(path, _player_image())
        adapter.trigger_address = instance.image_base + 24
        adapter.fingerprint_address = instance.image_base + 4
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "指令指纹"):
            monitor.reduce_heat_once(25)

        self.assertEqual(monitor.snapshot.state, "disconnected")
        self.assertEqual(monitor.snapshot.active_effects, ())

    def test_layout_does_not_authorize_unproven_write_capabilities(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image = _player_image()
        adapter.start_process(path, image)
        profile = _realtime_profile(path, permissions=EffectPermissions())
        monitor = RealtimeMonitor(adapter, profile)
        monitor.connect()

        self.assertEqual(monitor.read_player_state().health, 50)
        with self.assertRaisesRegex(RealtimeEffectError, "未授权"):
            monitor.restore_health_once()
        with self.assertRaisesRegex(RealtimeEffectError, "未授权"):
            monitor.enable_heat_maintenance(20)
        self.assertEqual(monitor.snapshot.state, "connected_read_only")

    def test_player_validation_and_write_failure_disconnect_before_unknown_writes(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image = bytearray(64)
        image[4:6] = b"\xaa\xbb"
        image[16:20] = (0).to_bytes(4, "little", signed=True)
        image[20:24] = (100).to_bytes(4, "little", signed=True)
        image[24:28] = (10).to_bytes(4, "little", signed=True)
        image[28:32] = (200).to_bytes(4, "little", signed=True)
        image[32:36] = (7).to_bytes(4, "little", signed=True)
        image[36:40] = (7).to_bytes(4, "little", signed=True)
        image[40:44] = (1).to_bytes(4, "little", signed=True)
        instance = adapter.start_process(path, bytes(image))
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()

        with self.assertRaisesRegex(RealtimeEffectError, "死亡"):
            monitor.restore_health_once()
        self.assertEqual(monitor.snapshot.state, "disconnected")

        monitor.connect()
        with self.assertRaisesRegex(RealtimeEffectError, "死亡"):
            monitor.reduce_heat_once(0)
        self.assertEqual(monitor.snapshot.state, "disconnected")
        reader = adapter.open_process(instance)
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 24, 4), "little", signed=True),
            10,
        )
        reader.close()

        for enable_effect in (
            lambda: monitor.enable_health_maintenance(50),
            lambda: monitor.enable_heat_maintenance(10),
        ):
            monitor.connect()
            with self.assertRaisesRegex(RealtimeEffectError, "死亡"):
                enable_effect()
            self.assertEqual(monitor.snapshot.state, "disconnected")

        writer = adapter.open_process(instance, writable=True)
        writer.write(instance.image_base + 16, (50).to_bytes(4, "little", signed=True))
        writer.write(instance.image_base + 40, (0).to_bytes(4, "little", signed=True))
        writer.close()
        monitor.connect()
        with self.assertRaisesRegex(RealtimeEffectError, "战斗"):
            monitor.reduce_heat_once(0)
        self.assertEqual(monitor.snapshot.state, "connected_read_only")

    def test_continuous_tick_stops_on_object_replacement_or_short_write(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image = bytearray(64)
        image[4:6] = b"\xaa\xbb"
        image[16:20] = (50).to_bytes(4, "little", signed=True)
        image[20:24] = (100).to_bytes(4, "little", signed=True)
        image[24:28] = (90).to_bytes(4, "little", signed=True)
        image[28:32] = (200).to_bytes(4, "little", signed=True)
        image[32:36] = (7).to_bytes(4, "little", signed=True)
        image[36:40] = (7).to_bytes(4, "little", signed=True)
        image[40:44] = (1).to_bytes(4, "little", signed=True)
        instance = adapter.start_process(path, bytes(image))
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()
        monitor.enable_health_maintenance(80)
        monitor.enable_heat_maintenance(30)

        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.active_effects, ("health", "heat"))
        reader = adapter.open_process(instance)
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 16, 4), "little", signed=True),
            80,
        )
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 24, 4), "little", signed=True),
            30,
        )

        writer = adapter.open_process(instance, writable=True)
        writer.write(instance.image_base + 36, (9).to_bytes(4, "little", signed=True))
        snapshot = monitor.tick_effects()
        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("等待", snapshot.message)

        denied_adapter = FakeProcessMemoryAdapter()
        denied_adapter.start_process(path, bytes(image), writable=False)
        denied_monitor = RealtimeMonitor(denied_adapter, _realtime_profile(path))
        denied_monitor.connect()
        denied_monitor.enable_health_maintenance(80)
        snapshot = denied_monitor.tick_effects()
        self.assertEqual(snapshot.state, "disconnected")
        self.assertFalse(snapshot.active_effects)
        self.assertIn("权限", snapshot.message)

    def test_continuous_tick_does_not_follow_replaced_pointer_chain(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        image_base = 0x400000
        image = bytearray(0x300)
        image[4:6] = b"\xaa\xbb"
        image[0x80:0x84] = (image_base + 0x180).to_bytes(4, "little")
        _write_player_object(image, 0x180, health=50, max_health=100)
        _write_player_object(image, 0x200, health=40, max_health=100)
        instance = adapter.start_process(path, bytes(image), image_base=image_base)
        monitor = RealtimeMonitor(adapter, _dynamic_realtime_profile(path))
        monitor.connect()
        monitor.enable_health_maintenance(80)
        writer = adapter.open_process(instance, writable=True)
        writer.write(image_base + 0x80, (image_base + 0x200).to_bytes(4, "little"))

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "connected_read_only")
        self.assertEqual(snapshot.active_effects, ())
        self.assertIn("对象", snapshot.message)
        reader = adapter.open_process(instance)
        self.assertEqual(int.from_bytes(reader.read(image_base + 0x210, 4), "little"), 40)

    def test_continuous_tick_rejects_stale_target_above_current_maximum(self) -> None:
        adapter = FakeProcessMemoryAdapter()
        path = Path("C:/game/BaldrForce.exe")
        instance = adapter.start_process(path, _player_image())
        monitor = RealtimeMonitor(adapter, _realtime_profile(path))
        monitor.connect()
        monitor.enable_health_maintenance(80)
        writer = adapter.open_process(instance, writable=True)
        writer.write(instance.image_base + 20, (60).to_bytes(4, "little", signed=True))

        snapshot = monitor.tick_effects()

        self.assertEqual(snapshot.state, "disconnected")
        self.assertIn("最大血量", snapshot.message)
        reader = adapter.open_process(instance)
        self.assertEqual(
            int.from_bytes(reader.read(instance.image_base + 16, 4), "little", signed=True),
            50,
        )


class _PointerSwapAdapter(FakeProcessMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.swap_address = 0
        self.swap_value = 0

    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        if writable and self.swap_address:
            writer = super().open_process(instance, writable=True)
            writer.write(self.swap_address, self.swap_value.to_bytes(4, "little"))
            writer.close()
        return super().open_process(instance, writable=writable)


class _FingerprintSwapAdapter(FakeProcessMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fingerprint_address = 0

    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        if writable and self.fingerprint_address:
            writer = super().open_process(instance, writable=True)
            writer.write(self.fingerprint_address, b"\x90\x90")
            writer.close()
        return super().open_process(instance, writable=writable)


class _PostWriteIdentitySwapAdapter(FakeProcessMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.trigger_address = 0
        self.identity_address = 0

    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        connection = super().open_process(instance, writable=writable)
        if not writable or not self.trigger_address:
            return connection
        return _PostWriteIdentitySwapMemory(
            connection,
            self.trigger_address,
            self.identity_address,
        )


class _PostWriteIdentitySwapMemory:
    def __init__(self, connection, trigger_address: int, identity_address: int):  # type: ignore[no-untyped-def]
        self._connection = connection
        self._trigger_address = trigger_address
        self._identity_address = identity_address

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
        if address == self._trigger_address:
            self._connection.write(
                self._identity_address,
                (9).to_bytes(4, "little", signed=True),
            )

    def is_alive(self) -> bool:
        return self._connection.is_alive()

    def close(self) -> None:
        self._connection.close()


class _PostWriteFingerprintSwapAdapter(FakeProcessMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.trigger_address = 0
        self.fingerprint_address = 0

    def open_process(self, instance, *, writable: bool = False):  # type: ignore[no-untyped-def]
        connection = super().open_process(instance, writable=writable)
        if not writable or not self.trigger_address:
            return connection
        return _PostWriteFingerprintSwapMemory(
            connection,
            self.trigger_address,
            self.fingerprint_address,
        )


class _PostWriteFingerprintSwapMemory:
    def __init__(self, connection, trigger_address: int, fingerprint_address: int):  # type: ignore[no-untyped-def]
        self._connection = connection
        self._trigger_address = trigger_address
        self._fingerprint_address = fingerprint_address

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
        if address == self._trigger_address:
            self._connection.write(self._fingerprint_address, b"\x90\x90")

    def is_alive(self) -> bool:
        return self._connection.is_alive()

    def close(self) -> None:
        self._connection.close()


def _player_image() -> bytes:
    image = bytearray(64)
    image[4:6] = b"\xaa\xbb"
    image[16:20] = (50).to_bytes(4, "little", signed=True)
    image[20:24] = (100).to_bytes(4, "little", signed=True)
    image[24:28] = (90).to_bytes(4, "little", signed=True)
    image[28:32] = (200).to_bytes(4, "little", signed=True)
    image[32:36] = (7).to_bytes(4, "little", signed=True)
    image[36:40] = (7).to_bytes(4, "little", signed=True)
    image[40:44] = (1).to_bytes(4, "little", signed=True)
    return bytes(image)


def _write_player_object(
    image: bytearray,
    offset: int,
    *,
    health: int,
    max_health: int,
) -> None:
    image[offset + 0x10 : offset + 0x14] = health.to_bytes(4, "little", signed=True)
    image[offset + 0x14 : offset + 0x18] = max_health.to_bytes(4, "little", signed=True)
    image[offset + 0x18 : offset + 0x1C] = (90).to_bytes(4, "little", signed=True)
    image[offset + 0x1C : offset + 0x20] = (200).to_bytes(4, "little", signed=True)
    image[offset + 0x20 : offset + 0x24] = (7).to_bytes(4, "little", signed=True)
    image[offset + 0x24 : offset + 0x28] = (7).to_bytes(4, "little", signed=True)
    image[offset + 0x28 : offset + 0x2C] = (1).to_bytes(4, "little", signed=True)


def _realtime_profile(
    path: Path,
    *,
    permissions: EffectPermissions | None = None,
) -> RealtimeProfile:
    effective_permissions = permissions or EffectPermissions(
        health_restore=True,
        health_maintenance=True,
        heat_reduce=True,
        heat_maintenance=True,
    )
    return RealtimeProfile(
        executable_path=path,
        architecture="x86",
        fingerprints=(InstructionFingerprint("probe", 4, b"\xaa\xbb"),),
        player_state=PlayerStateLayout(
            health=IntegerField(16, 4, 0, 1_000_000),
            max_health=IntegerField(20, 4, 1, 1_000_000),
            heat=IntegerField(24, 4, 0, 1_000_000),
            max_heat=IntegerField(28, 4, 1, 1_000_000),
            player_identity=IntegerField(32, 4, 0, 1_000_000),
            object_identity=IntegerField(36, 4, 0, 1_000_000),
            battle_state=IntegerField(40, 4, 0, 10),
            battle_scenes=(BattleSceneSignature("test", 1),),
        ),
        permissions=effective_permissions,
        heat_write=HeatWriteLayout() if effective_permissions.heat_reduce else None,
    )


def _dynamic_realtime_profile(path: Path) -> RealtimeProfile:
    profile = _realtime_profile(path)
    assert profile.player_state is not None
    layout = profile.player_state
    return RealtimeProfile(
        executable_path=profile.executable_path,
        architecture=profile.architecture,
        fingerprints=profile.fingerprints,
        player_state=PlayerStateLayout(
            health=layout.health,
            max_health=layout.max_health,
            heat=layout.heat,
            max_heat=layout.max_heat,
            player_identity=layout.player_identity,
            object_identity=layout.object_identity,
            battle_state=layout.battle_state,
            battle_scenes=layout.battle_scenes,
            root=AddressPath(0x80, (0,)),
        ),
        permissions=profile.permissions,
        heat_write=profile.heat_write,
    )


if __name__ == "__main__":
    unittest.main()
