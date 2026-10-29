"""无注入的外部置顶战斗面板。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QMouseEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .window_layer import WindowLayerController


class BattleHud(QWidget):
    hidden = Signal()
    effect_toggled = Signal(str, bool)
    target_changed = Signal(str, int)
    quick_preset = Signal(str)

    def __init__(
        self, actions: dict[str, Callable[[], None]], parent: QWidget | None = None
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setWindowTitle("BFE 战斗 HUD")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(360)
        self._layer = WindowLayerController()
        self._layer_timer = QTimer(self)
        self._layer_timer.setInterval(150)
        self._layer_timer.timeout.connect(self._maintain_layer)
        self._drag_offset: QPoint | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        title = QHBoxLayout()
        brand = QLabel("BF // LIVE")
        brand.setStyleSheet("color: #78dcff; font-size: 18px; font-weight: 700;")
        title.addWidget(brand)
        title.addStretch()
        self.compact = QCheckBox("精简")
        title.addWidget(self.compact)
        hide = QPushButton("×")
        hide.setFixedWidth(30)
        hide.clicked.connect(self.close)
        title.addWidget(hide)
        layout.addLayout(title)
        self.status = QLabel("等待连接游戏")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bars: dict[str, QProgressBar] = {}
        for name, label, object_name in (
            ("health", "HP", "health"),
            ("heat", "HEAT", "heat"),
            ("force_crash", "FORCE CRASH", "forceCrash"),
        ):
            bar = QProgressBar()
            bar.setObjectName(object_name)
            bar.setRange(0, 100)
            bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar.setValue(0)
            bar.setFormat(f"{label}  —")
            bar.setMinimumHeight(23)
            layout.addWidget(bar)
            self.bars[name] = bar
        self.effects = QLabel("未启用持续效果")
        self.effects.setWordWrap(True)
        layout.addWidget(self.effects)
        self.controls = QWidget()
        buttons = QVBoxLayout(self.controls)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.action_buttons: dict[str, QPushButton] = {}
        effects_grid = QGridLayout()
        self.effect_checks: dict[str, QCheckBox] = {}
        self.target_controls: dict[str, QSpinBox] = {}
        for row_index, (name, label, target_name, step) in enumerate(
            (
                ("health", "锁血", "health", 100),
                ("heat", "热量阈值", "heat", 5),
                ("weapon_repeat", "同连段武器重复", None, 1),
                ("force_crash", "锁 FC 满槽", None, 1),
            )
        ):
            checkbox = QCheckBox(label)
            checkbox.setEnabled(False)
            checkbox.toggled.connect(
                lambda enabled, effect=name: self.effect_toggled.emit(effect, enabled)
            )
            self.effect_checks[name] = checkbox
            effects_grid.addWidget(checkbox, row_index, 0, 1, 3 if target_name is None else 1)
            if name == "force_crash":
                effects_grid.removeWidget(checkbox)
                effects_grid.addWidget(checkbox, row_index, 0, 1, 2)
                lock_fc = QPushButton("锁满 · F11")
                lock_fc.setToolTip("配置持续满槽；Ctrl+Alt+F11 可切换 FC 锁满")
                lock_fc.clicked.connect(lambda: self.quick_preset.emit("force_crash"))
                self.action_buttons["preset_force_crash"] = lock_fc
                effects_grid.addWidget(lock_fc, row_index, 2)
            if target_name is not None:
                target = self._target_control(target_name, step)
                effects_grid.addWidget(target, row_index, 1)
                preset = QPushButton("满血" if name == "health" else "自然冷却")
                preset.setToolTip(
                    "选择满血方案，进入战斗后跟随实际 HP 上限"
                    if name == "health"
                    else "设为零目标并启用热量阈值，保留原生连段结束流程"
                )
                preset.clicked.connect(
                    lambda _checked=False, effect=name: self.quick_preset.emit(effect)
                )
                self.action_buttons[f"preset_{name}"] = preset
                effects_grid.addWidget(preset, row_index, 2)
        effects_grid.addWidget(QLabel("一次降热目标"), 4, 0)
        effects_grid.addWidget(self._target_control("heat_once", 5), 4, 1, 1, 2)
        buttons.addLayout(effects_grid)
        row = QHBoxLayout()
        for key, label in (("heal", "回血 · F6"), ("cool", "降热 · F7"), ("fc", "FC · F8")):
            button = QPushButton(label)
            button.clicked.connect(actions[key])
            button.setEnabled(False)
            self.action_buttons[key] = button
            row.addWidget(button)
        buttons.addLayout(row)
        row = QHBoxLayout()
        for key, label in (("connect", "连接游戏"), ("load", "上次方案"), ("practice", "练习组合")):
            button = QPushButton(label)
            button.clicked.connect(actions[key])
            self.action_buttons[key] = button
            row.addWidget(button)
        buttons.addLayout(row)
        row = QHBoxLayout()
        for key, label in (("pause", "暂停方案 · F9"), ("stop", "紧急全停 · F12")):
            button = QPushButton(label)
            button.clicked.connect(actions[key])
            if key == "stop":
                button.setObjectName("danger")
            self.action_buttons[key] = button
            row.addWidget(button)
        buttons.addLayout(row)
        hint = QLabel("快捷键均需 Ctrl + Alt；拖动标题移动")
        hint.setObjectName("muted")
        buttons.addWidget(hint)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("透明度"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(40, 100)
        self.opacity.setValue(92)
        self.opacity.valueChanged.connect(lambda value: self.setWindowOpacity(value / 100))
        opacity_row.addWidget(self.opacity)
        buttons.addLayout(opacity_row)
        self.layer_status = QLabel("自动维持置顶 · 不抢游戏焦点")
        self.layer_status.setObjectName("muted")
        self.layer_status.setWordWrap(True)
        buttons.addWidget(self.layer_status)
        self.pin_button = QPushButton("重新置顶")
        self.pin_button.setToolTip("立即重置 HUD 的窗口层级，不切换前台窗口")
        self.pin_button.clicked.connect(lambda: self._maintain_layer(force=True))
        buttons.addWidget(self.pin_button)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.controls_scroll.setWidget(self.controls)
        layout.addWidget(self.controls_scroll)
        self.compact.toggled.connect(self._set_compact)
        for widget in self.findChildren(QWidget):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowOpacity(0.92)

    def _target_control(self, name: str, step: int) -> QSpinBox:
        target = QSpinBox()
        target.setRange(0, 1_000_000)
        target.setSingleStep(step)
        target.setKeyboardTracking(False)
        target.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        target.lineEdit().setReadOnly(True)
        target.setToolTip("用加减按钮或滚轮调整；HUD 不接收键盘输入")
        target.setEnabled(False)
        target.valueChanged.connect(lambda value: self.target_changed.emit(name, value))
        self.target_controls[name] = target
        return target

    def set_game_directory(self, game_root: Path | None) -> None:
        self._layer.set_target_paths(
            tuple(game_root / name for name in ("BaldrForce.exe", "Start-BaldrForce.exe"))
            if game_root is not None
            else ()
        )

    def _maintain_layer(self, *, force: bool = False) -> None:
        if not self.isVisible() or QApplication.platformName() == "offscreen":
            return
        self._layer.maintain(int(self.winId()), force=force)
        self.layer_status.setText(self._layer.last_error or "自动维持置顶 · 不抢游戏焦点")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit_to_screen()
        self._maintain_layer(force=True)
        self._layer_timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self._layer_timer.stop()
        super().hideEvent(event)

    def _set_compact(self, compact: bool) -> None:
        self.controls_scroll.setVisible(not compact)
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        controls_height = min(
            self.controls.sizeHint().height() + 4, max(120, available.height() - 280)
        )
        self.controls_scroll.setFixedHeight(controls_height)
        self.adjustSize()
        self.move(
            max(available.left(), min(self.x(), available.right() - self.width() + 1)),
            max(available.top(), min(self.y(), available.bottom() - self.height() + 1)),
        )

    def sync(self, page: Any, plan_status: str) -> None:
        self.status.setText(page.hud_status.text())
        for name, value, source in (
            ("health", page.health_value, page.health_bar),
            ("heat", page.heat_value, page.heat_bar),
            ("force_crash", page.force_crash_value, page.force_crash_bar),
        ):
            bar = self.bars[name]
            bar.setValue(source.value())
            label = {"health": "HP", "heat": "HEAT", "force_crash": "FC"}[name]
            bar.setFormat(f"{label}  {value.text()}")
        for key, source in (
            ("heal", page.restore_health_button),
            ("cool", page.reduce_heat_button),
            ("fc", page.refill_force_crash_button),
            ("connect", page.connect_button),
            ("load", page.load_plan_button),
            ("practice", page.practice_preset_button),
            ("pause", page.pause_plan_button),
            ("preset_health", page.health_target),
            ("preset_heat", page.heat_target),
            ("preset_force_crash", page.force_crash_maintenance),
        ):
            self.action_buttons[key].setEnabled(source.isEnabled())
        for name, source in (
            ("health", page.health_maintenance),
            ("heat", page.heat_maintenance),
            ("weapon_repeat", page.weapon_repeat),
            ("force_crash", page.force_crash_maintenance),
        ):
            checkbox = self.effect_checks[name]
            with QSignalBlocker(checkbox):
                checkbox.setChecked(source.isChecked())
                checkbox.setEnabled(source.isEnabled())
                checkbox.setToolTip(source.toolTip())
        for name, source in (
            ("health", page.health_target),
            ("heat", page.heat_target),
            ("heat_once", page.heat_once_target),
        ):
            target = self.target_controls[name]
            with QSignalBlocker(target):
                target.setRange(source.minimum(), source.maximum())
                target.setSpecialValueText(source.specialValueText())
                target.setValue(source.value())
                target.setEnabled(source.isEnabled())
        self.effects.setText(plan_status)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        self.hidden.emit()
        event.accept()
