"""由安装内容指纹选择的版本化实时能力配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from .combat import EnemyClearLayout
from .realtime import (
    AddressPath,
    BattleSceneSignature,
    EffectPermissions,
    ForceCrashWriteLayout,
    HeatWriteLayout,
    InstructionFingerprint,
    IntegerField,
    PlayerStateLayout,
    RealtimeProfile,
    SegmentedHealthLayout,
    SegmentedHealthWriteLayout,
    WeaponRepeatWriteLayout,
    WeaponStateLayout,
)
from .versioning import KNOWN_FINGERPRINTS, InstallationIdentity
from .waves import WaveSkipLayout

RealtimeCapabilityId: TypeAlias = Literal[
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
    "realtime_enemy_clear",
    "realtime_enemy_clear_maintenance",
    "realtime_wave_skip",
]
RealtimeCapabilityLevel: TypeAlias = Literal[
    "read_only",
    "experimental_write",
    "verified_write",
]


@dataclass(frozen=True)
class ProfileCapabilitySupport:
    capability_id: RealtimeCapabilityId
    level: RealtimeCapabilityLevel


@dataclass(frozen=True)
class RealtimeProfileDefinition:
    """不含本地路径的研究证据；运行时按安装目录生成连接配置。"""

    id: str
    display_name: str
    required_installation_fingerprints: tuple[tuple[str, str], ...]
    architecture: str
    instruction_fingerprints: tuple[InstructionFingerprint, ...]
    supported_capabilities: tuple[ProfileCapabilitySupport, ...]
    evidence_source: str
    verified_date: str
    read_validation: str
    write_validation: str
    known_failures: tuple[str, ...]

    def __post_init__(self) -> None:
        support = {item.capability_id: item.level for item in self.supported_capabilities}
        if len(support) != len(self.supported_capabilities):
            raise ValueError("实时 Profile 不能重复声明同一能力")
        health_maintenance = support.get("realtime_health_maintenance")
        if health_maintenance in {"experimental_write", "verified_write"} and support.get(
            "realtime_health_restore"
        ) not in {"experimental_write", "verified_write"}:
            raise ValueError("持续血量能力必须先声明一次回血能力")
        heat_maintenance = support.get("realtime_heat_maintenance")
        if heat_maintenance in {"experimental_write", "verified_write"} and support.get(
            "realtime_heat_reduce"
        ) not in {"experimental_write", "verified_write"}:
            raise ValueError("持续热量能力必须先声明一次降热能力")
        if support.get("realtime_force_crash_maintenance") in {
            "experimental_write",
            "verified_write",
        } and support.get("realtime_force_crash_refill") not in {
            "experimental_write",
            "verified_write",
        }:
            raise ValueError("持续 FC 能力必须先声明一次补充能力")
        if support.get("realtime_enemy_clear_maintenance") in {
            "experimental_write",
            "verified_write",
        } and support.get("realtime_enemy_clear") not in {
            "experimental_write",
            "verified_write",
        }:
            raise ValueError("自动清敌必须先声明一次清敌能力")
        if support.get("realtime_wave_skip") in {
            "experimental_write",
            "verified_write",
        } and support.get("realtime_enemy_clear_maintenance") not in {
            "experimental_write",
            "verified_write",
        }:
            raise ValueError("跳过刷波等待必须先声明自动清敌能力")


@dataclass(frozen=True)
class SelectedRealtimeProfile:
    definition: RealtimeProfileDefinition
    profile: RealtimeProfile


KNOWN_PLAYER_STATE_LAYOUT = PlayerStateLayout(
    health=IntegerField(0x16, 2, 0, 0x820),
    max_health=IntegerField(0x1A, 2, 0, 31),
    heat=IntegerField(0x20, 2, 0, 130),
    max_heat=IntegerField(0x22, 2, 130, 130),
    player_identity=IntegerField(4, 4, 0, 0, path=AddressPath(0x138A00, (0x68, 0))),
    object_identity=IntegerField(8, 4, 0, 0, path=AddressPath(0x138A00, (0x68, 0))),
    battle_state=IntegerField(0xE0624, 1, 0, 3, path=AddressPath()),
    battle_scenes=(
        BattleSceneSignature("普通战斗", 0, (0,)),
        BattleSceneSignature("练习战斗", 3, (1,)),
    ),
    root=AddressPath(0x138A00, (0x38, 0)),
    segmented_health=SegmentedHealthLayout(IntegerField(0x18, 2, -1, 31), 0x820),
    force_crash=IntegerField(0x3A, 2, 0, 10000),
    guards=(
        IntegerField(0, 1, 1, 1, path=AddressPath(0x138A00, (0,))),
        IntegerField(1, 1, 5, 5, path=AddressPath(0x138A00, (0,))),
        IntegerField(3, 1, 0, 0, path=AddressPath(0x138A00, (0x68, 0))),
    ),
    scene_guards=(IntegerField(0xE0625, 1, 0, 1, path=AddressPath()),),
    write_inhibitors=(IntegerField(0xE0626, 1, 0, 31, path=AddressPath()),),
)


KNOWN_REALTIME_PROFILE = RealtimeProfileDefinition(
    id="zh-integrated-2026-09-player-hud",
    display_name="BALDR FORCE EXE 中文整合版（2026-09 玩家战斗 HUD）",
    required_installation_fingerprints=tuple(KNOWN_FINGERPRINTS.items()),
    architecture="x86",
    instruction_fingerprints=(
        InstructionFingerprint("存档成长块路径", 0x3DDD0, bytes.fromhex("8B44240453558B6C")),
        InstructionFingerprint("开发完成路径", 0x651B1, bytes.fromhex("C604D5AC907C0005")),
        InstructionFingerprint("Force Crash 界面", 0x7B47C, bytes.fromhex("BFACA27C008DB588")),
        InstructionFingerprint(
            "战斗对象表构造", 0x1C0B4, bytes.fromhex("8B442420893C85008A53008B")
        ),
        InstructionFingerprint(
            "Force Crash 仪表累加与钳制",
            0x1AB3F,
            bytes.fromhex("0FBF7E3AE818CA0300668B54241883C4086601563A"),
        ),
        InstructionFingerprint(
            "Force Crash 满槽发动门禁",
            0x53175,
            bytes.fromhex("6681783A10277C728A484084C9756B"),
        ),
        InstructionFingerprint(
            "Force Crash 发动清零",
            0x43FE3,
            bytes.fromhex("66C7473A00008B466883C40CC680C401000001"),
        ),
        InstructionFingerprint("生命段回写", 0x4113F, bytes.fromhex("668B46165355578B7C241C0F")),
        InstructionFingerprint("热量上限钳制", 0x1AF2F, bytes.fromhex("663D82007C0C66C746208200")),
        InstructionFingerprint(
            "玩家输入更新",
            0x188B8,
            bytes.fromhex("8B0D0CE27C0083E10F8B5C8C10"),
        ),
        InstructionFingerprint(
            "玩家更新注册",
            0x1BC99,
            bytes.fromhex("6A0068008841006A016A0156E8"),
        ),
        InstructionFingerprint("普通武器槽使用门禁", 0x18220, bytes.fromhex("8A4C3B5C84C97415")),
        InstructionFingerprint(
            "普通武器槽成功占用",
            0x18377,
            bytes.fromhex("8B7C2418C6443E5C01"),
        ),
        InstructionFingerprint("实际武器连段历史追加", 0x3F3BA, bytes.fromhex("0FBE8B46010000")),
        InstructionFingerprint(
            "连段结束武器状态恢复",
            0x1AE82,
            bytes.fromhex("8DBE14010000B90C000000B819FC19FC889E46010000F3AB66AB"),
        ),
        InstructionFingerprint(
            "原生退场计时设置与剧情保护",
            0x1B240,
            bytes.fromhex(
                "8B44240456578B7C24108B703885FF7E1F8B40686A008B4804516A03E8EFFA0200"
                "83C40C83F80174076689BE4E0100005F5EC3"
            ),
        ),
        InstructionFingerprint(
            "原生计时退场与事件推进",
            0x1B280,
            bytes.fromhex(
                "568B7424088B4E38668B814E0100006685C0750433C05EC3486685C06689814E010000"
                "7E0433C05EC38B4668807803017505E8192103006A0156E891FCFFFF83C408B8010000005EC3"
            ),
        ),
        InstructionFingerprint(
            "剧情事件按槽位拦截退场",
            0x4AE82,
            bytes.fromhex(
                "8B4C242085C9741385D20F8406FFFFFF66837A18000F8CFBFEFFFF8B0C8D008A53008B37"
                "8B49683B710475B52C04895C2414F6D81BC024F283C00F"
            ),
        ),
        InstructionFingerprint(
            "原生阵营存活计数递减",
            0x4BE7D,
            bytes.fromhex("8B048D008A53008B48680FBE41038A8860495700FEC9888860495700B801000000C3"),
        ),
        InstructionFingerprint(
            "原生全灭后增援时钟快进",
            0x4D44F,
            bytes.fromhex(
                "C746043F420F008D4E10BF40000000B8010000003841FC750C8B118B5E043BD37D0389560483C1204F75E9"
            ),
        ),
        InstructionFingerprint(
            "增援时钟与同屏限制",
            0x4D54D,
            bytes.fromhex(
                "807EF801751C3B1E7D188B4D048B46FC3BC87C0E57E83900000083C40485C0741D434783C62083FF407CD58B45045F405E894504"
            ),
        ),
        InstructionFingerprint(
            "增援记录消耗与计数", 0x4D687, bytes.fromhex("C6430C028B0F83C41449890F")
        ),
        InstructionFingerprint(
            "独立实际战斗计时",
            0x4EF52,
            bytes.fromhex("A128064E008B0D2C064E00406A0141506A00A328064E00890D2C064E00E8DCBDFFFF"),
        ),
    ),
    supported_capabilities=(
        ProfileCapabilitySupport("realtime_connection", "read_only"),
        ProfileCapabilitySupport("realtime_player_hud", "read_only"),
        ProfileCapabilitySupport("realtime_health_restore", "verified_write"),
        ProfileCapabilitySupport("realtime_heat_reduce", "verified_write"),
        ProfileCapabilitySupport("realtime_health_maintenance", "verified_write"),
        ProfileCapabilitySupport("realtime_heat_maintenance", "verified_write"),
        ProfileCapabilitySupport("realtime_force_crash_refill", "verified_write"),
        ProfileCapabilitySupport("realtime_force_crash_maintenance", "verified_write"),
        ProfileCapabilitySupport("realtime_weapon_state", "read_only"),
        ProfileCapabilitySupport("realtime_weapon_repeat", "verified_write"),
        ProfileCapabilitySupport("realtime_enemy_clear", "verified_write"),
        ProfileCapabilitySupport("realtime_enemy_clear_maintenance", "verified_write"),
        ProfileCapabilitySupport("realtime_wave_skip", "verified_write"),
    ),
    evidence_source="本地受控取证记录（未纳入发行包）",
    verified_date="2026-10-30",
    read_validation=(
        "路径、进程实例、x86 架构、模块 RVA 指纹、动态对象链、玩家控制更新、"
        "玩家身份、普通与练习战斗场景、分段生命、普通热量和上限 10000 的 Force "
        "Crash 仪表、13 个状态槽实际武器 ID、最近槽及当前连段实际武器历史均已通过独立会话只读验证"
    ),
    write_validation=(
        "隔离实机已验证一次回血 4564→6240、一次降热与持续热量纠正；目标 0 通过"
        "最小正值交还游戏自然归零，保留连段武器恢复路径。对象替换、门禁、死亡、指纹、进程实例和"
        "权限故障均由自动测试验证为失效停写；自动重连后仅按用户已配置方案重新验证恢复。"
        "Force Crash 仪表"
        "已在隔离练习进程完成 10000→9000→10000 的受控一次补充与写后复核。武器重复仅清除"
        "12 个普通槽门禁，并以 24→23 回退保护 25 项历史；隔离练习中已完成同一实际武器 ID 0"
        "连续 26 次真实命中，总伤害 1680、Force Crash 自然增长 3360，且保留原生伤害衰减与连段恢复。"
        "2026-10-30 用户实机确认 HUD 置顶与勾选、跨场次恢复、战前配置、"
        "FC 满槽维持及连续发动后补满正常。普通战斗已完成原生清敌、跨关及增援快进验收："
        "增援时钟 12→500 时实际战斗计时维持 7，随后增援正常出场、击败、结算和处理成长；"
        "用户确认胜利、奖励和后续剧情正常，并于 2026-10-30 确认候选版可正式发布"
    ),
    known_failures=(
        "只在已验证的普通或练习战斗场景显示；对白、菜单、结算及对象切换期间主动清空",
        "Force Crash 发动瞬态保持只读 HUD，但暂停所有一次和持续写入",
        "持续效果采用轮询纠正；热量功能仍调用游戏自身归零路径，武器重复功能独立维护普通槽门禁",
        "不修改游戏程序、不注入代码",
        "清敌仅限已验证普通战斗：剧情保护对象跳过；其他关卡、首领及特殊战斗不扩大为已验收声明",
        "刷波加速只在场上非友军均移除且队列受支持时推进独立增援时钟；不修改实际战斗计时、结果与脚本",
    ),
)

REALTIME_PROFILE_REGISTRY = (KNOWN_REALTIME_PROFILE,)


def select_realtime_profile(
    identity: InstallationIdentity,
    game_root: str | Path,
) -> SelectedRealtimeProfile | None:
    """仅当安装的完整指纹集合与证据配置精确匹配时选择 Profile。"""

    for definition in REALTIME_PROFILE_REGISTRY:
        if not identity.is_known:
            continue
        if any(
            identity.fingerprints.get(relative) != expected
            for relative, expected in definition.required_installation_fingerprints
        ):
            continue
        return SelectedRealtimeProfile(
            definition,
            RealtimeProfile(
                executable_path=Path(game_root) / "BaldrForce.exe",
                architecture=definition.architecture,
                fingerprints=definition.instruction_fingerprints,
                player_state=KNOWN_PLAYER_STATE_LAYOUT,
                permissions=EffectPermissions(
                    health_restore=True,
                    health_maintenance=True,
                    heat_reduce=True,
                    heat_maintenance=True,
                    force_crash_refill=True,
                    force_crash_maintenance=True,
                    weapon_repeat=True,
                    enemy_clear=True,
                    enemy_clear_maintenance=True,
                    wave_skip=True,
                ),
                segmented_health_write=SegmentedHealthWriteLayout(),
                # 游戏只在旧热量大于零且本帧自然冷却到零时恢复连段武器。
                heat_write=HeatWriteLayout(natural_zero_transition=1),
                force_crash_write=ForceCrashWriteLayout(),
                weapon_state=WeaponStateLayout(
                    combo_active=IntegerField(0x3C, 1, 0, 1, signed=False),
                    slot_locks_relative_address=0x5C,
                    recent_slot=IntegerField(0x108, 1, 0, 255, signed=False),
                    history_relative_address=0x114,
                    history_count=IntegerField(0x146, 1, 0, 255, signed=False),
                    configuration_pointer_relative_address=0x164,
                    weapon_ids_pointer_relative_address=0x108,
                    weapon_id_minimum=0,
                    weapon_id_maximum=255,
                ),
                weapon_repeat_write=WeaponRepeatWriteLayout(safe_history_count=23),
                enemy_clear=EnemyClearLayout(),
                wave_skip=WaveSkipLayout(),
            ),
        )
    return None
