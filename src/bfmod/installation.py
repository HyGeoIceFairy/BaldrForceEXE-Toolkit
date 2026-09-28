"""BALDR FORCE EXE 安装目录的发现与严格校验。"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

GAME_ROOT_ENVIRONMENT_VARIABLE = "BFMOD_GAME_ROOT"
REQUIRED_GAME_FILES = ("BaldrForce.exe", "Table.pac", "Exe.pac")
DEFAULT_GAME_ROOT_CANDIDATES = (
    Path("C:/Game/Baldr Force Exe"),
    Path("D:/Game/Baldr Force Exe"),
)


class InstallationError(ValueError):
    """游戏目录缺失或不是受支持的 BALDR FORCE EXE 安装。"""


def validate_game_root(path: str | Path) -> Path:
    """返回规范化游戏目录；目录结构不完整时给出可操作的错误。"""

    game_root = Path(path).expanduser().resolve(strict=False)
    if not game_root.is_dir():
        raise InstallationError(f"游戏目录不存在或不是文件夹：{game_root}")

    missing = [name for name in REQUIRED_GAME_FILES if not (game_root / name).is_file()]
    if not (game_root / "Save").is_dir():
        missing.append("Save/")
    if missing:
        raise InstallationError(
            f"所选目录不是完整的 BALDR FORCE EXE 安装；缺少：{', '.join(missing)}"
        )
    return game_root


def resolve_game_root(
    explicit_path: str | Path | None,
    *,
    environment: Mapping[str, str] | None = None,
    conventional_candidates: Iterable[Path] = DEFAULT_GAME_ROOT_CANDIDATES,
) -> Path:
    """按显式路径、环境变量、常见目录的顺序寻找游戏安装。"""

    if explicit_path is not None:
        return validate_game_root(explicit_path)

    variables = os.environ if environment is None else environment
    configured = variables.get(GAME_ROOT_ENVIRONMENT_VARIABLE)
    if configured:
        return validate_game_root(configured)

    for candidate in conventional_candidates:
        if candidate.is_dir():
            try:
                return validate_game_root(candidate)
            except InstallationError:
                continue

    raise InstallationError(
        "未找到 BALDR FORCE EXE 游戏目录；请使用 --game-root 指定，"
        f"或设置 {GAME_ROOT_ENVIRONMENT_VARIABLE} 环境变量"
    )
