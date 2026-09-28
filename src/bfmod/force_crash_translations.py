"""公开发行不内置游戏文案；在用户安装上读取名称和说明。"""

from __future__ import annotations

FORCE_CRASH_NAMES_ZH = tuple(f"Force Crash #{index:02d}" for index in range(21))
FORCE_CRASH_DESCRIPTIONS_ZH = ("",) * len(FORCE_CRASH_NAMES_ZH)
