"""统一描述离线、Force Crash 与实时能力的证据和发布级别。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .realtime_profiles import RealtimeCapabilityLevel, RealtimeProfileDefinition
from .versioning import InstallationIdentity

CapabilityId: TypeAlias = Literal[
    "offline_read",
    "offline_weapon_edit",
    "force_crash_unlock",
    "realtime_connection",
    "realtime_player_hud",
    "realtime_health_restore",
    "realtime_health_maintenance",
    "realtime_heat_reduce",
    "realtime_heat_maintenance",
    "realtime_force_crash_refill",
    "realtime_force_crash_maintenance",
    "realtime_weapon_state",
    "realtime_weapon_repeat",
]
CapabilityState: TypeAlias = Literal[
    "unavailable",
    "read_only",
    "experimental_write",
    "verified_write",
]

OFFLINE_READ: CapabilityId = "offline_read"
OFFLINE_WEAPON_EDIT: CapabilityId = "offline_weapon_edit"
FORCE_CRASH_UNLOCK: CapabilityId = "force_crash_unlock"
REALTIME_CONNECTION: CapabilityId = "realtime_connection"
REALTIME_PLAYER_HUD: CapabilityId = "realtime_player_hud"
REALTIME_HEALTH_RESTORE: CapabilityId = "realtime_health_restore"
REALTIME_HEALTH_MAINTENANCE: CapabilityId = "realtime_health_maintenance"
REALTIME_HEAT_REDUCE: CapabilityId = "realtime_heat_reduce"
REALTIME_HEAT_MAINTENANCE: CapabilityId = "realtime_heat_maintenance"
REALTIME_FORCE_CRASH_REFILL: CapabilityId = "realtime_force_crash_refill"
REALTIME_FORCE_CRASH_MAINTENANCE: CapabilityId = "realtime_force_crash_maintenance"
REALTIME_WEAPON_STATE: CapabilityId = "realtime_weapon_state"
REALTIME_WEAPON_REPEAT: CapabilityId = "realtime_weapon_repeat"


@dataclass(frozen=True)
class Capability:
    id: CapabilityId
    display_name: str
    state: CapabilityState
    reasons: tuple[str, ...]
    required_installation_fingerprints: tuple[str, ...] = ()
    required_instruction_fingerprints: tuple[str, ...] = ()
    required_player_layout: bool = False
    requires_game_running: bool = False
    requires_game_stopped: bool = False


def build_capability_matrix(
    identity: InstallationIdentity,
    profile: RealtimeProfileDefinition | None,
) -> tuple[Capability, ...]:
    """从安装身份和研究 Profile 构造 GUI、诊断共用的能力矩阵。"""

    installation_requirements = tuple(identity.fingerprints)
    unknown_reasons = tuple(identity.reasons)
    offline_write_state: CapabilityState = (
        "verified_write" if identity.write_supported else "unavailable"
    )
    offline_reasons = (
        ("已通过安装指纹、候选重解码和字节白名单门禁",)
        if identity.write_supported
        else unknown_reasons
    )
    force_crash_state: CapabilityState = (
        "verified_write" if identity.write_supported else "unavailable"
    )
    force_crash_reasons = (
        (
            "离线记录映射、候选白名单与安全提交链路已验证",
            "21 项已完成隔离游戏内显示、装备、发动、重存、重启持久化和恢复验收",
        )
        if identity.write_supported
        else unknown_reasons
    )

    realtime_support: dict[CapabilityId, RealtimeCapabilityLevel] = (
        {support.capability_id: support.level for support in profile.supported_capabilities}
        if profile is not None
        else {}
    )
    realtime_fingerprints = (
        tuple(item.name for item in profile.instruction_fingerprints) if profile is not None else ()
    )
    profile_matches_installation = identity.is_known and profile is not None
    realtime_connection_state: CapabilityState = (
        realtime_support.get(REALTIME_CONNECTION, "unavailable")
        if profile_matches_installation
        else "unavailable"
    )
    realtime_connection_reasons = (
        (profile.read_validation,)
        if profile is not None
        and profile_matches_installation
        and realtime_connection_state != "unavailable"
        else ("当前安装没有匹配的版本化实时 Profile", *unknown_reasons)
    )
    missing_player_layout = ("真实玩家对象、字段值域、身份和战斗状态尚未完成直接取证",)

    return (
        Capability(
            OFFLINE_READ,
            "离线读取与诊断",
            "read_only",
            ("读取、备份和诊断不授权存档写入",),
        ),
        Capability(
            OFFLINE_WEAPON_EDIT,
            "普通武器离线编辑",
            offline_write_state,
            offline_reasons,
            installation_requirements,
            requires_game_stopped=True,
        ),
        Capability(
            FORCE_CRASH_UNLOCK,
            "Force Crash 离线解锁",
            force_crash_state,
            force_crash_reasons,
            installation_requirements,
            requires_game_stopped=True,
        ),
        Capability(
            REALTIME_CONNECTION,
            "只读进程连接",
            realtime_connection_state,
            realtime_connection_reasons,
            installation_requirements,
            realtime_fingerprints,
            requires_game_running=True,
        ),
        _profile_player_capability(
            REALTIME_PLAYER_HUD,
            "只读玩家战斗仪表",
            realtime_support,
            profile_matches_installation,
            profile,
            missing_player_layout,
        ),
        _profile_player_capability(
            REALTIME_HEALTH_RESTORE,
            "一次回血",
            realtime_support,
            profile_matches_installation,
            profile,
            missing_player_layout,
        ),
        _profile_player_capability(
            REALTIME_HEALTH_MAINTENANCE,
            "最低血量维持",
            realtime_support,
            profile_matches_installation,
            profile,
            missing_player_layout,
        ),
        _profile_player_capability(
            REALTIME_HEAT_REDUCE,
            "一次降热",
            realtime_support,
            profile_matches_installation,
            profile,
            missing_player_layout,
        ),
        _profile_player_capability(
            REALTIME_HEAT_MAINTENANCE,
            "热量阈值与连段武器恢复",
            realtime_support,
            profile_matches_installation,
            profile,
            missing_player_layout,
        ),
        _profile_player_capability(
            REALTIME_FORCE_CRASH_REFILL,
            "Force Crash 一次补充",
            realtime_support,
            profile_matches_installation,
            profile,
            ("Force Crash 战斗仪表尚未完成直接取证",),
        ),
        _profile_player_capability(
            REALTIME_WEAPON_STATE,
            "当前武器配置与连段历史",
            realtime_support,
            profile_matches_installation,
            profile,
            ("武器配置表、槽位和连段历史尚未完成直接取证",),
        ),
        _profile_player_capability(
            REALTIME_FORCE_CRASH_MAINTENANCE,
            "FC 满槽维持",
            realtime_support,
            profile_matches_installation,
            profile,
            ("FC 满槽维持未授权",),
        ),
        _profile_player_capability(
            REALTIME_WEAPON_REPEAT,
            "同连段武器可重复使用",
            realtime_support,
            profile_matches_installation,
            profile,
            ("普通槽门禁和历史安全边界尚未完成写入取证",),
        ),
    )


def capability_by_id(
    matrix: tuple[Capability, ...],
    capability_id: str,
) -> Capability:
    for capability in matrix:
        if capability.id == capability_id:
            return capability
    raise KeyError(capability_id)


def _profile_player_capability(
    capability_id: CapabilityId,
    display_name: str,
    support: dict[CapabilityId, RealtimeCapabilityLevel],
    profile_matches_installation: bool,
    profile: RealtimeProfileDefinition | None,
    reasons: tuple[str, ...],
) -> Capability:
    state: CapabilityState = (
        support.get(capability_id, "unavailable") if profile_matches_installation else "unavailable"
    )
    capability_reasons = (
        _profile_capability_reasons(profile, state) if state != "unavailable" else reasons
    )
    if capability_id == REALTIME_FORCE_CRASH_MAINTENANCE and state == "experimental_write":
        capability_reasons = (
            "复用已验证的 0–10000 仪表和一次补充写入；持续锁满已覆盖故障门禁，待用户实机复测",
        )
    return Capability(
        capability_id,
        display_name,
        state,
        capability_reasons,
        required_player_layout=True,
        requires_game_running=True,
    )


def _profile_capability_reasons(
    profile: RealtimeProfileDefinition | None,
    state: CapabilityState,
) -> tuple[str, ...]:
    assert profile is not None
    if state == "read_only":
        return (profile.read_validation,)
    return (profile.write_validation,)
