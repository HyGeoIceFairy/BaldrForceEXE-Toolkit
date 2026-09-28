"""已验证游戏安装的内容指纹与功能门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .snapshot import sha256_file

KNOWN_INSTALLATION_NAME = "BALDR FORCE EXE 中文整合版（已验证样本 2026-09）"
# 当前 BaldrForce.exe 是带约 44 MiB 附加数据的封装启动映像；
# BaldrForce.exe.bak 是其运行时展开的 32 位游戏映像研究副本。
# 在隔离安装中启动当前封装后，运行时 VA 0x43DDD0、0x43E8D6、0x43F010、
# 0x44DAC0、0x44E8AB、0x456CB4、0x4651B1、0x47B5EA、0x47DCA4、
# 0x47E940 各 32 字节均与该研究副本逐字节一致，覆盖本工具依赖的存档、
# 武器开发、成长和热度路径。写入门禁仍要求封装、研究映像与数据文件整组匹配。
KNOWN_FINGERPRINTS = {
    "BaldrForce.exe": "7A9341D40E24C4728ECC8666FE77BBE01EE78D65C65E7F6F9A486BA9630CAD76",
    "BaldrForce.exe.bak": "C38F7946FA8AC8F714F77ED15394785170507FF8133D42B3D571518E3C9CF605",
    "Table.pac": "B1014ABBD89098193C7BB47EE9747EC6A1E25AE524C5B615CB46ED3E6130722B",
    "Exe.pac": "FB18B39B5727349B6C0C0B9BD8F475003FE7EE44588DFA2C5EC0A846D118C200",
    "Dat/Waza/TOORU.WAZ": "897227B766CF8E5DE694D2E7CD9F5F32779F13F762170ECBAE135085B5228677",
}


@dataclass(frozen=True)
class InstallationIdentity:
    name: str
    fingerprints: dict[str, str | None]
    is_known: bool
    read_supported: bool
    write_supported: bool
    reasons: tuple[str, ...]


def identify_installation(game_root: str | Path) -> InstallationIdentity:
    """计算关键文件指纹；只有全部匹配时才开放首版写入。"""

    root = Path(game_root).resolve(strict=True)
    fingerprints: dict[str, str | None] = {}
    mismatches: list[str] = []
    for relative, expected in KNOWN_FINGERPRINTS.items():
        path = root / Path(relative)
        actual = sha256_file(path) if path.is_file() else None
        fingerprints[relative] = actual
        if actual is None:
            mismatches.append(f"缺少版本识别文件：{relative}")
        elif actual != expected:
            mismatches.append(f"文件指纹不匹配：{relative}")

    known = not mismatches
    reasons = (
        ("关键游戏文件与已研究版本完全匹配",)
        if known
        else tuple(mismatches)
    )
    return InstallationIdentity(
        name=KNOWN_INSTALLATION_NAME if known else "未知或未完整验证的游戏版本",
        fingerprints=fingerprints,
        is_known=known,
        read_supported=True,
        write_supported=known,
        reasons=reasons,
    )
