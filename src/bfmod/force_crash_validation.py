"""只读检查 Force Crash 隔离候选槽位的结构和精确字节白名单。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from pathlib import Path

from .force_crash import FORCE_CRASH_COUNT, FORCE_CRASH_RECORD_START
from .save_slot import decode_save_slot
from .weapon_progress import (
    DEVELOPMENT_COMPLETE_STATE,
    PROGRESS_RECORD_OFFSET,
    PROGRESS_RECORD_SIZE,
    decode_weapon_progress_attachment,
)

RECORD_COUNT = FORCE_CRASH_RECORD_START + FORCE_CRASH_COUNT


class ForceCrashValidationError(ValueError):
    """候选槽位不满足 Force Crash 最小变化白名单。"""


@dataclass(frozen=True)
class ForceCrashCandidateReport:
    source_path: Path
    candidate_path: Path
    source_sha256: str
    candidate_sha256: str
    changed_force_crash_records: tuple[int, ...]
    changed_decoded_offsets: tuple[int, ...]


def build_force_crash_validation_record(
    report: ForceCrashCandidateReport,
    *,
    slot_name: str,
) -> str:
    """创建不声称实机通过的人工闭环记录模板。"""

    if not slot_name or Path(slot_name).name != slot_name:
        raise ForceCrashValidationError("目标槽位名必须是不含路径的文件名")
    payload = {
        "schema": 1,
        "capability": "force_crash_offline_unlock",
        "created_utc": datetime.now(UTC).isoformat(),
        "automated_validation": "passed",
        "source_path": str(report.source_path),
        "candidate_path": str(report.candidate_path),
        "source_sha256": report.source_sha256,
        "candidate_sha256": report.candidate_sha256,
        "target_slot_name": slot_name,
        "changed_force_crash_records": list(report.changed_force_crash_records),
        "changed_decoded_offsets": list(report.changed_decoded_offsets),
        "manual_validation": {
            "overall": "pending",
            "checks": {
                "displayed": "pending",
                "equippable": "pending",
                "activatable": "pending",
                "resaved": "pending",
                "persisted": "pending",
            },
            "notes": "",
        },
        "restoration": {
            "status": "pending",
            "restored_sha256": None,
        },
    }
    return dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_force_crash_validation_record(
    report: ForceCrashCandidateReport,
    output_path: str | Path,
    *,
    slot_name: str,
) -> Path:
    """以独占创建方式发布记录，避免静默覆盖既有人工验收证据。"""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(build_force_crash_validation_record(report, slot_name=slot_name))
    return output.resolve(strict=True)


def inspect_force_crash_candidate(
    source_path: str | Path,
    candidate_path: str | Path,
) -> ForceCrashCandidateReport:
    """只读取两个文件；要求仅有 attachment 1 中目标记录的状态字节变成 5。"""

    source = Path(source_path).resolve(strict=True)
    candidate = Path(candidate_path).resolve(strict=True)
    if source == candidate:
        raise ForceCrashValidationError("源槽位与候选槽位必须是两个隔离文件")
    with source.open("rb") as source_stream:
        source_raw = source_stream.read()
    with candidate.open("rb") as candidate_stream:
        candidate_raw = candidate_stream.read()
    source_slot = decode_save_slot(source_raw)
    candidate_slot = decode_save_slot(candidate_raw)
    if source_slot.version != candidate_slot.version:
        raise ForceCrashValidationError("候选槽位容器版本与源槽位不同")
    if source_slot.main_data != candidate_slot.main_data:
        raise ForceCrashValidationError("候选槽位主 Huffman 明文发生变化")
    if source_slot.main_payload != candidate_slot.main_payload:
        raise ForceCrashValidationError("候选槽位主 Huffman 载荷发生变化")
    for index in (0, 2, 3):
        if source_slot.attachments[index] != candidate_slot.attachments[index]:
            raise ForceCrashValidationError(f"候选槽位 attachment {index} 发生变化")

    source_progress = decode_weapon_progress_attachment(
        source_slot.weapon_progress_attachment,
        record_count=RECORD_COUNT,
    )
    candidate_progress = decode_weapon_progress_attachment(
        candidate_slot.weapon_progress_attachment,
        record_count=RECORD_COUNT,
    )
    if len(source_progress.decoded) != len(candidate_progress.decoded):
        raise ForceCrashValidationError("候选 Force Crash 成长块解码长度与源槽位不同")
    changed_offsets = tuple(
        index
        for index, (before, after) in enumerate(
            zip(source_progress.decoded, candidate_progress.decoded, strict=True)
        )
        if before != after
    )
    if not changed_offsets:
        raise ForceCrashValidationError("候选槽位没有 Force Crash 状态变化")

    allowed_offsets = {
        PROGRESS_RECORD_OFFSET + record_index * PROGRESS_RECORD_SIZE: record_index
        for record_index in range(
            FORCE_CRASH_RECORD_START,
            FORCE_CRASH_RECORD_START + FORCE_CRASH_COUNT,
        )
    }
    unexpected = tuple(offset for offset in changed_offsets if offset not in allowed_offsets)
    if unexpected:
        formatted = ", ".join(hex(offset) for offset in unexpected[:8])
        raise ForceCrashValidationError(f"候选变化超出 Force Crash 字节白名单：{formatted}")
    changed_records = tuple(allowed_offsets[offset] for offset in changed_offsets)
    for offset in changed_offsets:
        if candidate_progress.decoded[offset] != DEVELOPMENT_COMPLETE_STATE:
            raise ForceCrashValidationError("Force Crash 目标状态不是已证明的开发完成状态 5")
        if source_progress.decoded[offset] == DEVELOPMENT_COMPLETE_STATE:
            raise ForceCrashValidationError("候选重复写入已经解锁的 Force Crash")

    return ForceCrashCandidateReport(
        source_path=source,
        candidate_path=candidate,
        source_sha256=sha256(source_raw).hexdigest().upper(),
        candidate_sha256=sha256(candidate_raw).hexdigest().upper(),
        changed_force_crash_records=changed_records,
        changed_decoded_offsets=changed_offsets,
    )
