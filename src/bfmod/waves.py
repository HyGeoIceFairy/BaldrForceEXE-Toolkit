"""只推进原生增援调度时钟；实际战斗计时、事件和敌人记录保持原样。"""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack_from

from .combat import CombatFrame, CombatFrameChangedError, CombatValidationError, _pointer
from .process_memory import ProcessMemory


@dataclass(frozen=True)
class WaveSkipLayout:
    controller_pointer_rva: int = 0x183728
    result_rva: int = 0x174968
    finish_timer_rva: int = 0xE0627

    def __post_init__(self) -> None:
        if min(self.controller_pointer_rva, self.result_rva, self.finish_timer_rva) < 0:
            raise ValueError("波次布局 RVA 必须非负")


@dataclass(frozen=True)
class WaveSchedule:
    controller: int
    attributes: int
    clock: int
    next_time: int | None
    records: bytes
    remaining: int

    @property
    def token(self) -> tuple[int, int, bytes, int]:
        return (self.controller, self.attributes, self.records, self.remaining)


def read_wave_schedule(
    connection: ProcessMemory, layout: WaveSkipLayout, frame: CombatFrame
) -> WaveSchedule | None:
    # 跟随原生全灭后推进路径：只有场上非友军均已移除，才可越过空闲等待。
    if frame.protected_slots or any(a.slot and a.team != 0 for a in frame.actors):
        return None
    base = connection.instance.image_base
    if (
        connection.read(base + layout.result_rva, 1) != b"\xff"
        or connection.read(base + layout.finish_timer_rva, 1) != b"\x00"
    ):
        return None
    root_address = base + layout.controller_pointer_rva
    root = int.from_bytes(connection.read(root_address, 4), "little")
    if not root:
        return None
    header = connection.read(_pointer(root), 0x40)
    if header[0] != 1 or header[1] != 10 or header[0x3C] != 0:
        return None
    attributes = _pointer(unpack_from("<I", header, 0x38)[0])
    raw = connection.read(attributes, 0x80C)
    remaining, clock, repeat = unpack_from("<3i", raw)
    if repeat != 0:  # 不快进无限循环练习／生存波次。
        return None
    if not (0 <= remaining <= 64 and 0 <= clock <= 999999):
        raise CombatValidationError("增援调度计数或时钟无效")
    pending = []
    for index in range(64):
        offset = 12 + 32 * index
        flag = raw[offset]
        if flag not in (0, 1, 2):
            raise CombatValidationError("增援记录状态无效")
        if flag != 1:
            continue
        timestamp, limit, team = unpack_from("<3i", raw, offset + 4)
        if not (0 <= timestamp <= 999999 and 1 <= limit <= 32):
            raise CombatValidationError("增援时间或同屏限制无效")
        if team != 1:
            return None  # 不提前调度友军、护送或未知阵营。
        pending.append(timestamp)
    if len(pending) != remaining:
        raise CombatFrameChangedError("增援记录正在创建或消费")
    after = connection.read(attributes, 0x80C)
    if (
        int.from_bytes(connection.read(root_address, 4), "little") != root
        or connection.read(root, 0x40) != header
        or after[:4] != raw[:4]
        or after[8:] != raw[8:]
    ):
        raise CombatFrameChangedError("增援调度器正在替换或更新")
    clock_after = unpack_from("<i", after, 4)[0]
    if clock_after < clock or clock_after > 999999:
        raise CombatFrameChangedError("增援时钟在采样期间重置")
    next_time = min(pending) if pending else None
    return WaveSchedule(root, attributes, clock_after, next_time, raw[12:], remaining)
