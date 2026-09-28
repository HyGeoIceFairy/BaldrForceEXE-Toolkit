"""公开发行不内置游戏文案；在用户安装上读取名称和说明。"""

from __future__ import annotations

WEAPON_NAMES_ZH = tuple(f"武器 #{index:02d}" for index in range(63))
WEAPON_DESCRIPTIONS_ZH = ("",) * len(WEAPON_NAMES_ZH)
