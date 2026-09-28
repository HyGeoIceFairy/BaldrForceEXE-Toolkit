"""解析官方 BFWT 武器开发条件表。"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WeaponRequirement:
    weapon_name_ja: str
    required_exp: int


@dataclass(frozen=True)
class WeaponDefinition:
    official_number: int
    name_ja: str
    normalized_name_ja: str
    requirements: tuple[WeaponRequirement, ...]


_ROW_PATTERN = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_PATTERN = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_HIDDEN_PATTERN = re.compile(
    r"<span\b[^>]*style=[\"'][^\"']*display\s*:\s*none[^\"']*[\"'][^>]*>.*?</span>",
    re.IGNORECASE | re.DOTALL,
)
_RUBY_READING_PATTERN = re.compile(r"<rt\b[^>]*>.*?</rt>", re.IGNORECASE | re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")


def normalize_japanese_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace(" ", "")


def _cell_text(value: str) -> str:
    value = _HIDDEN_PATTERN.sub("", value)
    value = _RUBY_READING_PATTERN.sub("", value)
    value = _TAG_PATTERN.sub("", value)
    value = html.unescape(value).replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", value)


def parse_bfwt_html(data: bytes) -> tuple[WeaponDefinition, ...]:
    text = data.decode("cp932", errors="strict")
    weapons: list[WeaponDefinition] = []
    for row_html in _ROW_PATTERN.findall(text):
        cells = [_cell_text(value) for value in _CELL_PATTERN.findall(row_html)]
        if len(cells) < 9 or not cells[1].isdigit():
            continue
        requirements: list[WeaponRequirement] = []
        for name_index, exp_index in ((3, 4), (5, 6), (7, 8)):
            name = cells[name_index]
            exp = cells[exp_index]
            if not name and not exp:
                continue
            if not name or not exp.isdigit():
                raise ValueError(f"BFWT 第 {cells[1]} 项前置条件不完整")
            requirements.append(WeaponRequirement(name, int(exp)))
        name = cells[2]
        weapons.append(
            WeaponDefinition(
                official_number=int(cells[1]),
                name_ja=name,
                normalized_name_ja=normalize_japanese_name(name),
                requirements=tuple(requirements),
            )
        )

    if len(weapons) != 63 or [item.official_number for item in weapons] != list(range(63)):
        raise ValueError("BFWT 武器编号不连续或数量不是 63")
    return tuple(weapons)


def load_bfwt(path: str | Path) -> tuple[WeaponDefinition, ...]:
    return parse_bfwt_html(Path(path).read_bytes())

