"""大型主窗口之外的独立页面视图。

页面只负责创建控件并转发用户动作；工作区、草稿和实时会话的生命周期
仍由主窗口协调。这使页面可以分阶段提取，而不改变现有行为边界。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

Callback = Callable[..., None]


def _page_layout(page: QWidget, title: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout(page)
    layout.setContentsMargins(26, 22, 26, 18)
    heading = QLabel(title)
    heading.setObjectName("heading")
    caption = QLabel(subtitle)
    caption.setObjectName("muted")
    caption.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(caption)
    layout.addSpacing(8)
    return layout


class ForceCrashPage(QWidget):
    """Force Crash 目录和草稿控件，不持有领域会话。"""

    def __init__(
        self,
        *,
        slot_changed: Callback,
        search_changed: Callback,
        filter_changed: Callback,
        selection_changed: Callback,
        stage_unlock: Callback,
        discard_selected: Callback,
        undo: Callback,
        redo: Callback,
        discard_all: Callback,
        import_preset: Callback,
        export_preset: Callback,
        apply_changes: Callback,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = _page_layout(
            self,
            "Force Crash",
            "已证明的 21 项必杀技记录；解锁先进入草稿，只影响当前普通存档槽位。",
        )
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("存档"))
        self.slot_combo = QComboBox()
        self.slot_combo.setMinimumWidth(230)
        self.slot_combo.currentIndexChanged.connect(slot_changed)
        toolbar.addWidget(self.slot_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称、说明或编号…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(search_changed)
        toolbar.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(("全部 Force Crash", "已解锁", "未解锁", "有待应用修改"))
        self.filter.currentIndexChanged.connect(filter_changed)
        toolbar.addWidget(self.filter)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("编号", "Force Crash", "状态", "成长记录", "草稿"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(selection_changed)
        splitter.addWidget(self.table)

        detail = QWidget()
        detail.setMinimumWidth(360)
        detail_layout = QVBoxLayout(detail)
        self.detail_title = QLabel("选择一项或多项 Force Crash")
        self.detail_title.setObjectName("heading")
        self.detail_title.setWordWrap(True)
        self.detail_state = QLabel("—")
        self.detail_state.setObjectName("muted")
        self.detail_description = QTextBrowser()
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_state)
        detail_layout.addWidget(QLabel("游戏资源中的说明"))
        detail_layout.addWidget(self.detail_description, 1)
        unlock = QPushButton("解锁选中 Force Crash")
        unlock.setObjectName("primary")
        unlock.clicked.connect(stage_unlock)
        select_all = QPushButton("全选当前结果")
        select_all.clicked.connect(self.table.selectAll)
        discard = QPushButton("移除选中项草稿")
        discard.clicked.connect(discard_selected)
        detail_layout.addWidget(select_all)
        detail_layout.addWidget(unlock)
        detail_layout.addWidget(discard)
        splitter.addWidget(detail)
        splitter.setSizes((850, 390))
        layout.addWidget(splitter, 1)

        pending = QFrame()
        pending_layout = QHBoxLayout(pending)
        self.pending_label = QLabel("没有待应用修改")
        self.pending_label.setObjectName("muted")
        pending_layout.addWidget(self.pending_label)
        pending_layout.addStretch()
        self.undo_button = QPushButton("撤销")
        self.undo_button.clicked.connect(undo)
        self.redo_button = QPushButton("重做")
        self.redo_button.clicked.connect(redo)
        discard_all_button = QPushButton("放弃全部")
        discard_all_button.clicked.connect(discard_all)
        import_preset_button = QPushButton("导入预设")
        import_preset_button.clicked.connect(import_preset)
        export_preset_button = QPushButton("导出预设")
        export_preset_button.clicked.connect(export_preset)
        self.apply_button = QPushButton("检查并应用")
        self.apply_button.setObjectName("primary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(apply_changes)
        for button in (
            self.undo_button,
            self.redo_button,
            discard_all_button,
            import_preset_button,
            export_preset_button,
            self.apply_button,
        ):
            pending_layout.addWidget(button)
        layout.addWidget(pending)

    def set_slots(self, slots: tuple[tuple[str, int], ...]) -> None:
        """原子替换槽位选项，避免装载工作区时发出中间选择事件。"""

        self.slot_combo.blockSignals(True)
        self.slot_combo.clear()
        for display_name, index in slots:
            self.slot_combo.addItem(display_name, index)
        self.slot_combo.blockSignals(False)

    def set_current_slot(self, index: int) -> None:
        self.slot_combo.blockSignals(True)
        self.slot_combo.setCurrentIndex(index)
        self.slot_combo.blockSignals(False)


class RealtimePage(QWidget):
    """实时连接、只读 HUD 和逐项授权效果的状态视图。"""

    def __init__(
        self,
        *,
        connect_monitor: Callback,
        stop_monitor: Callback,
        restore_health: Callback,
        reduce_heat: Callback,
        refill_force_crash: Callback,
        health_maintenance_changed: Callback,
        heat_maintenance_changed: Callback,
        weapon_repeat_changed: Callback,
        health_target_changed: Callback,
        heat_target_changed: Callback,
        clear_events: Callback,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        layout = _page_layout(
            content,
            "实时战斗",
            "自动识别普通战斗、练习模式和 Force Crash 发动状态。",
        )
        self._has_player_state = False
        self._write_paused = False
        self._health_restore_available = False
        self._health_maintenance_available = False
        self._heat_reduce_available = False
        self._heat_maintenance_available = False
        self._force_crash_refill_available = False
        self._weapon_repeat_available = False
        status_group = QGroupBox("连接状态")
        status_layout = QVBoxLayout(status_group)
        self.status = QLabel("尚未连接")
        self.status.setWordWrap(True)
        status_layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.connect_button = QPushButton("连接游戏")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(connect_monitor)
        self.stop_button = QPushButton("紧急全停")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(stop_monitor)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.stop_button)
        self.auto_reconnect = QCheckBox("断开后自动重连")
        self.auto_reconnect.setToolTip(
            "游戏重启或意外退出后自动恢复连接；手动停止后不会自动重连"
        )
        controls.addWidget(self.auto_reconnect)
        controls.addStretch()
        status_layout.addLayout(controls)

        self.practice_preset_button = QPushButton("一键练习：锁血＋自然冷却＋武器重复")
        self.practice_preset_button.setToolTip(
            "仅在已验证的练习战斗中启用三种已授权效果；不自动解锁存档，"
            "不锁 Force Crash；切换场景或紧急全停时自动停用"
        )
        self.practice_preset_button.setEnabled(False)
        status_layout.addWidget(self.practice_preset_button)

        self.last_stop_reason = QLabel("最近停用原因：无")
        self.last_stop_reason.setObjectName("muted")
        self.last_stop_reason.setWordWrap(True)
        status_layout.addWidget(self.last_stop_reason)

        boundary = QGroupBox("可用功能")
        boundary_layout = QVBoxLayout(boundary)
        self.capabilities = QLabel("载入游戏目录后将显示可用功能。")
        self.capabilities.setWordWrap(True)
        boundary_layout.addWidget(self.capabilities)
        layout.addWidget(status_group)
        layout.addWidget(boundary)

        hud = QGroupBox("玩家战斗仪表")
        hud_layout = QVBoxLayout(hud)
        self.hud_status = QLabel("连接游戏后自动显示战斗数据。")
        self.hud_status.setObjectName("muted")
        self.hud_status.setWordWrap(True)
        hud_layout.addWidget(self.hud_status)
        health_row = QHBoxLayout()
        health_row.addWidget(QLabel("HP"))
        self.health_value = QLabel("— / —")
        self.health_value.setMinimumWidth(110)
        health_row.addWidget(self.health_value)
        self.health_bar = QProgressBar()
        self.health_bar.setRange(0, 100)
        self.health_bar.setValue(0)
        self.health_bar.setTextVisible(False)
        self.health_bar.setObjectName("health")
        health_row.addWidget(self.health_bar, 1)
        hud_layout.addLayout(health_row)
        heat_row = QHBoxLayout()
        heat_row.addWidget(QLabel("热量"))
        self.heat_value = QLabel("— / —")
        self.heat_value.setMinimumWidth(110)
        heat_row.addWidget(self.heat_value)
        self.heat_bar = QProgressBar()
        self.heat_bar.setRange(0, 100)
        self.heat_bar.setValue(0)
        self.heat_bar.setTextVisible(False)
        self.heat_bar.setObjectName("heat")
        heat_row.addWidget(self.heat_bar, 1)
        hud_layout.addLayout(heat_row)
        force_crash_row = QHBoxLayout()
        force_crash_row.addWidget(QLabel("Force Crash"))
        self.force_crash_value = QLabel("— / —")
        self.force_crash_value.setMinimumWidth(110)
        force_crash_row.addWidget(self.force_crash_value)
        self.force_crash_bar = QProgressBar()
        self.force_crash_bar.setRange(0, 100)
        self.force_crash_bar.setValue(0)
        self.force_crash_bar.setTextVisible(False)
        self.force_crash_bar.setObjectName("forceCrash")
        self.force_crash_bar.setToolTip(
            "显示当前 Force Crash；可点击一次充满"
        )
        force_crash_row.addWidget(self.force_crash_bar, 1)
        hud_layout.addLayout(force_crash_row)
        layout.addWidget(hud)

        weapons = QGroupBox("当前武器")
        weapons_layout = QVBoxLayout(weapons)
        self.weapon_recent = QLabel("最近发动：—")
        self.weapon_recent.setWordWrap(True)
        weapons_layout.addWidget(self.weapon_recent)
        self.weapon_configuration = QLabel("配置：等待进入战斗后读取。")
        self.weapon_configuration.setObjectName("muted")
        self.weapon_configuration.setWordWrap(True)
        weapons_layout.addWidget(self.weapon_configuration)
        self.weapon_history = QLabel("当前连段历史：—")
        self.weapon_history.setObjectName("muted")
        self.weapon_history.setWordWrap(True)
        weapons_layout.addWidget(self.weapon_history)
        layout.addWidget(weapons)

        effects = QGroupBox("战斗修改")
        effects_layout = QVBoxLayout(effects)
        one_shot = QHBoxLayout()
        self.restore_health_button = QPushButton("一次回满 HP")
        self.restore_health_button.setToolTip("进入普通战斗或练习模式后可用")
        self.restore_health_button.clicked.connect(restore_health)
        self.restore_health_button.setEnabled(False)
        self.reduce_heat_button = QPushButton("一次降热")
        self.reduce_heat_button.setToolTip(
            "把共享热量降到右侧目标值；目标 0 会保留游戏的自然归零和武器恢复流程"
        )
        self.reduce_heat_button.clicked.connect(reduce_heat)
        self.reduce_heat_button.setEnabled(False)
        self.heat_once_target = QSpinBox()
        self.heat_once_target.setRange(0, 1_000_000)
        self.heat_once_target.setPrefix("目标 ")
        self.refill_force_crash_button = QPushButton("Force Crash 充满")
        self.refill_force_crash_button.setToolTip(
            "将 Force Crash 补充到 10000；每次点击执行一次"
        )
        self.refill_force_crash_button.clicked.connect(refill_force_crash)
        self.refill_force_crash_button.setEnabled(False)
        one_shot.addWidget(self.restore_health_button)
        one_shot.addWidget(self.reduce_heat_button)
        one_shot.addWidget(self.heat_once_target)
        one_shot.addWidget(self.refill_force_crash_button)
        one_shot.addStretch()
        effects_layout.addLayout(one_shot)
        continuous = QHBoxLayout()
        self.health_maintenance = QCheckBox("最低 HP 维持")
        self.health_maintenance.setToolTip("HP 低于目标值时自动恢复；离开战斗后自动关闭")
        self.health_maintenance.toggled.connect(health_maintenance_changed)
        self.health_maintenance.setEnabled(False)
        self.health_target = QSpinBox()
        self.health_target.setRange(1, 1_000_000)
        self.health_target.setValue(1)
        self.health_target.setEnabled(False)
        self.health_target.valueChanged.connect(health_target_changed)
        self.heat_maintenance = QCheckBox("热量阈值 / 武器恢复")
        self.heat_maintenance.setToolTip(
            "目标 0：快速冷却，并由游戏自然归零恢复本连段武器；"
            "非零目标：热量高于阈值时自动降低。离开战斗后自动关闭"
        )
        self.heat_maintenance.toggled.connect(heat_maintenance_changed)
        self.heat_maintenance.setEnabled(False)
        self.heat_target = QSpinBox()
        self.heat_target.setRange(0, 1_000_000)
        self.heat_target.setEnabled(False)
        self.heat_target.valueChanged.connect(heat_target_changed)
        self.weapon_repeat = QCheckBox("同连段武器可重复使用")
        self.weapon_repeat.setToolTip(
            "只恢复 12 个普通状态槽，不修改特殊槽、热量、伤害或命中；"
            "原生连段伤害衰减仍然生效"
        )
        self.weapon_repeat.toggled.connect(weapon_repeat_changed)
        self.weapon_repeat.setEnabled(False)
        continuous.addWidget(self.health_maintenance)
        continuous.addWidget(self.health_target)
        continuous.addSpacing(12)
        continuous.addWidget(self.heat_maintenance)
        continuous.addWidget(self.heat_target)
        continuous.addSpacing(12)
        continuous.addWidget(self.weapon_repeat)
        continuous.addStretch()
        effects_layout.addLayout(continuous)
        self.effect_status = QLabel("没有启用持续效果。")
        self.effect_status.setObjectName("muted")
        effects_layout.addWidget(self.effect_status)
        heat_recovery_note = QLabel(
            "提示：“热量阈值 / 武器恢复”仍通过自然归零结束连段；"
            "“同连段武器可重复使用”独立恢复已验证的 12 个普通槽，并维护极长连段历史安全边界。"
        )
        heat_recovery_note.setObjectName("muted")
        heat_recovery_note.setWordWrap(True)
        effects_layout.addWidget(heat_recovery_note)
        layout.addWidget(effects)

        events = QGroupBox("运行记录")
        events_layout = QVBoxLayout(events)
        self.event_log = QPlainTextEdit("尚无实时事件。")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(200)
        self.event_log.setMinimumHeight(150)
        events_layout.addWidget(self.event_log)
        self.clear_events_button = QPushButton("清空日志")
        self.clear_events_button.clicked.connect(clear_events)
        events_layout.addWidget(
            self.clear_events_button,
            alignment=Qt.AlignmentFlag.AlignRight,
        )
        layout.addWidget(events, 1)

    def show_capabilities(self, text: str, *, can_connect: bool) -> None:
        self.capabilities.setText(text)
        self.connect_button.setEnabled(can_connect)

    def show_player_state(
        self,
        health: int,
        max_health: int,
        heat: int,
        max_heat: int,
        battle_state: int,
        force_crash: int | None = None,
        max_force_crash: int | None = None,
        write_paused: bool = False,
        scene_name: str | None = None,
    ) -> None:
        first_valid_state = not self._has_player_state
        self._has_player_state = True
        self._write_paused = write_paused
        if write_paused:
            self.hud_status.setText("Force Crash 发动中 · 修改暂时暂停")
            self.hud_status.setObjectName("warning")
        else:
            mode = scene_name or ("练习模式" if battle_state == 3 else "普通战斗")
            self.hud_status.setText(f"{mode} · 可使用战斗修改")
            self.hud_status.setObjectName("good")
        self.hud_status.style().unpolish(self.hud_status)
        self.hud_status.style().polish(self.hud_status)
        self.health_value.setText(f"{health} / {max_health}")
        self.heat_value.setText(f"{heat} / {max_heat}")
        self.health_bar.setValue(round(health * 100 / max_health))
        self.heat_bar.setValue(round(heat * 100 / max_heat))
        if force_crash is not None and max_force_crash is not None:
            self.force_crash_value.setText(f"{force_crash} / {max_force_crash}")
            self.force_crash_bar.setValue(round(force_crash * 100 / max_force_crash))
        else:
            self.force_crash_value.setText("— / —")
            self.force_crash_bar.setValue(0)
        self.health_target.setMaximum(max_health)
        if first_valid_state:
            self.health_target.setValue(max_health)
        else:
            self.health_target.setValue(max(1, min(self.health_target.value(), max_health)))
        self.heat_once_target.setMaximum(max_heat)
        self.heat_target.setMaximum(max_heat)
        if first_valid_state:
            self.heat_once_target.setValue(0)
            self.heat_target.setValue(0)
        self._sync_effect_controls()

    def show_weapon_state(
        self,
        slots: tuple[tuple[int, int, bool], ...],
        recent_slot: int | None,
        recent_weapon_id: int | None,
        combo_active: bool,
        history: tuple[int, ...],
    ) -> None:
        if recent_slot is None or recent_weapon_id is None:
            self.weapon_recent.setText("最近发动：—")
        else:
            self.weapon_recent.setText(
                f"最近发动：槽 {recent_slot} · 实际武器 ID {recent_weapon_id}"
            )
        ordinary = "  ".join(
            f"{slot}:{weapon_id}{'●' if used else ''}"
            for slot, weapon_id, used in slots[:12]
        )
        special = slots[12] if len(slots) > 12 else None
        special_text = (
            f"特殊 12:{special[1]}{'●' if special[2] else ''}" if special is not None else "特殊：—"
        )
        self.weapon_configuration.setText(f"普通槽  {ordinary}\n{special_text}")
        history_text = " → ".join(str(weapon_id) for weapon_id in history) if history else "—"
        prefix = "当前连段历史" if combo_active else "空闲（有效历史）"
        self.weapon_history.setText(f"{prefix}：{history_text}")

    def clear_weapon_state(self) -> None:
        self.weapon_recent.setText("最近发动：—")
        self.weapon_configuration.setText("配置：等待进入战斗后读取。")
        self.weapon_history.setText("当前连段历史：—")

    def clear_player_state(self, reason: str) -> None:
        self._has_player_state = False
        self._write_paused = False
        self.clear_weapon_state()
        self.hud_status.setText(reason)
        self.hud_status.setObjectName("muted")
        self.hud_status.style().unpolish(self.hud_status)
        self.hud_status.style().polish(self.hud_status)
        self.health_value.setText("— / —")
        self.heat_value.setText("— / —")
        self.force_crash_value.setText("— / —")
        self.health_bar.setValue(0)
        self.heat_bar.setValue(0)
        self.force_crash_bar.setValue(0)
        self._sync_effect_controls()

    def show_active_effects(self, effects: tuple[str, ...]) -> None:
        names = []
        if "health" in effects:
            names.append("血量维持")
        if "heat" in effects:
            names.append("热量阈值 / 武器恢复")
        if "weapon_repeat" in effects:
            names.append("同连段武器可重复使用")
        self.effect_status.setText(f"已启用：{'、'.join(names)}" if names else "没有启用持续效果。")

    def reset_effect_controls(self) -> None:
        for checkbox in (self.health_maintenance, self.heat_maintenance, self.weapon_repeat):
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self.show_active_effects(())

    def sync_effect_controls(self, effects: tuple[str, ...]) -> None:
        """只撤销已经失效的授权；不自动启用任何持续效果。"""

        for effect, checkbox in (
            ("health", self.health_maintenance),
            ("heat", self.heat_maintenance),
            ("weapon_repeat", self.weapon_repeat),
        ):
            if effect not in effects and checkbox.isChecked():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

    def set_effect_capabilities(
        self,
        *,
        hud: bool,
        health_restore: bool,
        health_maintenance: bool,
        heat_reduce: bool,
        heat_maintenance: bool,
        force_crash_refill: bool = False,
        weapon_repeat: bool = False,
    ) -> None:
        self._health_restore_available = health_restore
        self._health_maintenance_available = health_maintenance
        self._heat_reduce_available = heat_reduce
        self._heat_maintenance_available = heat_maintenance
        self._force_crash_refill_available = force_crash_refill
        self._weapon_repeat_available = weapon_repeat
        if not health_maintenance and self.health_maintenance.isChecked():
            self.health_maintenance.blockSignals(True)
            self.health_maintenance.setChecked(False)
            self.health_maintenance.blockSignals(False)
        if not heat_maintenance and self.heat_maintenance.isChecked():
            self.heat_maintenance.blockSignals(True)
            self.heat_maintenance.setChecked(False)
            self.heat_maintenance.blockSignals(False)
        if not weapon_repeat and self.weapon_repeat.isChecked():
            self.weapon_repeat.blockSignals(True)
            self.weapon_repeat.setChecked(False)
            self.weapon_repeat.blockSignals(False)
        if not hud:
            self.clear_player_state("当前游戏版本不支持实时战斗功能。")
        else:
            self._sync_effect_controls()

    def _sync_effect_controls(self) -> None:
        """只有能力授权与当前玩家快照同时有效时才开放写入控件。"""

        usable = self._has_player_state and not self._write_paused
        self.restore_health_button.setEnabled(usable and self._health_restore_available)
        self.reduce_heat_button.setEnabled(usable and self._heat_reduce_available)
        self.heat_once_target.setEnabled(usable and self._heat_reduce_available)
        self.refill_force_crash_button.setEnabled(
            usable and self._force_crash_refill_available
        )
        self.health_maintenance.setEnabled(usable and self._health_maintenance_available)
        self.health_target.setEnabled(usable and self._health_maintenance_available)
        self.heat_maintenance.setEnabled(usable and self._heat_maintenance_available)
        self.heat_target.setEnabled(usable and self._heat_maintenance_available)
        self.weapon_repeat.setEnabled(usable and self._weapon_repeat_available)
        self.practice_preset_button.setEnabled(
            usable
            and self.hud_status.text().startswith("练习战斗")
            and self._health_maintenance_available
            and self._heat_maintenance_available
            and self._weapon_repeat_available
        )
