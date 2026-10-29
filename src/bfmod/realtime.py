"""实时修改器的连接门禁、玩家监控和逐项授权写入。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from struct import pack, unpack
from typing import Literal

from .combat import (
    CombatFrameChangedError,
    CombatValidationError,
    EnemyClearLayout,
    EnemyClearResult,
    read_actor,
    read_combat_frame,
)
from .process_memory import (
    ProcessInstance,
    ProcessMemory,
    ProcessMemoryAdapter,
    ProcessMemoryError,
    normalized_executable_path,
)
from .waves import WaveSkipLayout, read_wave_schedule


class RealtimeConnectionError(RuntimeError):
    """目标进程不能满足唯一实例、路径、架构或指纹门禁。"""


class RealtimeEffectError(RuntimeError):
    """玩家状态未通过校验或实时效果无法安全执行。"""


class RealtimeWritePausedError(RealtimeEffectError):
    """已验证的瞬态允许继续读取，但当前帧禁止写入。"""


class RealtimePlayerUnavailableError(RealtimeEffectError):
    """进程连接仍有效，但当前没有可安全修改的玩家战斗状态。"""


class RealtimeFrameUnstableError(RealtimeEffectError):
    """游戏正常更新与采样重叠；本帧不得写入，但会话及目标仍有效。"""


@dataclass(frozen=True)
class InstructionFingerprint:
    name: str
    relative_address: int
    expected: bytes

    def __post_init__(self) -> None:
        if self.relative_address < 0 or not self.expected:
            raise ValueError("指令指纹必须包含非负相对地址和非空字节")


@dataclass(frozen=True)
class IntegerField:
    relative_address: int
    size: int
    minimum: int
    maximum: int
    signed: bool = True
    path: AddressPath | None = None

    def __post_init__(self) -> None:
        if self.relative_address < 0 or self.size not in (1, 2, 4, 8):
            raise ValueError("整数内存字段的相对地址或大小无效")
        if self.minimum > self.maximum:
            raise ValueError("整数内存字段的值域无效")


@dataclass(frozen=True)
class AddressPath:
    """从模块 RVA 根开始，按偏移逐级解引用到动态对象。"""

    root_relative_address: int = 0
    pointer_offsets: tuple[int, ...] = ()
    pointer_size: int = 4

    def __post_init__(self) -> None:
        if self.root_relative_address < 0 or any(offset < 0 for offset in self.pointer_offsets):
            raise ValueError("地址链的模块相对地址和指针偏移必须为非负数")
        if self.pointer_size not in (4, 8):
            raise ValueError("地址链只支持 32 位或 64 位指针")
        if self.root_relative_address % self.pointer_size != 0:
            raise ValueError("地址链根必须按指针宽度对齐")
        if len(self.pointer_offsets) > 8:
            raise ValueError("地址链最多允许八级解引用")


@dataclass(frozen=True)
class EffectPermissions:
    """逐项写入授权；有布局并不等于有写入证据。"""

    health_restore: bool = False
    health_maintenance: bool = False
    heat_reduce: bool = False
    heat_maintenance: bool = False
    force_crash_refill: bool = False
    weapon_repeat: bool = False
    force_crash_maintenance: bool = False
    enemy_clear: bool = False
    enemy_clear_maintenance: bool = False
    wave_skip: bool = False

    def __post_init__(self) -> None:
        if self.health_maintenance and not self.health_restore:
            raise ValueError("持续血量写入必须先具有一次回血授权")
        if self.heat_maintenance and not self.heat_reduce:
            raise ValueError("持续热量写入必须先具有一次降热授权")
        if self.force_crash_maintenance and not self.force_crash_refill:
            raise ValueError("持续 FC 写入必须先具有一次补充授权")
        if self.enemy_clear_maintenance and not self.enemy_clear:
            raise ValueError("自动清敌必须先具有一次清敌授权")
        if self.wave_skip and not self.enemy_clear_maintenance:
            raise ValueError("跳过刷波等待必须先具有自动清敌授权")


@dataclass(frozen=True)
class BattleSceneSignature:
    """一个已验证战斗模式及其关联场景字段值。"""

    name: str
    battle_state: int
    scene_guard_values: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("战斗场景签名必须具有名称")


@dataclass(frozen=True)
class PlayerStateLayout:
    health: IntegerField
    max_health: IntegerField
    heat: IntegerField
    max_heat: IntegerField
    player_identity: IntegerField
    object_identity: IntegerField
    battle_state: IntegerField
    battle_scenes: tuple[BattleSceneSignature, ...]
    root: AddressPath = AddressPath()
    segmented_health: SegmentedHealthLayout | None = None
    force_crash: IntegerField | None = None
    guards: tuple[IntegerField, ...] = ()
    scene_guards: tuple[IntegerField, ...] = ()
    write_inhibitors: tuple[IntegerField, ...] = ()

    def __post_init__(self) -> None:
        if not self.battle_scenes:
            raise ValueError("玩家布局至少需要一个已验证战斗场景")
        scene_keys = tuple(
            (scene.battle_state, scene.scene_guard_values) for scene in self.battle_scenes
        )
        if len(set(scene_keys)) != len(scene_keys):
            raise ValueError("已验证战斗场景签名不能重复")
        if any(
            not self.battle_state.minimum <= scene.battle_state <= self.battle_state.maximum
            for scene in self.battle_scenes
        ):
            raise ValueError("已验证战斗状态必须位于字段白名单内")
        if any(
            len(scene.scene_guard_values) != len(self.scene_guards) for scene in self.battle_scenes
        ):
            raise ValueError("战斗场景签名必须覆盖全部关联场景字段")
        if any(
            not field.minimum <= value <= field.maximum
            for scene in self.battle_scenes
            for field, value in zip(self.scene_guards, scene.scene_guard_values, strict=True)
        ):
            raise ValueError("战斗场景签名值必须位于字段白名单内")
        if len(self.guards) > 16:
            raise ValueError("玩家布局最多允许十六个状态门禁")


@dataclass(frozen=True)
class SegmentedHealthLayout:
    """只读分段生命模型；当前段与最大段计数沿用布局的 health/max_health 字段。"""

    remaining_segments: IntegerField
    segment_capacity: int

    def __post_init__(self) -> None:
        if type(self.segment_capacity) is not int or not 1 <= self.segment_capacity <= 1_000_000:
            raise ValueError("生命段容量必须为 1–1000000 的整数")
        if not self.remaining_segments.signed or self.remaining_segments.minimum != -1:
            raise ValueError("剩余生命段必须支持 -1 死亡标记")


@dataclass(frozen=True)
class SegmentedHealthWriteLayout:
    """把相邻的当前生命段和剩余段作为一次连续写入。"""


@dataclass(frozen=True)
class HeatWriteLayout:
    """授权热量写入；零目标可先停在正值，交由游戏自然跨零。"""

    natural_zero_transition: int | None = None

    def __post_init__(self) -> None:
        if self.natural_zero_transition is not None and (
            type(self.natural_zero_transition) is not int or self.natural_zero_transition <= 0
        ):
            raise ValueError("热量自然归零过渡值必须为正整数")


@dataclass(frozen=True)
class ForceCrashWriteLayout:
    """授权一次写入玩家属性组件中的 Force Crash 仪表。"""


@dataclass(frozen=True)
class WeaponRepeatWriteLayout:
    """授权清除普通槽门禁，并在写满前回退连段历史计数。"""

    safe_history_count: int

    def __post_init__(self) -> None:
        if type(self.safe_history_count) is not int or self.safe_history_count < 0:
            raise ValueError("武器重复的安全历史数量必须为非负整数")


@dataclass(frozen=True)
class WeaponStateLayout:
    """玩家属性组件内的槽门禁、连段历史和武器配置访问路径。"""

    combo_active: IntegerField
    slot_locks_relative_address: int
    recent_slot: IntegerField
    history_relative_address: int
    history_count: IntegerField
    configuration_pointer_relative_address: int
    weapon_ids_pointer_relative_address: int
    weapon_id_minimum: int
    weapon_id_maximum: int
    ordinary_slot_count: int = 12
    total_slot_count: int = 13
    history_capacity: int = 25
    pointer_size: int = 4

    def __post_init__(self) -> None:
        addresses = (
            self.slot_locks_relative_address,
            self.history_relative_address,
            self.configuration_pointer_relative_address,
            self.weapon_ids_pointer_relative_address,
        )
        if any(address < 0 for address in addresses):
            raise ValueError("武器状态布局的相对地址必须为非负数")
        if self.ordinary_slot_count != 12 or self.total_slot_count != 13:
            raise ValueError("武器状态布局必须描述 12 个普通槽和 1 个特殊槽")
        if self.history_capacity != 25:
            raise ValueError("武器连段历史容量必须为 25")
        if self.pointer_size not in (4, 8):
            raise ValueError("武器配置只支持 32 位或 64 位指针")
        if self.weapon_id_minimum > self.weapon_id_maximum:
            raise ValueError("实际武器 ID 值域无效")


@dataclass(frozen=True)
class PlayerState:
    health: int
    max_health: int
    heat: int
    max_heat: int
    player_identity: int
    object_identity: int
    battle_state: int
    force_crash: int | None = None
    max_force_crash: int | None = None
    scene_guard_values: tuple[int, ...] = ()
    write_inhibited: bool = False
    scene_name: str | None = None

    @property
    def is_player(self) -> bool:
        return self.player_identity == self.object_identity


@dataclass(frozen=True)
class WeaponSlotState:
    slot: int
    weapon_id: int
    used_in_combo: bool


@dataclass(frozen=True)
class RealtimeWeaponState:
    slots: tuple[WeaponSlotState, ...]
    recent_slot: int | None
    recent_weapon_id: int | None
    combo_active: bool
    history_count: int
    history: tuple[int, ...]


@dataclass(frozen=True)
class RealtimeProfile:
    executable_path: Path
    architecture: str
    fingerprints: tuple[InstructionFingerprint, ...]
    player_state: PlayerStateLayout | None = None
    permissions: EffectPermissions = EffectPermissions()
    segmented_health_write: SegmentedHealthWriteLayout | None = None
    heat_write: HeatWriteLayout | None = None
    force_crash_write: ForceCrashWriteLayout | None = None
    weapon_state: WeaponStateLayout | None = None
    weapon_repeat_write: WeaponRepeatWriteLayout | None = None
    enemy_clear: EnemyClearLayout | None = None
    wave_skip: WaveSkipLayout | None = None

    def __post_init__(self) -> None:
        if not self.fingerprints:
            raise ValueError("实时配置至少需要一个指令指纹")
        layout = self.player_state
        if layout is None and self.permissions != EffectPermissions():
            raise ValueError("写入权限必须具有玩家状态布局")
        if self.permissions.health_restore and layout is not None:
            if layout.segmented_health is not None and self.segmented_health_write is None:
                raise ValueError("分段生命必须具有专用分段回血写入布局")
        elif self.segmented_health_write is not None:
            raise ValueError("分段回血写入布局必须同时授权一次回血")
        if self.segmented_health_write is not None and (
            layout is None or layout.segmented_health is None
        ):
            raise ValueError("分段回血写入布局必须配合分段生命读取布局")
        if self.segmented_health_write is not None and layout is not None:
            segmented = layout.segmented_health
            assert segmented is not None
            if layout.health.path is not None or segmented.remaining_segments.path is not None:
                raise ValueError("分段生命字段必须位于玩家属性根组件")
            if (
                layout.health.size != segmented.remaining_segments.size
                or segmented.remaining_segments.relative_address
                != layout.health.relative_address + layout.health.size
            ):
                raise ValueError("分段生命写入字段必须等宽且连续")
        if self.permissions.heat_reduce and layout is not None:
            if any(field.path is not None for field in (layout.heat, layout.max_heat)):
                raise ValueError("热量字段必须位于玩家属性根组件")
            if self.heat_write is None:
                raise ValueError("热量写入必须具有专用热量写入布局")
            transition = self.heat_write.natural_zero_transition
            if transition is not None and not (
                layout.heat.minimum < transition <= layout.heat.maximum
            ):
                raise ValueError("热量自然归零过渡值必须位于热量字段的正数白名单内")
        elif self.heat_write is not None:
            raise ValueError("热量写入布局必须同时授权一次降热")
        if self.permissions.force_crash_refill and layout is not None:
            if layout.force_crash is None:
                raise ValueError("Force Crash 补充必须具有仪表读取布局")
            if layout.force_crash.path is not None:
                raise ValueError("Force Crash 仪表必须位于玩家属性根组件")
            if self.force_crash_write is None:
                raise ValueError("Force Crash 补充必须具有专用 Force Crash 写入布局")
        elif self.force_crash_write is not None:
            raise ValueError("Force Crash 写入布局必须同时授权一次补充")
        if self.permissions.health_maintenance and layout is not None:
            if layout.segmented_health is not None and self.segmented_health_write is None:
                raise ValueError("分段生命维持必须具有专用分段回血写入布局")
        if self.permissions.heat_maintenance and layout is not None:
            if self.heat_write is None:
                raise ValueError("热量维持必须具有专用热量写入布局")
        if self.permissions.weapon_repeat:
            if layout is None or self.weapon_state is None:
                raise ValueError("武器重复必须具有玩家状态与武器状态读取布局")
            if self.weapon_repeat_write is None:
                raise ValueError("武器重复必须具有专用写入布局")
            if (
                self.weapon_repeat_write.safe_history_count + 1
                >= self.weapon_state.history_capacity
            ):
                raise ValueError("武器重复的安全历史数量必须为尾部追加保留空间")
        elif self.weapon_repeat_write is not None:
            raise ValueError("武器重复写入布局必须同时授权武器重复")
        if self.permissions.enemy_clear != (self.enemy_clear is not None):
            raise ValueError("清敌授权与专用退场布局必须同时存在")
        if self.enemy_clear is not None and (layout is None or self.architecture != "x86"):
            raise ValueError("清敌必须具有已验证的 x86 玩家布局")
        if self.permissions.wave_skip != (self.wave_skip is not None):
            raise ValueError("增援快进授权与专用布局必须同时存在")


RealtimeState = Literal["disconnected", "connected_read_only"]


@dataclass(frozen=True)
class RealtimeSnapshot:
    state: RealtimeState
    instance: ProcessInstance | None
    fingerprints_valid: bool
    message: str
    active_effects: tuple[str, ...] = ()
    recoverable: bool = False


class RealtimeMonitor:
    """保持只读监控句柄，仅为已授权效果临时打开写句柄。"""

    def __init__(
        self,
        adapter: ProcessMemoryAdapter,
        profile: RealtimeProfile,
    ) -> None:
        self._adapter = adapter
        self._profile = profile
        self._connection: ProcessMemory | None = None
        self._health_target: int | None = None
        self._heat_target: int | None = None
        self._heat_zero_transition_pending = False
        self._health_object_token: tuple[int, ...] | None = None
        self._heat_object_token: tuple[int, ...] | None = None
        self._weapon_repeat_enabled = False
        self._weapon_object_token: tuple[int, ...] | None = None
        self._practice_effects_active = False
        self._force_crash_enabled = False
        self._force_crash_object_token: tuple[int, ...] | None = None
        self._enemy_clear_enabled = False
        self._enemy_clear_object_token: tuple[int, ...] | None = None
        self._snapshot = RealtimeSnapshot(
            "disconnected",
            None,
            False,
            "尚未连接游戏",
            (),
        )

    @property
    def snapshot(self) -> RealtimeSnapshot:
        return self._snapshot

    def connect(self) -> RealtimeSnapshot:
        self.disconnect("正在重新连接")
        connection: ProcessMemory | None = None
        try:
            candidates = self._adapter.list_processes(self._profile.executable_path.name)
            expected_path = normalized_executable_path(self._profile.executable_path)
            matching_paths = tuple(
                candidate
                for candidate in candidates
                if normalized_executable_path(candidate.executable_path) == expected_path
            )
            if not candidates:
                raise RealtimeConnectionError("未找到游戏进程")
            if not matching_paths:
                raise RealtimeConnectionError("进程名称匹配，但可执行文件路径不匹配")
            if len(matching_paths) != 1:
                raise RealtimeConnectionError("检测到多个匹配的游戏进程实例，拒绝猜测目标")
            instance = matching_paths[0]
            if instance.architecture != self._profile.architecture:
                raise RealtimeConnectionError(
                    f"目标进程架构为 {instance.architecture}，期望 {self._profile.architecture}"
                )

            connection = self._adapter.open_process(instance, writable=False)
            self._verify_fingerprints(connection)
        except (ProcessMemoryError, RealtimeConnectionError) as error:
            if connection is not None:
                connection.close()
            if isinstance(error, RealtimeConnectionError):
                raise
            raise RealtimeConnectionError(f"无法建立只读连接：{error}") from error
        except Exception:
            if connection is not None:
                connection.close()
            raise
        assert connection is not None
        self._connection = connection
        self._snapshot = RealtimeSnapshot(
            "connected_read_only",
            instance,
            True,
            "已通过路径、进程实例、架构和指令指纹门禁，只读连接成功",
            (),
        )
        return self._snapshot

    def poll(self) -> RealtimeSnapshot:
        connection = self._connection
        if connection is None:
            return self._snapshot
        if not connection.is_alive():
            return self.disconnect("目标进程实例已退出，已停用实时会话", recoverable=True)
        try:
            self._verify_fingerprints(connection)
        except RealtimeConnectionError as error:
            return self.disconnect(str(error))
        except ProcessMemoryError as error:
            return self.disconnect(f"只读监控失败：{error}")
        self._snapshot = RealtimeSnapshot(
            "connected_read_only",
            connection.instance,
            True,
            "只读连接正常",
            self._active_effects(),
        )
        return self._snapshot

    def disconnect(
        self, message: str = "已断开实时会话", *, recoverable: bool = False
    ) -> RealtimeSnapshot:
        if self._connection is not None:
            recoverable = recoverable or not self._connection.is_alive()
            self._connection.close()
            self._connection = None
        self._clear_maintenance_targets()
        self._snapshot = RealtimeSnapshot("disconnected", None, False, message, (), recoverable)
        return self._snapshot

    def read_player_state(self) -> PlayerState:
        _, _, _, state, _ = self._validated_player_snapshot()
        return state

    def enable_practice_effects(self) -> PlayerState:
        """一次性门禁练习组合；失败时撤销全部目标，不接受失效中途留下半套效果。"""

        _, _, _, player, token = self._validated_player_snapshot()
        if player.scene_name != "练习战斗":
            raise RealtimeEffectError("练习组合仅限已验证的练习战斗")
        try:
            self.enable_health_maintenance(player.max_health)
            self.enable_heat_maintenance(0)
            self.enable_weapon_repeat()
            _, _, _, confirmed, confirmed_token = self._validated_player_snapshot()
            if confirmed.scene_name != "练习战斗" or confirmed_token != token:
                raise RealtimeEffectError("练习组合启用期间玩家对象或练习场景发生变化")
            self._practice_effects_active = True
        except RealtimeEffectError:
            self._clear_maintenance_targets()
            if self._connection is not None:
                self._refresh_snapshot("练习组合启用失败，已撤销全部持续效果")
            raise
        return player

    def read_weapon_state(self) -> RealtimeWeaponState:
        connection, player_layout, base, _, token = self._validated_player_snapshot()
        layout = self._profile.weapon_state
        if layout is None:
            raise RealtimeEffectError("当前版本没有经过验证的武器状态布局")
        try:
            self._verify_fingerprints(connection)
            before = self._read_weapon_state_at(connection, base, layout)
            after = self._read_weapon_state_at(connection, base, layout)
            current_base, current_player, current_token = self._read_player_state(
                connection, player_layout
            )
            self._validate_player_state(current_player, player_layout)
            if current_base != base or current_token != token:
                raise RealtimePlayerUnavailableError("玩家对象在武器状态采样期间发生变化")
            self._verify_fingerprints(connection)
            if before != after:
                raise RealtimeFrameUnstableError("武器状态在采样期间变化，已丢弃整帧")
            return before
        except RealtimeFrameUnstableError:
            self._refresh_snapshot("战斗数据正在更新，本帧已跳过")
            raise
        except RealtimePlayerUnavailableError:
            self._clear_maintenance_targets()
            self._refresh_snapshot("已离开战斗，武器状态和持续效果已失效")
            raise
        except (ProcessMemoryError, RealtimeConnectionError, RealtimeEffectError) as error:
            self.disconnect(f"武器状态失效，已停写：{error}")
            raise RealtimeEffectError(str(error)) from error

    @staticmethod
    def _read_weapon_state_at(
        connection: ProcessMemory,
        base: int,
        layout: WeaponStateLayout,
    ) -> RealtimeWeaponState:
        locks = connection.read(
            base + layout.slot_locks_relative_address,
            layout.total_slot_count,
        )
        if len(locks) != layout.total_slot_count or any(lock not in (0, 1) for lock in locks):
            raise RealtimeEffectError("武器槽门禁读取不完整或超出已验证范围")
        combo_active = bool(_read_integer(connection, base, layout.combo_active))
        count = _read_integer(connection, base, layout.history_count)
        if count > layout.history_capacity:
            raise RealtimeEffectError("武器连段历史数量超出容量")
        if count:
            history_raw = connection.read(base + layout.history_relative_address, count * 2)
            if len(history_raw) != count * 2:
                raise RealtimeEffectError("武器连段历史读取不完整")
            history = tuple(unpack(f"<{count}h", history_raw))
        else:
            history = ()
        if any(
            not layout.weapon_id_minimum <= weapon_id <= layout.weapon_id_maximum
            for weapon_id in history
        ):
            raise RealtimeEffectError("武器连段历史包含未验证的实际武器 ID")

        configuration = _read_pointer(
            connection,
            base + layout.configuration_pointer_relative_address,
            layout.pointer_size,
            "武器配置",
        )
        weapon_ids = _read_pointer(
            connection,
            configuration + layout.weapon_ids_pointer_relative_address,
            layout.pointer_size,
            "实际武器 ID 表",
        )
        ids_raw = connection.read(weapon_ids, layout.total_slot_count * 2)
        if len(ids_raw) != layout.total_slot_count * 2:
            raise RealtimeEffectError("实际武器 ID 表读取不完整")
        ids = tuple(unpack(f"<{layout.total_slot_count}h", ids_raw))
        if any(
            not layout.weapon_id_minimum <= weapon_id <= layout.weapon_id_maximum
            for weapon_id in ids
        ):
            raise RealtimeEffectError("实际武器 ID 超出已验证范围")

        recent_raw = _read_integer(connection, base, layout.recent_slot)
        recent_slot = recent_raw if 0 <= recent_raw < layout.total_slot_count else None
        return RealtimeWeaponState(
            slots=tuple(
                WeaponSlotState(slot, weapon_id, bool(locks[slot]))
                for slot, weapon_id in enumerate(ids)
            ),
            recent_slot=recent_slot,
            recent_weapon_id=ids[recent_slot] if recent_slot is not None else None,
            combo_active=combo_active,
            history_count=count,
            history=history,
        )

    def _validated_player_snapshot(
        self,
    ) -> tuple[ProcessMemory, PlayerStateLayout, int, PlayerState, tuple[int, ...]]:
        connection, layout = self._require_player_connection()
        try:
            self._verify_fingerprints(connection)
            base, state, token = self._read_player_state(connection, layout)
            self._validate_player_state(state, layout)
            self._verify_fingerprints(connection)
        except RealtimeFrameUnstableError:
            self._refresh_snapshot("战斗数据正在更新，本帧已跳过")
            raise
        except RealtimePlayerUnavailableError:
            self._clear_maintenance_targets()
            raise
        except (ProcessMemoryError, RealtimeConnectionError, RealtimeEffectError) as error:
            self.disconnect(f"玩家状态失效，已停写：{error}")
            raise RealtimeEffectError(str(error)) from error
        return connection, layout, base, state, token

    def restore_health_once(self) -> PlayerState:
        self._require_permission("health_restore", "一次回血")
        _, layout, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            self.disconnect("玩家已死亡，已停用实时会话")
            raise RealtimeEffectError("玩家已死亡，默认不执行复活写入")
        self._require_write_safe(state)
        if self._profile.segmented_health_write is not None:
            return self._write_segmented_health(token=token)
        return self._write_player_field(
            "health",
            state.max_health,
            layout=layout,
            token=token,
        )

    def reduce_heat_once(self, target: int = 0) -> PlayerState:
        self._require_permission("heat_reduce", "一次降热")
        _, layout, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            self.disconnect("玩家已死亡，已停用实时会话")
            raise RealtimeEffectError("玩家已死亡，不执行降热写入")
        self._require_write_safe(state)
        if not 0 <= target <= state.max_heat:
            raise RealtimeEffectError("目标热量超出当前已验证范围")
        return self._write_heat_target(target, layout=layout, token=token)

    def refill_force_crash_once(self) -> PlayerState:
        self._require_permission("force_crash_refill", "一次 Force Crash 补充")
        _, layout, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            self.disconnect("玩家已死亡，已停用实时会话")
            raise RealtimeEffectError("玩家已死亡，不执行 Force Crash 补充")
        self._require_write_safe(state)
        field = layout.force_crash
        if field is None or self._profile.force_crash_write is None:
            raise RealtimeEffectError("当前 Profile 没有 Force Crash 写入布局")
        return self._write_player_field(
            "force_crash",
            field.maximum,
            layout=layout,
            token=token,
        )

    def clear_enemies_once(self) -> EnemyClearResult:
        """仅启动敌方原生退场计时，游戏自行处理计数、奖励和结算。"""
        self._require_permission("enemy_clear", "一次清敌")
        connection, layout, base, player, token = self._validated_player_snapshot()
        self._require_write_safe(player)
        if player.health <= 0 or player.scene_name != "普通战斗":
            raise RealtimePlayerUnavailableError("清敌仅适用于存活玩家的普通战斗")
        clear_layout = self._profile.enemy_clear
        assert clear_layout is not None
        requested = 0
        try:
            frame = read_combat_frame(connection, clear_layout)
            if frame.actors[0].attributes != base:
                raise CombatValidationError("战斗表玩家与已验证玩家组件不一致")
            targets = tuple(actor for actor in frame.actors if actor.clearable)
            protected = sum(actor.slot in frame.protected_slots for actor in targets)
            for actor in targets:
                if actor.slot in frame.protected_slots:
                    continue
                writer = self._adapter.open_process(connection.instance, writable=True)
                try:
                    self._verify_fingerprints(writer)
                    current_base, current, current_token = self._read_player_state(writer, layout)
                    self._validate_player_state(current, layout)
                    self._require_write_safe(current)
                    if (
                        current_token != token
                        or current.health <= 0
                        or current.scene_name != "普通战斗"
                    ):
                        raise RealtimePlayerUnavailableError("战斗在清敌前切换")
                    latest = read_combat_frame(writer, clear_layout)
                    if latest.actors[0].attributes != current_base:
                        raise CombatValidationError("清敌前玩家组件不一致")
                    same = next((item for item in latest.actors if item.slot == actor.slot), None)
                    if (
                        same is None
                        or same.token != actor.token
                        or not same.clearable
                        or same.slot in latest.protected_slots
                    ):
                        continue
                    # 再核对待写入对象，避免其他敌人死亡、刷新增援时复用旧槽位。
                    table_slot = (
                        writer.instance.image_base
                        + clear_layout.table_relative_address
                        + 4 * same.slot
                    )
                    if int.from_bytes(writer.read(table_slot, 4), "little") != same.root:
                        continue
                    final = read_actor(writer, same.slot, same.root, clear_layout)
                    if final.token != same.token or not final.clearable:
                        continue
                    # 对象采样之后再次检查场景与事件表；切场时不把上一帧意图写入新战斗。
                    _, current, final_token = self._read_player_state(writer, layout)
                    self._validate_player_state(current, layout)
                    self._require_write_safe(current)
                    if (
                        final_token != token
                        or current.health <= 0
                        or current.scene_name != "普通战斗"
                    ):
                        raise RealtimePlayerUnavailableError("清敌最后复核时场景已变化")
                    event_pointer, event_bytes = latest.events_token
                    if (
                        int.from_bytes(
                            writer.read(
                                writer.instance.image_base
                                + clear_layout.events_pointer_relative_address,
                                4,
                            ),
                            "little",
                        )
                        != event_pointer
                        or writer.read(event_pointer, len(event_bytes)) != event_bytes
                        or int.from_bytes(writer.read(table_slot, 4), "little") != final.root
                    ):
                        continue
                    writer.write(
                        final.attributes + clear_layout.removal_timer_offset, pack("<h", 1)
                    )
                    requested += 1
                    # 正常死亡可以立刻移除或替换槽位，不能再读已释放的组件。
                    if int.from_bytes(writer.read(table_slot, 4), "little") == final.root:
                        after = read_actor(writer, final.slot, final.root, clear_layout)
                        if after.token == final.token and after.removal_timer not in (0, 1):
                            raise CombatValidationError("敌方退场计时写后复核失败")
                    self._verify_fingerprints(writer)
                finally:
                    writer.close()
        except CombatFrameChangedError as error:
            if not requested:
                raise RealtimeFrameUnstableError(str(error)) from error
        except (RealtimePlayerUnavailableError, RealtimeWritePausedError):
            if not requested:
                raise
        except (
            ProcessMemoryError,
            RealtimeConnectionError,
            CombatValidationError,
            RealtimeEffectError,
        ) as error:
            self.disconnect(f"清敌失败，已全部停写：{error}")
            raise RealtimeEffectError(str(error)) from error
        return EnemyClearResult(requested, protected)

    def advance_enemy_wave_once(self) -> int:
        """复现原生全灭后的增援快进；绝不写真实战斗用时、结果或结束倒计时。"""
        self._require_permission("wave_skip", "跳过刷波等待")
        connection, player_layout, player_base, player, token = self._validated_player_snapshot()
        self._require_write_safe(player)
        if player.health <= 0 or player.scene_name != "普通战斗":
            raise RealtimePlayerUnavailableError("增援快进等待普通战斗")
        clear_layout = self._profile.enemy_clear
        wave_layout = self._profile.wave_skip
        assert clear_layout is not None and wave_layout is not None
        write_started = False
        try:
            frame = read_combat_frame(connection, clear_layout)
            if frame.actors[0].attributes != player_base:
                raise CombatValidationError("增援快进前玩家组件不一致")
            schedule = read_wave_schedule(connection, wave_layout, frame)
            if (
                schedule is None
                or schedule.next_time is None
                or schedule.next_time <= schedule.clock
            ):
                return 0
            writer = self._adapter.open_process(connection.instance, writable=True)
            try:
                self._verify_fingerprints(writer)
                current_base, current, current_token = self._read_player_state(
                    writer, player_layout
                )
                self._validate_player_state(current, player_layout)
                self._require_write_safe(current)
                if (
                    current_token != token
                    or current.health <= 0
                    or current.scene_name != "普通战斗"
                ):
                    raise RealtimePlayerUnavailableError("场景在增援快进前变化")
                current_frame = read_combat_frame(writer, clear_layout)
                if current_frame.actors[0].attributes != current_base:
                    raise CombatValidationError("增援快进写前玩家组件不一致")
                current_schedule = read_wave_schedule(writer, wave_layout, current_frame)
                if (
                    current_schedule is None
                    or current_schedule.token != schedule.token
                    or current_schedule.next_time is None
                    or current_schedule.next_time <= current_schedule.clock
                ):
                    return 0
                # 队列采样之后重新确认玩家、场景与对象表，避免下一波恰在此时入场。
                _, final_player, final_token = self._read_player_state(writer, player_layout)
                self._validate_player_state(final_player, player_layout)
                self._require_write_safe(final_player)
                if (
                    final_token != token
                    or final_player.health <= 0
                    or final_player.scene_name != "普通战斗"
                ):
                    raise RealtimePlayerUnavailableError("增援快进最后复核时场景已变化")
                final_frame = read_combat_frame(writer, clear_layout)
                if final_frame.actors[0].attributes != current_base:
                    raise RealtimePlayerUnavailableError("增援快进最后复核时玩家已替换")
                final_schedule = read_wave_schedule(writer, wave_layout, final_frame)
                if (
                    final_schedule is None
                    or final_schedule.token != schedule.token
                    or final_schedule.next_time is None
                    or final_schedule.next_time <= final_schedule.clock
                ):
                    return 0
                skipped = final_schedule.next_time - final_schedule.clock
                write_started = True
                writer.write(final_schedule.attributes + 4, pack("<i", final_schedule.next_time))
                # 正常调度可立刻消费记录；只核对同一控制器的时钟，不要求记录保持不变。
                root = int.from_bytes(
                    writer.read(writer.instance.image_base + wave_layout.controller_pointer_rva, 4),
                    "little",
                )
                if root == final_schedule.controller:
                    attributes = int.from_bytes(writer.read(root + 0x38, 4), "little")
                    if attributes == final_schedule.attributes:
                        after_clock = int.from_bytes(
                            writer.read(attributes + 4, 4), "little", signed=True
                        )
                        if not final_schedule.next_time <= after_clock <= 999999:
                            raise CombatValidationError("增援时钟写后复核失败")
                self._verify_fingerprints(writer)
                return skipped
            finally:
                writer.close()
        except CombatFrameChangedError as error:
            if write_started:
                self.disconnect("增援快进写后状态变化，已全部停写")
            raise RealtimeFrameUnstableError(str(error)) from error
        except (RealtimePlayerUnavailableError, RealtimeWritePausedError):
            raise
        except (
            ProcessMemoryError,
            RealtimeConnectionError,
            CombatValidationError,
            RealtimeEffectError,
        ) as error:
            self.disconnect(f"增援快进失败，已全部停写：{error}")
            raise RealtimeEffectError(str(error)) from error

    def enable_enemy_clear_maintenance(self) -> None:
        self._require_permission("enemy_clear_maintenance", "自动清敌")
        _, _, _, player, token = self._validated_player_snapshot()
        self._require_write_safe(player)
        if player.health <= 0 or player.scene_name != "普通战斗":
            raise RealtimePlayerUnavailableError("自动清敌等待普通战斗")
        self._enemy_clear_enabled = True
        self._enemy_clear_object_token = token
        self._refresh_snapshot("自动清敌已启用")

    def enable_health_maintenance(self, target: int) -> None:
        self._require_permission("health_maintenance", "持续血量")
        _, _, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            self.disconnect("玩家已死亡，已停用实时会话")
            raise RealtimeEffectError("玩家已死亡，不启用持续血量")
        self._require_write_safe(state)
        if not 1 <= target <= state.max_health:
            raise RealtimeEffectError("持续血量目标超出当前已验证范围")
        self._health_target = target
        self._health_object_token = token
        self._refresh_snapshot("血量维持已启用")

    def enable_heat_maintenance(self, target: int) -> None:
        self._require_permission("heat_maintenance", "持续热量")
        _, _, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            self.disconnect("玩家已死亡，已停用实时会话")
            raise RealtimeEffectError("玩家已死亡，不启用持续热量")
        self._require_write_safe(state)
        if not 0 <= target <= state.max_heat:
            raise RealtimeEffectError("持续热量目标超出当前已验证范围")
        self._heat_target = target
        write_layout = self._profile.heat_write
        self._heat_zero_transition_pending = (
            target == 0
            and write_layout is not None
            and write_layout.natural_zero_transition is not None
        )
        self._heat_object_token = token
        self._refresh_snapshot("热量阈值已启用")

    def enable_weapon_repeat(self) -> None:
        self._require_permission("weapon_repeat", "同连段武器重复")
        _, _, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            self.disconnect("玩家已死亡，已停用实时会话")
            raise RealtimeEffectError("玩家已死亡，不启用同连段武器重复")
        self._require_write_safe(state)
        if self._profile.weapon_state is None or self._profile.weapon_repeat_write is None:
            raise RealtimeEffectError("当前 Profile 没有武器重复写入布局")
        self._weapon_repeat_enabled = True
        self._weapon_object_token = token
        self._refresh_snapshot("同连段武器重复已启用")

    def enable_force_crash_maintenance(self) -> None:
        self._require_permission("force_crash_maintenance", "FC 满槽维持")
        _, _, _, state, token = self._validated_player_snapshot()
        if state.health <= 0:
            raise RealtimePlayerUnavailableError("玩家已死亡，不启用 FC 满槽维持")
        self._require_write_safe(state)
        self._force_crash_enabled = True
        self._force_crash_object_token = token
        self._refresh_snapshot("FC 满槽维持已启用")

    def stop_effects(self) -> RealtimeSnapshot:
        self._clear_maintenance_targets()
        return self.poll()

    def stop_health_maintenance(self) -> RealtimeSnapshot:
        self._health_target = None
        self._health_object_token = None
        return self._refresh_snapshot("血量维持已停用")

    def stop_heat_maintenance(self) -> RealtimeSnapshot:
        self._heat_target = None
        self._heat_zero_transition_pending = False
        self._heat_object_token = None
        return self._refresh_snapshot("热量阈值已停用")

    def stop_weapon_repeat(self) -> RealtimeSnapshot:
        self._weapon_repeat_enabled = False
        self._weapon_object_token = None
        return self._refresh_snapshot("同连段武器重复已停用")

    def tick_effects(self) -> RealtimeSnapshot:
        state = self.poll()
        effect_message = "实时效果已安全执行"
        if state.state != "connected_read_only":
            return state
        if (
            self._health_target is None
            and self._heat_target is None
            and not self._weapon_repeat_enabled
            and not self._force_crash_enabled
            and not self._enemy_clear_enabled
        ):
            return state
        try:
            _, layout, _, player, current_token = self._validated_player_snapshot()
            if self._practice_effects_active and player.scene_name != "练习战斗":
                self._clear_maintenance_targets()
                return self._refresh_snapshot("已离开练习战斗，练习组合已停用")
            expected_tokens = tuple(
                token
                for token in (
                    self._health_object_token,
                    self._heat_object_token,
                    self._weapon_object_token,
                    self._force_crash_object_token,
                    self._enemy_clear_object_token,
                )
                if token is not None
            )
            if any(token != current_token for token in expected_tokens):
                raise RealtimePlayerUnavailableError("玩家对象已重建，等待重新验证")
            if player.health <= 0:
                raise RealtimePlayerUnavailableError("玩家已死亡，等待下一场战斗")
            if player.write_inhibited:
                return self._refresh_snapshot("Force Crash 发动中，持续写入已暂停")
            if self._health_target is not None:
                if player.health <= 0:
                    raise RealtimeEffectError("玩家已死亡，持续血量已停用")
                if self._health_target > player.max_health:
                    raise RealtimeEffectError("持续血量目标超过当前最大血量")
                if player.health < self._health_target:
                    if self._profile.segmented_health_write is not None:
                        player = self._write_segmented_health(
                            self._health_target,
                            token=current_token,
                        )
                    else:
                        player = self._write_player_field(
                            "health",
                            self._health_target,
                            layout=layout,
                            token=current_token,
                        )
            if self._heat_target is not None:
                if self._heat_target > player.max_heat:
                    raise RealtimeEffectError("持续热量目标超过当前热量上限")
                if player.heat > self._heat_target or self._heat_zero_transition_pending:
                    player = self._write_heat_target(
                        self._heat_target,
                        layout=layout,
                        token=current_token,
                    )
                    self._heat_zero_transition_pending = False
            if self._weapon_repeat_enabled:
                self._write_weapon_repeat(layout=layout, token=current_token)
            if self._force_crash_enabled and player.force_crash != player.max_force_crash:
                if layout.force_crash is None:
                    raise RealtimeEffectError("当前 Profile 没有 FC 仪表布局")
                self._write_player_field(
                    "force_crash",
                    layout.force_crash.maximum,
                    layout=layout,
                    token=current_token,
                )
            if self._enemy_clear_enabled and player.scene_name == "普通战斗":
                result = self.clear_enemies_once()
                skipped = (
                    self.advance_enemy_wave_once() if self._profile.permissions.wave_skip else 0
                )
                effect_message = (
                    f"自动清敌：已请求 {result.requested} 名敌人退场；"
                    f"跳过 {result.protected} 名剧情保护对象"
                    if result.requested or result.protected
                    else "自动清敌：等待敌人或后续增援"
                )
                if skipped:
                    effect_message += f"；已跳过 {skipped} 帧刷波等待"
        except RealtimePlayerUnavailableError as error:
            self._clear_maintenance_targets()
            return self._refresh_snapshot(f"等待进入战斗：{error}")
        except RealtimeFrameUnstableError:
            if self._connection is None:
                return self._snapshot  # 写入后复核失败已停写，不能再刷新断开的句柄
            return self._refresh_snapshot("战斗数据正在更新，本帧已跳过")
        except RealtimeWritePausedError:
            return self._refresh_snapshot("Force Crash 发动中，持续写入已暂停")
        except RealtimeEffectError as error:
            if self._connection is None:
                return self._snapshot  # 保留采样层记录的进程退出原因和可重连标记。
            return self.disconnect(f"实时效果失败，已全部停写：{error}")
        self._snapshot = RealtimeSnapshot(
            "connected_read_only",
            self._require_connection().instance,
            True,
            effect_message,
            self._active_effects(),
        )
        return self._snapshot

    def _write_weapon_repeat(
        self,
        *,
        layout: PlayerStateLayout,
        token: tuple[int, ...],
    ) -> None:
        connection, current_layout = self._require_player_connection()
        weapon_layout = self._profile.weapon_state
        write_layout = self._profile.weapon_repeat_write
        if layout != current_layout or weapon_layout is None or write_layout is None:
            raise RealtimeEffectError("武器重复布局在写入前发生变化或不完整")
        write_started = False
        try:
            writer = self._adapter.open_process(connection.instance, writable=True)
            try:
                self._verify_fingerprints(writer)
                base, player, current_token = self._read_player_state(writer, layout)
                self._validate_player_state(player, layout)
                if current_token != token:
                    raise RealtimePlayerUnavailableError("玩家对象在武器重复写入前发生变化")
                if player.health <= 0:
                    raise RealtimeEffectError("玩家已死亡，不执行武器重复写入")
                self._require_write_safe(player)
                if self._practice_effects_active and player.scene_name != "练习战斗":
                    raise RealtimePlayerUnavailableError("练习场景在武器写入前变化")
                before = self._read_weapon_state_at(writer, base, weapon_layout)
                if not before.combo_active:
                    return
                rollback_count = write_layout.safe_history_count + 1
                if before.history_count > rollback_count:
                    # 25 项是游戏合法的满历史，但此时不能安全回退；等待原生连段结束。
                    raise RealtimeFrameUnstableError("连段历史已满，本帧跳过武器恢复")
                # 武器发动可能正夹在读取与清门禁之间；跳过该帧而非覆盖新动作。
                confirmed = self._read_weapon_state_at(writer, base, weapon_layout)
                if confirmed != before:
                    raise RealtimeFrameUnstableError("武器状态正在更新，本帧跳过恢复")
                ordinary_slots = before.slots[: weapon_layout.ordinary_slot_count]
                if any(slot.used_in_combo for slot in ordinary_slots):
                    write_started = True
                    writer.write(
                        base + weapon_layout.slot_locks_relative_address,
                        bytes(weapon_layout.ordinary_slot_count),
                    )
                if before.history_count == rollback_count:
                    write_started = True
                    writer.write(
                        _field_address(writer, base, weapon_layout.history_count),
                        _encode_integer(
                            write_layout.safe_history_count,
                            weapon_layout.history_count,
                        ),
                    )
                after_base, after_player, after_token = self._read_player_state(writer, layout)
                self._validate_player_state(after_player, layout)
                if after_base != base or after_token != token:
                    raise RealtimeEffectError("玩家对象在武器重复写入后发生变化")
                after = self._read_weapon_state_at(writer, base, weapon_layout)
                if after.slots[-1].used_in_combo != before.slots[-1].used_in_combo:
                    raise RealtimeEffectError("特殊武器槽在写入期间发生变化")
                expected_count = (
                    write_layout.safe_history_count
                    if before.history_count == rollback_count
                    else before.history_count
                )
                if any(
                    slot.used_in_combo for slot in after.slots[: weapon_layout.ordinary_slot_count]
                ):
                    # 若游戏已追加一招，必须留下原生门禁；下轮再恢复，不覆盖新动作。
                    if after.history_count <= expected_count:
                        raise RealtimeEffectError("普通武器槽门禁写入后复核失败")
                    if after.history_count > expected_count + 1:
                        raise RealtimeEffectError("连段历史超过已验证容量")
                if after.history_count != expected_count and not (
                    after.combo_active and after.history_count == expected_count + 1
                ):
                    raise RealtimeEffectError("连段历史数量写入后复核失败")
                self._verify_fingerprints(writer)
            finally:
                writer.close()
        except RealtimeWritePausedError:
            raise
        except RealtimePlayerUnavailableError as error:
            self._handle_write_scene_change(error, write_started)
            raise
        except RealtimeFrameUnstableError:
            if write_started:
                self.disconnect("武器重复写入后状态变化，已全部停写")
            raise
        except (ProcessMemoryError, RealtimeConnectionError, RealtimeEffectError) as error:
            self.disconnect(f"武器重复写入失败，已全部停写：{error}")
            raise RealtimeEffectError(str(error)) from error

    def _verify_fingerprints(self, connection: ProcessMemory) -> None:
        for fingerprint in self._profile.fingerprints:
            address = connection.instance.image_base + fingerprint.relative_address
            try:
                actual = connection.read(address, len(fingerprint.expected))
            except ProcessMemoryError as error:
                raise RealtimeConnectionError(
                    f"无法验证指令指纹 {fingerprint.name}：{error}"
                ) from error
            if actual != fingerprint.expected:
                raise RealtimeConnectionError(
                    f"指令指纹 {fingerprint.name} 不匹配，游戏可能尚未展开或版本不受支持"
                )

    def _write_player_field(
        self,
        field_name: Literal["health", "heat", "force_crash"],
        value: int,
        *,
        layout: PlayerStateLayout | None = None,
        token: tuple[int, ...] | None = None,
        accepted_after_values: tuple[int, ...] | None = None,
    ) -> PlayerState:
        connection, current_layout = self._require_player_connection()
        if layout is None or token is None:
            connection, layout, _, _, token = self._validated_player_snapshot()
        elif layout != current_layout:
            raise RealtimeEffectError("玩家布局在写入前发生变化")
        field = getattr(layout, field_name)
        if not field.minimum <= value <= field.maximum:
            raise RealtimeEffectError(f"{field_name} 目标值超出字段白名单")
        write_started = False
        try:
            writer = self._adapter.open_process(connection.instance, writable=True)
            try:
                self._verify_fingerprints(writer)
                current_base, current, current_token = self._read_player_state(writer, layout)
                self._validate_player_state(current, layout)
                if current_token != token:
                    raise RealtimePlayerUnavailableError("玩家对象在读写之间发生变化")
                if current.health <= 0:
                    raise RealtimeEffectError("玩家已死亡，不执行实时写入")
                self._require_write_safe(current)
                if self._practice_effects_active and current.scene_name != "练习战斗":
                    raise RealtimePlayerUnavailableError("练习场景在数值写入前变化")
                write_started = True
                writer.write(
                    _field_address(writer, current_base, field),
                    _encode_integer(value, field),
                )
                # 同一写句柄立刻核对实际写入，随后游戏可在整帧复核期间自然改变热量。
                written = _read_integer(writer, current_base, field)
                accepted = (value,) if accepted_after_values is None else accepted_after_values
                if written not in accepted:
                    if field_name != "heat" or not field.minimum <= written <= field.maximum:
                        raise RealtimeEffectError(f"{field_name} 写入未生效")
                if field_name == "heat":
                    # 写入后游戏可能恰在跨生命段、自然冷却或发动武器。
                    # 在同一实例和句柄上重采样，不重写，也不接受无法稳定验证的对象。
                    for attempt in range(3):
                        try:
                            _, after, after_token = self._read_player_state(writer, layout)
                            break
                        except RealtimeFrameUnstableError:
                            if attempt == 2:
                                raise
                else:
                    _, after, after_token = self._read_player_state(writer, layout)
                if after_token != token:
                    raise RealtimeEffectError("玩家对象在写入后发生变化")
                self._validate_player_state(after, layout)
                if after.health <= 0:
                    raise RealtimeEffectError("玩家在写入复核前死亡")
                if field_name != "heat" and getattr(after, field_name) not in accepted:
                    raise RealtimeEffectError(f"{field_name} 写入后复核失败")
                self._verify_fingerprints(writer)
                return after
            finally:
                writer.close()
        except RealtimeWritePausedError:
            raise
        except RealtimePlayerUnavailableError as error:
            self._handle_write_scene_change(error, write_started)
            raise
        except RealtimeFrameUnstableError:
            if write_started:
                self.disconnect(f"{field_name} 写入后状态变化，已全部停写")
            raise
        except (ProcessMemoryError, RealtimeConnectionError, RealtimeEffectError) as error:
            self.disconnect(f"{field_name} 写入失败，已全部停写：{error}")
            raise RealtimeEffectError(str(error)) from error

    def _write_heat_target(
        self,
        target: int,
        *,
        layout: PlayerStateLayout,
        token: tuple[int, ...],
    ) -> PlayerState:
        """写入热量阈值；零目标保留游戏处理自然跨零的机会。"""

        write_layout = self._profile.heat_write
        if write_layout is None:
            raise RealtimeEffectError("当前 Profile 没有热量写入布局")
        value = target
        if target == 0 and write_layout.natural_zero_transition is not None:
            value = write_layout.natural_zero_transition
            return self._write_player_field(
                "heat",
                value,
                layout=layout,
                token=token,
                accepted_after_values=(value, 0),
            )
        return self._write_player_field("heat", value, layout=layout, token=token)

    def _write_segmented_health(
        self,
        target_health: int | None = None,
        *,
        token: tuple[int, ...] | None = None,
    ) -> PlayerState:
        connection, layout = self._require_player_connection()
        if token is None:
            connection, layout, _, _, token = self._validated_player_snapshot()
        segmented = layout.segmented_health
        if segmented is None or self._profile.segmented_health_write is None:
            raise RealtimeEffectError("当前 Profile 没有分段回血事务布局")
        current_field = layout.health
        remaining_field = segmented.remaining_segments
        maximum_field = layout.max_health
        write_started = False
        try:
            writer = self._adapter.open_process(connection.instance, writable=True)
            try:
                self._verify_fingerprints(writer)
                current_base, current, current_token = self._read_player_state(writer, layout)
                self._validate_player_state(current, layout)
                if current_token != token:
                    raise RealtimePlayerUnavailableError("玩家对象在分段回血前发生变化")
                if current.health <= 0:
                    raise RealtimeEffectError("玩家已死亡，默认不执行复活写入")
                self._require_write_safe(current)
                if self._practice_effects_active and current.scene_name != "练习战斗":
                    raise RealtimePlayerUnavailableError("练习场景在分段回血前变化")
                maximum_segments = _read_integer(writer, current_base, maximum_field)
                maximum_health = (maximum_segments + 1) * segmented.segment_capacity
                requested_health = maximum_health if target_health is None else target_health
                if not 1 <= requested_health <= maximum_health:
                    raise RealtimeEffectError("分段生命目标超出当前最大血量")
                target = _encode_segmented_health(
                    requested_health,
                    segmented.segment_capacity,
                    maximum_segments,
                    current_field,
                    remaining_field,
                )
                write_started = True
                writer.write(_field_address(writer, current_base, current_field), target)
                _, after, after_token = self._read_player_state(writer, layout)
                if after_token != token:
                    raise RealtimeEffectError("玩家对象在分段回血复核前发生变化")
                self._validate_player_state(after, layout)
                if after.health != requested_health:
                    raise RealtimeEffectError("分段回血写入后复核失败")
                self._verify_fingerprints(writer)
                return after
            finally:
                writer.close()
        except RealtimeWritePausedError:
            raise
        except RealtimePlayerUnavailableError as error:
            self._handle_write_scene_change(error, write_started)
            raise
        except RealtimeFrameUnstableError:
            if write_started:
                self.disconnect("分段回血写入后状态变化，已全部停写")
            raise
        except (ProcessMemoryError, RealtimeConnectionError, RealtimeEffectError) as error:
            self.disconnect(f"health 写入失败，已全部停写：{error}")
            raise RealtimeEffectError(str(error)) from error

    def _handle_write_scene_change(
        self, error: RealtimePlayerUnavailableError, write_started: bool
    ) -> None:
        if write_started:
            self.disconnect(f"写入后场景变化，已全部停写：{error}")
            raise RealtimeEffectError(str(error)) from error
        self._clear_maintenance_targets()
        self._refresh_snapshot(f"写入前场景变化，等待下一场战斗：{error}")

    def _read_player_state(
        self,
        connection: ProcessMemory,
        layout: PlayerStateLayout,
    ) -> tuple[int, PlayerState, tuple[int, ...]]:
        paths = (layout.root,) + tuple(
            field.path for field in _layout_fields(layout) if field.path is not None
        )
        before = tuple(_resolve_address_path_token(connection, path) for path in paths)
        base = before[0][0]
        guard_values = tuple(_read_integer(connection, base, field) for field in layout.guards)
        inhibitor_values = tuple(
            _read_integer(connection, base, field) for field in layout.write_inhibitors
        )
        state: PlayerState | None = None
        torn_segment_error: RealtimeEffectError | None = None
        try:
            state = self._read_player_state_at(connection, layout, base)
        except RealtimeEffectError as error:
            if layout.segmented_health is None or str(error) not in (
                "分段生命数值关系异常",
                "死亡标记与当前生命段不一致",
            ):
                raise
            torn_segment_error = error
        # 先复核门禁和地址链：这些变化是身份/场景失效，不能伪装成普通战斗数值竞争。
        if guard_values != tuple(_read_integer(connection, base, field) for field in layout.guards):
            raise RealtimePlayerUnavailableError("玩家场景门禁在读取期间变化")
        if inhibitor_values != tuple(
            _read_integer(connection, base, field) for field in layout.write_inhibitors
        ):
            raise RealtimeFrameUnstableError("实时写入抑制状态在读取期间变化")
        after = tuple(_resolve_address_path_token(connection, path) for path in paths)
        if before != after:
            raise RealtimePlayerUnavailableError("玩家地址链在采样期间发生变化，已丢弃整帧")
        if layout.segmented_health is not None:
            # 多字段状态必须复核；正常攻击/冷却可能推进数值，但不意味着对象身份失效。
            try:
                repeated = self._read_player_state_at(connection, layout, base)
            except RealtimeEffectError:
                if torn_segment_error is not None:
                    raise torn_segment_error from None
                raise
            if torn_segment_error is not None:
                raise RealtimeFrameUnstableError("分段生命在首次采样期间变化，已丢弃整帧")
            assert state is not None
            if state != repeated:
                if (
                    state.player_identity != repeated.player_identity
                    or state.object_identity != repeated.object_identity
                    or state.battle_state != repeated.battle_state
                    or state.scene_guard_values != repeated.scene_guard_values
                ):
                    raise RealtimePlayerUnavailableError("玩家身份或战斗场景在采样期间变化")
                raise RealtimeFrameUnstableError("分段玩家状态在读取期间变化，已丢弃整帧")
        assert state is not None
        resolved_path_token = tuple(
            pointer
            for resolved_address, pointers in before
            for pointer in (*pointers, resolved_address)
        )
        token = (*resolved_path_token, state.player_identity, state.object_identity)
        state = replace(state, write_inhibited=any(inhibitor_values))
        return base, state, token

    @staticmethod
    def _read_player_state_at(
        connection: ProcessMemory,
        layout: PlayerStateLayout,
        base: int,
    ) -> PlayerState:
        health = _read_integer(connection, base, layout.health)
        max_health = _read_integer(connection, base, layout.max_health)
        segmented = layout.segmented_health
        if segmented is not None:
            remaining = _read_integer(connection, base, segmented.remaining_segments)
            if max_health < 0 or remaining > max_health or health > segmented.segment_capacity:
                raise RealtimeEffectError("分段生命数值关系异常")
            if remaining == -1:
                if health != 0:
                    raise RealtimeEffectError("死亡标记与当前生命段不一致")
                health = 0
            else:
                health += remaining * segmented.segment_capacity
            max_health = (max_health + 1) * segmented.segment_capacity
        battle_state = _read_integer(connection, base, layout.battle_state)
        scene_guard_values = tuple(
            _read_integer(connection, base, field) for field in layout.scene_guards
        )
        scene_name = next(
            (
                scene.name
                for scene in layout.battle_scenes
                if (scene.battle_state, scene.scene_guard_values)
                == (battle_state, scene_guard_values)
            ),
            "未识别状态",
        )
        return PlayerState(
            health=health,
            max_health=max_health,
            heat=_read_integer(connection, base, layout.heat),
            max_heat=_read_integer(connection, base, layout.max_heat),
            player_identity=_read_integer(connection, base, layout.player_identity),
            object_identity=_read_integer(connection, base, layout.object_identity),
            battle_state=battle_state,
            force_crash=(
                _read_integer(connection, base, layout.force_crash)
                if layout.force_crash is not None
                else None
            ),
            max_force_crash=(
                layout.force_crash.maximum if layout.force_crash is not None else None
            ),
            scene_guard_values=scene_guard_values,
            scene_name=scene_name,
        )

    @staticmethod
    def _validate_player_state(state: PlayerState, layout: PlayerStateLayout) -> None:
        if not state.is_player:
            raise RealtimePlayerUnavailableError("正在切换玩家对象")
        scene_key = (state.battle_state, state.scene_guard_values)
        if scene_key not in {
            (scene.battle_state, scene.scene_guard_values) for scene in layout.battle_scenes
        }:
            raise RealtimePlayerUnavailableError("当前不在普通或练习战斗中")
        if state.max_health <= 0 or state.max_heat <= 0 or state.health < 0 or state.heat < 0:
            raise RealtimeEffectError("玩家状态上限或当前值无效")
        if state.health > state.max_health or state.heat > state.max_heat:
            raise RealtimeEffectError("玩家状态数值关系异常")
        if (state.force_crash is None) != (state.max_force_crash is None):
            raise RealtimeEffectError("Force Crash 仪表布局不完整")
        if state.force_crash is not None and state.max_force_crash is not None:
            if state.max_force_crash <= 0 or not 0 <= state.force_crash <= state.max_force_crash:
                raise RealtimeEffectError("Force Crash 仪表数值关系异常")

    def _require_connection(self) -> ProcessMemory:
        connection = self._connection
        if connection is None or not connection.is_alive():
            raise RealtimeEffectError("实时会话未连接或目标实例已退出")
        return connection

    def _require_player_connection(self) -> tuple[ProcessMemory, PlayerStateLayout]:
        connection = self._require_connection()
        layout = self._profile.player_state
        if layout is None:
            raise RealtimeEffectError("当前版本没有经过验证的玩家状态布局")
        return connection, layout

    def _require_permission(
        self,
        permission: Literal[
            "health_restore",
            "health_maintenance",
            "heat_reduce",
            "heat_maintenance",
            "force_crash_refill",
            "force_crash_maintenance",
            "weapon_repeat",
            "enemy_clear",
            "enemy_clear_maintenance",
            "wave_skip",
        ],
        display_name: str,
    ) -> None:
        if not getattr(self._profile.permissions, permission):
            raise RealtimeEffectError(f"{display_name}未授权：当前 Profile 没有写入证据")

    @staticmethod
    def _require_write_safe(state: PlayerState) -> None:
        if state.write_inhibited:
            raise RealtimeWritePausedError("Force Crash 发动中，当前帧暂停实时写入")

    def _active_effects(self) -> tuple[str, ...]:
        effects: list[str] = []
        if self._health_target is not None:
            effects.append("health")
        if self._heat_target is not None:
            effects.append("heat")
        if self._weapon_repeat_enabled:
            effects.append("weapon_repeat")
        if self._force_crash_enabled:
            effects.append("force_crash")
        if self._enemy_clear_enabled:
            effects.append("enemy_clear")
        return tuple(effects)

    def _clear_maintenance_targets(self) -> None:
        self._health_target = None
        self._heat_target = None
        self._heat_zero_transition_pending = False
        self._health_object_token = None
        self._heat_object_token = None
        self._weapon_repeat_enabled = False
        self._weapon_object_token = None
        self._practice_effects_active = False
        self._force_crash_enabled = False
        self._force_crash_object_token = None
        self._enemy_clear_enabled = False
        self._enemy_clear_object_token = None

    def _refresh_snapshot(self, message: str) -> RealtimeSnapshot:
        connection = self._require_connection()
        self._snapshot = RealtimeSnapshot(
            "connected_read_only",
            connection.instance,
            True,
            message,
            self._active_effects(),
        )
        return self._snapshot


def _layout_fields(layout: PlayerStateLayout) -> tuple[IntegerField, ...]:
    fields: tuple[IntegerField, ...] = (
        layout.health,
        layout.max_health,
        layout.heat,
        layout.max_heat,
        layout.player_identity,
        layout.object_identity,
        layout.battle_state,
    )
    if layout.segmented_health is not None:
        fields += (layout.segmented_health.remaining_segments,)
    if layout.force_crash is not None:
        fields += (layout.force_crash,)
    return fields + layout.guards + layout.scene_guards + layout.write_inhibitors


def _read_integer(connection: ProcessMemory, base: int, field: IntegerField) -> int:
    raw = connection.read(_field_address(connection, base, field), field.size)
    if len(raw) != field.size:
        raise RealtimeEffectError("玩家字段读取不完整")
    value = int(unpack(_integer_format(field.size, field.signed), raw)[0])
    if not field.minimum <= value <= field.maximum:
        raise RealtimeEffectError(f"字段 {hex(field.relative_address)} 的值 {value} 超出已验证范围")
    return value


def _field_address(connection: ProcessMemory, base: int, field: IntegerField) -> int:
    if field.path is not None:
        base = _resolve_address_path(connection, field.path)
    return base + field.relative_address


def _encode_integer(value: int, field: IntegerField) -> bytes:
    return pack(_integer_format(field.size, field.signed), value)


def _read_pointer(connection: ProcessMemory, address: int, size: int, name: str) -> int:
    raw = connection.read(address, size)
    if len(raw) != size:
        raise RealtimeEffectError(f"{name}指针读取不完整")
    pointer = int.from_bytes(raw, "little", signed=False)
    if pointer <= 0 or pointer % 2:
        raise RealtimeEffectError(f"{name}指针为空或未对齐")
    return pointer


def _encode_segmented_health(
    total_health: int,
    segment_capacity: int,
    maximum_segments: int,
    current_field: IntegerField,
    remaining_field: IntegerField,
) -> bytes:
    remaining_segments, current_segment = divmod(total_health - 1, segment_capacity)
    current_segment += 1
    if remaining_segments > maximum_segments:
        raise RealtimeEffectError("分段生命目标超过最大段数")
    return _encode_integer(current_segment, current_field) + _encode_integer(
        remaining_segments, remaining_field
    )


def _integer_format(size: int, signed: bool) -> str:
    if signed:
        return {1: "<b", 2: "<h", 4: "<i", 8: "<q"}[size]
    return {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}[size]


def _resolve_address_path(connection: ProcessMemory, path: AddressPath) -> int:
    return _resolve_address_path_token(connection, path)[0]


def _resolve_address_path_token(
    connection: ProcessMemory, path: AddressPath
) -> tuple[int, tuple[int, ...]]:
    address = connection.instance.image_base + path.root_relative_address
    pointers = []
    for offset in path.pointer_offsets:
        raw = connection.read(address, path.pointer_size)
        if len(raw) != path.pointer_size:
            raise RealtimeEffectError("玩家地址链读取不完整")
        pointer = int.from_bytes(raw, "little", signed=False)
        if pointer <= 0:
            raise RealtimePlayerUnavailableError("当前场景没有活动的玩家对象")
        if pointer % path.pointer_size or pointer + offset >= 1 << (path.pointer_size * 8):
            raise RealtimeEffectError("玩家地址链包含未对齐或越界指针")
        pointers.append(pointer)
        address = pointer + offset
    return address, tuple(pointers)
