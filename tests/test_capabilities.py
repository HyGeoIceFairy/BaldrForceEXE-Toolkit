from __future__ import annotations

import unittest

from bfmod.capabilities import (
    FORCE_CRASH_UNLOCK,
    OFFLINE_WEAPON_EDIT,
    REALTIME_CONNECTION,
    REALTIME_FORCE_CRASH_MAINTENANCE,
    REALTIME_FORCE_CRASH_REFILL,
    REALTIME_HEALTH_MAINTENANCE,
    REALTIME_HEALTH_RESTORE,
    REALTIME_HEAT_MAINTENANCE,
    REALTIME_HEAT_REDUCE,
    REALTIME_PLAYER_HUD,
    build_capability_matrix,
    capability_by_id,
)
from bfmod.realtime_profiles import (
    KNOWN_REALTIME_PROFILE,
    ProfileCapabilitySupport,
    RealtimeProfileDefinition,
)
from bfmod.versioning import KNOWN_FINGERPRINTS, InstallationIdentity


class CapabilityMatrixTests(unittest.TestCase):
    def test_known_installation_exposes_only_evidence_backed_capability_levels(self) -> None:
        identity = _identity(known=True)

        matrix = build_capability_matrix(identity, KNOWN_REALTIME_PROFILE)

        self.assertEqual(capability_by_id(matrix, OFFLINE_WEAPON_EDIT).state, "verified_write")
        force_crash = capability_by_id(matrix, FORCE_CRASH_UNLOCK)
        self.assertEqual(force_crash.state, "verified_write")
        self.assertTrue(any("隔离游戏" in reason for reason in force_crash.reasons))
        self.assertTrue(any("持久化" in reason for reason in force_crash.reasons))
        self.assertEqual(capability_by_id(matrix, REALTIME_CONNECTION).state, "read_only")
        hud = capability_by_id(matrix, REALTIME_PLAYER_HUD)
        self.assertEqual(hud.state, "read_only")
        self.assertTrue(any("分段生命" in reason for reason in hud.reasons))
        health = capability_by_id(matrix, REALTIME_HEALTH_RESTORE)
        self.assertEqual(health.state, "verified_write")
        self.assertTrue(health.required_player_layout)
        self.assertTrue(any("隔离实机" in reason for reason in health.reasons))
        self.assertEqual(
            capability_by_id(matrix, REALTIME_HEAT_REDUCE).state,
            "verified_write",
        )
        self.assertEqual(
            capability_by_id(matrix, REALTIME_HEALTH_MAINTENANCE).state,
            "verified_write",
        )
        self.assertEqual(
            capability_by_id(matrix, REALTIME_HEAT_MAINTENANCE).state,
            "verified_write",
        )
        force_crash_meter = capability_by_id(matrix, REALTIME_FORCE_CRASH_REFILL)
        self.assertEqual(force_crash_meter.state, "verified_write")
        self.assertTrue(any("10000→9000→10000" in reason for reason in force_crash_meter.reasons))
        fc_lock = capability_by_id(matrix, REALTIME_FORCE_CRASH_MAINTENANCE)
        self.assertEqual(fc_lock.state, "verified_write")
        self.assertTrue(any("连续发动后补满正常" in reason for reason in fc_lock.reasons))

    def test_unknown_installation_never_inherits_profile_or_write_capabilities(self) -> None:
        identity = _identity(known=False)

        matrix = build_capability_matrix(identity, None)

        for capability_id in (
            OFFLINE_WEAPON_EDIT,
            FORCE_CRASH_UNLOCK,
            REALTIME_CONNECTION,
            REALTIME_PLAYER_HUD,
            REALTIME_HEALTH_RESTORE,
        ):
            capability = capability_by_id(matrix, capability_id)
            self.assertEqual(capability.state, "unavailable")
            self.assertTrue(capability.reasons)

    def test_profile_does_not_override_unknown_installation_gate(self) -> None:
        matrix = build_capability_matrix(_identity(known=False), KNOWN_REALTIME_PROFILE)

        connection = capability_by_id(matrix, REALTIME_CONNECTION)
        self.assertEqual(connection.state, "unavailable")
        self.assertTrue(any("Profile" in reason for reason in connection.reasons))

    def test_each_realtime_capability_uses_its_own_evidence_level(self) -> None:
        profile = RealtimeProfileDefinition(
            id="test-player-layout",
            display_name="test",
            required_installation_fingerprints=tuple(KNOWN_FINGERPRINTS.items()),
            architecture="x86",
            instruction_fingerprints=KNOWN_REALTIME_PROFILE.instruction_fingerprints,
            supported_capabilities=(
                ProfileCapabilitySupport("realtime_connection", "read_only"),
                ProfileCapabilitySupport("realtime_player_hud", "read_only"),
                ProfileCapabilitySupport("realtime_health_restore", "experimental_write"),
                ProfileCapabilitySupport("realtime_heat_reduce", "verified_write"),
            ),
            evidence_source="test",
            verified_date="2026-09-20",
            read_validation="玩家对象只读布局已验证",
            write_validation="逐项验证",
            known_failures=("持续写入尚未验证",),
        )

        matrix = build_capability_matrix(_identity(known=True), profile)

        self.assertEqual(capability_by_id(matrix, REALTIME_PLAYER_HUD).state, "read_only")
        self.assertEqual(
            capability_by_id(matrix, REALTIME_HEALTH_RESTORE).state,
            "experimental_write",
        )
        self.assertEqual(capability_by_id(matrix, REALTIME_HEAT_REDUCE).state, "verified_write")
        self.assertEqual(
            capability_by_id(matrix, REALTIME_HEALTH_MAINTENANCE).state,
            "unavailable",
        )
        self.assertEqual(
            capability_by_id(matrix, REALTIME_HEAT_MAINTENANCE).state,
            "unavailable",
        )

    def test_capability_ids_are_unique_and_unknown_lookup_is_rejected(self) -> None:
        matrix = build_capability_matrix(_identity(known=True), KNOWN_REALTIME_PROFILE)
        self.assertEqual(len({capability.id for capability in matrix}), len(matrix))
        with self.assertRaisesRegex(KeyError, "unknown"):
            capability_by_id(matrix, "unknown")


def _identity(*, known: bool) -> InstallationIdentity:
    fingerprints: dict[str, str | None] = dict(KNOWN_FINGERPRINTS)
    if not known:
        fingerprints["BaldrForce.exe"] = "0" * 64
    return InstallationIdentity(
        name="known" if known else "unknown",
        fingerprints=fingerprints,
        is_known=known,
        read_supported=True,
        write_supported=known,
        reasons=("matched",) if known else ("文件指纹不匹配：BaldrForce.exe",),
    )


if __name__ == "__main__":
    unittest.main()
