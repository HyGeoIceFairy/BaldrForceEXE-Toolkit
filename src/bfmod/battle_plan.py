"""跨场次只保留用户意图；每个新对象仍须通过 Monitor 的全部写入门禁。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .realtime import PlayerState, RealtimeEffectError, RealtimeMonitor


@dataclass
class BattlePlan:
    health: int | None = None
    full_health: bool = False
    heat: int | None = None
    weapon_repeat: bool = False
    practice_only: bool = False
    force_crash: bool = False

    @property
    def effects(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, enabled in (
                ("health", self.health is not None),
                ("heat", self.heat is not None),
                ("weapon_repeat", self.weapon_repeat),
                ("force_crash", self.force_crash),
            )
            if enabled
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> BattlePlan:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("force_crash", False)  # 兼容 v1.0.0/1.0.1 已保存方案。
            if not isinstance(data, dict) or set(data) != set(asdict(cls())):
                raise ValueError("战斗方案字段不完整")
            for key, minimum in (("health", 1), ("heat", 0)):
                value = data[key]
                if value is not None and (
                    type(value) is not int or not minimum <= value <= 1_000_000
                ):
                    raise ValueError("战斗方案目标值无效")
            for key in ("full_health", "weapon_repeat", "practice_only", "force_crash"):
                if type(data[key]) is not bool:
                    raise ValueError("战斗方案开关无效")
            return cls(**data)
        except (TypeError, KeyError, json.JSONDecodeError) as error:
            raise ValueError("无法读取战斗方案") from error

    def resume(self, monitor: RealtimeMonitor, player: PlayerState) -> bool:
        """仅恢复缺失效果，不缓存地址，也不重放一次性写入。"""
        if not self.effects or player.health <= 0 or player.write_inhibited:
            return False
        if self.practice_only and player.scene_name != "练习战斗":
            return False
        active = monitor.snapshot.active_effects
        missing = set(self.effects) - set(active)
        if not missing:
            return False
        try:
            if self.practice_only:
                monitor.enable_practice_effects()
            else:
                if "health" in missing and self.health is not None:
                    target = (
                        player.max_health
                        if self.full_health
                        else min(self.health, player.max_health)
                    )
                    monitor.enable_health_maintenance(target)
                if "heat" in missing and self.heat is not None:
                    monitor.enable_heat_maintenance(min(self.heat, player.max_heat))
                if "weapon_repeat" in missing:
                    monitor.enable_weapon_repeat()
            if "force_crash" in missing:
                monitor.enable_force_crash_maintenance()
        except RealtimeEffectError:
            # 启用组合时中途失效，不能留下半套授权。
            if monitor.snapshot.state == "connected_read_only":
                monitor.stop_effects()
            raise
        return True
