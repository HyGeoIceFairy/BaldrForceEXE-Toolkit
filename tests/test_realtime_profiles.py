from __future__ import annotations

import unittest
from pathlib import Path

from bfmod.capabilities import (
    REALTIME_CONNECTION,
    REALTIME_ENEMY_CLEAR,
    REALTIME_ENEMY_CLEAR_MAINTENANCE,
    REALTIME_FORCE_CRASH_MAINTENANCE,
    REALTIME_FORCE_CRASH_REFILL,
    REALTIME_HEALTH_MAINTENANCE,
    REALTIME_HEALTH_RESTORE,
    REALTIME_HEAT_MAINTENANCE,
    REALTIME_HEAT_REDUCE,
    REALTIME_PLAYER_HUD,
    REALTIME_WAVE_SKIP,
    REALTIME_WEAPON_REPEAT,
    REALTIME_WEAPON_STATE,
)
from bfmod.realtime import HeatWriteLayout, InstructionFingerprint
from bfmod.realtime_profiles import (
    KNOWN_REALTIME_PROFILE,
    ProfileCapabilitySupport,
    RealtimeProfileDefinition,
    select_realtime_profile,
)
from bfmod.versioning import KNOWN_FINGERPRINTS, InstallationIdentity


class RealtimeProfileRegistryTests(unittest.TestCase):
    def test_profile_rejects_continuous_capability_without_one_shot_predecessor(self) -> None:
        common = dict(
            id="invalid",
            display_name="invalid",
            required_installation_fingerprints=(("BaldrForce.exe", "ABC"),),
            architecture="x86",
            instruction_fingerprints=(InstructionFingerprint("probe", 0, b"\x90"),),
            evidence_source="test",
            verified_date="2026-09-21",
            read_validation="test",
            write_validation="test",
            known_failures=(),
        )
        with self.assertRaisesRegex(ValueError, "一次回血"):
            RealtimeProfileDefinition(
                **common,
                supported_capabilities=(
                    ProfileCapabilitySupport(
                        "realtime_health_maintenance",
                        "experimental_write",
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "一次降热"):
            RealtimeProfileDefinition(
                **common,
                supported_capabilities=(
                    ProfileCapabilitySupport(
                        "realtime_heat_maintenance",
                        "verified_write",
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "一次回血"):
            RealtimeProfileDefinition(
                **common,
                supported_capabilities=(
                    ProfileCapabilitySupport("realtime_health_restore", "read_only"),
                    ProfileCapabilitySupport(
                        "realtime_health_maintenance",
                        "experimental_write",
                    ),
                ),
            )

    def test_exact_installation_fingerprints_select_versioned_realtime_profile(self) -> None:
        game_root = Path("C:/Game/Baldr Force Exe")
        identity = _identity(dict(KNOWN_FINGERPRINTS), known=True)

        selected = select_realtime_profile(identity, game_root)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.definition, KNOWN_REALTIME_PROFILE)
        self.assertEqual(selected.profile.executable_path, game_root / "BaldrForce.exe")
        self.assertEqual(selected.profile.architecture, "x86")
        layout = selected.profile.player_state
        self.assertIsNotNone(layout)
        assert layout is not None
        self.assertEqual(layout.root.root_relative_address, 0x138A00)
        self.assertEqual(layout.root.pointer_offsets, (0x38, 0))
        self.assertEqual(
            (layout.health.relative_address, layout.max_health.relative_address),
            (0x16, 0x1A),
        )
        self.assertEqual(
            (layout.heat.relative_address, layout.max_heat.relative_address),
            (0x20, 0x22),
        )
        self.assertIsNotNone(layout.force_crash)
        assert layout.force_crash is not None
        self.assertEqual(
            (
                layout.force_crash.relative_address,
                layout.force_crash.size,
                layout.force_crash.minimum,
                layout.force_crash.maximum,
            ),
            (0x3A, 2, 0, 10000),
        )
        self.assertIsNotNone(layout.segmented_health)
        assert layout.segmented_health is not None
        self.assertEqual(layout.segmented_health.remaining_segments.relative_address, 0x18)
        self.assertEqual(layout.segmented_health.segment_capacity, 0x820)
        self.assertEqual(
            tuple(
                (scene.name, scene.battle_state, scene.scene_guard_values)
                for scene in layout.battle_scenes
            ),
            (("普通战斗", 0, (0,)), ("练习战斗", 3, (1,))),
        )
        self.assertEqual(len(layout.guards), 3)
        self.assertEqual(len(layout.scene_guards), 1)
        self.assertEqual(len(layout.write_inhibitors), 1)
        self.assertEqual(
            tuple(fingerprint.relative_address for fingerprint in selected.profile.fingerprints),
            (
                0x3DDD0,
                0x651B1,
                0x7B47C,
                0x1C0B4,
                0x1AB3F,
                0x53175,
                0x43FE3,
                0x4113F,
                0x1AF2F,
                0x188B8,
                0x1BC99,
                0x18220,
                0x18377,
                0x3F3BA,
                0x1AE82,
                0x1B240,
                0x1B280,
                0x4AE82,
                0x4BE7D,
                0x4D44F,
                0x4D54D,
                0x4D687,
                0x4EF52,
            ),
        )
        self.assertEqual(
            tuple(support.capability_id for support in selected.definition.supported_capabilities),
            (
                REALTIME_CONNECTION,
                REALTIME_PLAYER_HUD,
                REALTIME_HEALTH_RESTORE,
                REALTIME_HEAT_REDUCE,
                REALTIME_HEALTH_MAINTENANCE,
                REALTIME_HEAT_MAINTENANCE,
                REALTIME_FORCE_CRASH_REFILL,
                REALTIME_FORCE_CRASH_MAINTENANCE,
                REALTIME_WEAPON_STATE,
                REALTIME_WEAPON_REPEAT,
                REALTIME_ENEMY_CLEAR,
                REALTIME_ENEMY_CLEAR_MAINTENANCE,
                REALTIME_WAVE_SKIP,
            ),
        )
        self.assertEqual(
            selected.definition.supported_capabilities[-1].level,
            "verified_write",
        )
        self.assertIn("隔离实机", selected.definition.write_validation)
        self.assertTrue(selected.profile.permissions.health_restore)
        self.assertTrue(selected.profile.permissions.health_maintenance)
        self.assertTrue(selected.profile.permissions.heat_reduce)
        self.assertTrue(selected.profile.permissions.heat_maintenance)
        self.assertTrue(selected.profile.permissions.force_crash_refill)
        self.assertTrue(selected.profile.permissions.weapon_repeat)
        self.assertIsNotNone(selected.profile.segmented_health_write)
        self.assertIsNotNone(selected.profile.heat_write)
        assert selected.profile.heat_write is not None
        self.assertEqual(selected.profile.heat_write.natural_zero_transition, 1)
        self.assertIsNotNone(selected.profile.force_crash_write)
        self.assertIsNotNone(selected.profile.weapon_state)
        self.assertIsNotNone(selected.profile.weapon_repeat_write)
        assert selected.profile.weapon_state is not None
        assert selected.profile.weapon_repeat_write is not None
        self.assertEqual(
            (
                selected.profile.weapon_state.ordinary_slot_count,
                selected.profile.weapon_state.total_slot_count,
                selected.profile.weapon_state.history_capacity,
                selected.profile.weapon_repeat_write.safe_history_count,
            ),
            (12, 13, 25, 23),
        )

    def test_natural_zero_transition_must_be_a_positive_integer(self) -> None:
        for value in (0, -1, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "正整数"):
                    HeatWriteLayout(natural_zero_transition=value)

    def test_unknown_or_partially_matching_installation_has_no_realtime_profile(self) -> None:
        fingerprints = dict(KNOWN_FINGERPRINTS)
        fingerprints["Table.pac"] = "F" * 64

        self.assertIsNone(
            select_realtime_profile(
                _identity(fingerprints, known=False),
                Path("C:/Game/Baldr Force Exe"),
            )
        )

    def test_claimed_known_identity_still_requires_exact_complete_fingerprints(self) -> None:
        incomplete: dict[str, str | None] = dict(KNOWN_FINGERPRINTS)
        incomplete.pop("Table.pac")

        self.assertIsNone(
            select_realtime_profile(
                _identity(incomplete, known=True),
                Path("C:/Game/Baldr Force Exe"),
            )
        )


def _identity(
    fingerprints: dict[str, str | None],
    *,
    known: bool,
) -> InstallationIdentity:
    return InstallationIdentity(
        name="known" if known else "unknown",
        fingerprints=fingerprints,
        is_known=known,
        read_supported=True,
        write_supported=known,
        reasons=("matched",) if known else ("mismatch",),
    )


if __name__ == "__main__":
    unittest.main()
