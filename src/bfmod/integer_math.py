"""复现游戏 x86 整数运算语义的小型纯函数。"""

from __future__ import annotations


def signed_divide_by_three(value: int) -> int:
    """返回有符号整数除以三、向零截断的结果。"""

    return value // 3 if value >= 0 else -((-value) // 3)
