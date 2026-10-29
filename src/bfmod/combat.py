"""原生敌方退场路径的只读对象校验；不写 HP、脚本或胜利状态。"""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack, unpack_from

from .process_memory import ProcessMemory


class CombatFrameChangedError(RuntimeError):
    """对象表或事件表正在重建，本帧应跳过。"""


class CombatValidationError(RuntimeError):
    """战斗对象不符合已证明的结构，禁止写入。"""


@dataclass(frozen=True)
class EnemyClearLayout:
    table_relative_address: int = 0x138A00
    events_pointer_relative_address: int = 0xD9344
    removal_timer_offset: int = 0x14E

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.table_relative_address,
                self.events_pointer_relative_address,
                self.removal_timer_offset,
            )
        ):
            raise ValueError("敌方退场布局偏移必须非负")


@dataclass(frozen=True)
class EnemyClearResult:
    requested: int = 0
    protected: int = 0


@dataclass(frozen=True)
class CombatActor:
    slot: int
    root: int
    attributes: int
    identity: int
    active: int
    team: int
    kind: int
    health: int
    remaining: int
    maximum: int
    removal_timer: int

    @property
    def token(self) -> tuple[int, ...]:
        return (
            self.slot,
            self.root,
            self.attributes,
            self.identity,
            self.active,
            self.team,
            self.kind,
            self.maximum,
        )

    @property
    def clearable(self) -> bool:
        return (
            self.slot != 0
            and self.active == 1
            and self.team == 1
            and self.remaining >= 0
            and self.removal_timer == 0
        )


@dataclass(frozen=True)
class CombatFrame:
    actors: tuple[CombatActor, ...]
    protected_slots: frozenset[int]
    events_token: tuple[int, bytes]


def _pointer(value: int) -> int:
    if not 0x10000 <= value <= 0x7FFFFFFF or value % 4:
        raise CombatValidationError("战斗组件指针无效")
    return value


def read_actor(
    connection: ProcessMemory, slot: int, root: int, layout: EnemyClearLayout
) -> CombatActor:
    header = connection.read(_pointer(root), 0x6C)
    if header[0] not in (0, 1) or header[1] != 5:
        raise CombatValidationError("战斗对象类别或活动状态无效")
    attributes = _pointer(unpack_from("<I", header, 0x38)[0])
    identity = _pointer(unpack_from("<I", header, 0x68)[0])
    info = connection.read(identity, 12)
    if unpack_from("<I", info, 4)[0] != slot or info[3] not in range(4):
        raise CombatValidationError("战斗对象槽位身份或阵营无效")
    health, remaining, maximum = unpack("<3h", connection.read(attributes + 0x16, 6))
    timer = unpack("<h", connection.read(attributes + layout.removal_timer_offset, 2))[0]
    if not (
        0 <= health <= 0x820 and -1 <= remaining <= maximum <= 31 and maximum >= 0 and timer >= 0
    ):
        raise CombatValidationError("战斗对象生命或退场计时超出白名单")
    if remaining == -1 and health != 0:
        raise CombatFrameChangedError("战斗对象正在切换死亡状态")
    if (
        connection.read(root, 2) != header[:2]
        or any(
            connection.read(root + offset, 4) != header[offset : offset + 4]
            for offset in (0x38, 0x68)
        )
        or connection.read(identity, 12) != info
    ):
        raise CombatFrameChangedError("战斗对象组件正在替换")
    return CombatActor(
        slot,
        root,
        attributes,
        identity,
        header[0],
        info[3],
        unpack_from("<I", info, 8)[0],
        health,
        remaining,
        maximum,
        timer,
    )


def read_combat_frame(connection: ProcessMemory, layout: EnemyClearLayout) -> CombatFrame:
    base = connection.instance.image_base
    table_address = base + layout.table_relative_address
    table = connection.read(table_address, 128)
    roots = unpack("<32I", table)
    present = [root for root in roots if root]
    if len(set(present)) != len(present):
        raise CombatValidationError("战斗对象表含重复对象")
    actors = tuple(
        read_actor(connection, slot, root, layout) for slot, root in enumerate(roots) if root
    )
    # 原生计时设置器在 type=3 的对应槽位剧情事件存在时拒绝退场。
    pointer_address = base + layout.events_pointer_relative_address
    event_pointer = _pointer(int.from_bytes(connection.read(pointer_address, 4), "little"))
    events = connection.read(event_pointer, 8 * 12)
    protected = frozenset(
        unpack_from("<I", events, n * 12 + 4)[0] for n in range(8) if events[n * 12 + 1] == 3
    )
    if (
        connection.read(table_address, 128) != table
        or int.from_bytes(connection.read(pointer_address, 4), "little") != event_pointer
        or connection.read(event_pointer, len(events)) != events
    ):
        raise CombatFrameChangedError("战斗对象表或剧情事件正在更新")
    if not actors or actors[0].slot != 0 or actors[0].team != 0 or actors[0].active != 1:
        raise CombatFrameChangedError("本地玩家对象正在重建")
    components = [address for actor in actors for address in (actor.attributes, actor.identity)]
    if len(set(components)) != len(components) or set(components) & set(present):
        raise CombatValidationError("战斗对象共享组件或组件与对象重叠")
    return CombatFrame(actors, protected, (event_pointer, events))
