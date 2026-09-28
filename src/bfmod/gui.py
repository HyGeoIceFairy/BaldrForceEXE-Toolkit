"""BALDR FORCE EXE 修改器的简体中文 PySide6 桌面界面。"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QItemSelectionModel,
    QLockFile,
    QObject,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .application import (
    apply_force_crash_session,
    apply_session,
    create_manual_backup,
    restore_slot,
)
from .backups import BackupInfo, list_backup_files, list_backups, verify_backup
from .capabilities import (
    REALTIME_FORCE_CRASH_REFILL,
    REALTIME_HEALTH_MAINTENANCE,
    REALTIME_HEALTH_RESTORE,
    REALTIME_HEAT_MAINTENANCE,
    REALTIME_HEAT_REDUCE,
    REALTIME_PLAYER_HUD,
    REALTIME_WEAPON_REPEAT,
    REALTIME_WEAPON_STATE,
    build_capability_matrix,
    capability_by_id,
)
from .editing import EditorSession, WeaponChange
from .force_crash import ForceCrashChange, ForceCrashSession
from .force_crash_translations import FORCE_CRASH_NAMES_ZH
from .gui_pages import ForceCrashPage, RealtimePage
from .installation import DEFAULT_GAME_ROOT_CANDIDATES, resolve_game_root
from .process_memory import WindowsProcessMemoryAdapter
from .realtime import (
    PlayerState,
    RealtimeEffectError,
    RealtimeFrameUnstableError,
    RealtimeMonitor,
    RealtimePlayerUnavailableError,
    RealtimeSnapshot,
    RealtimeWeaponState,
)
from .realtime_profiles import select_realtime_profile
from .snapshot import sha256_file
from .weapon_progress import WeaponProgressRecord
from .workspace import GameWorkspace, LoadedSlot, WeaponCatalogEntry, load_workspace

APP_NAME = "BALDR FORCE EXE 修改器"
ORGANIZATION_NAME = "BaldrForceModifier"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP_DATA = Path(os.environ.get("LOCALAPPDATA", PROJECT_ROOT / "work")) / ORGANIZATION_NAME
REALTIME_IDLE_POLL_INTERVAL_MS = 1000
REALTIME_EFFECT_POLL_INTERVAL_MS = 100
REALTIME_WEAPON_REPEAT_POLL_INTERVAL_MS = 20


STYLE_SHEET = """
QWidget { color: #dce8f2; background: #111820; font-size: 14px; }
QMainWindow, QDialog { background: #0c1219; }
QFrame#sidebar { background: #101923; border-right: 1px solid #263746; }
QLabel#brand { color: #78dcff; font-size: 21px; font-weight: 700; padding: 18px 10px; }
QLabel#heading { color: #f4f9fc; font-size: 22px; font-weight: 650; }
QLabel#muted { color: #8295a6; }
QLabel#good { color: #67e8aa; font-weight: 600; }
QLabel#warning { color: #ffbf69; font-weight: 600; }
QListWidget#navigation { border: none; background: transparent; outline: none; }
QListWidget#navigation::item { padding: 12px 14px; margin: 2px 7px; border-radius: 7px; }
QListWidget#navigation::item:selected { background: #173b4d; color: #86e5ff; }
QGroupBox {
    border: 1px solid #293a49;
    border-radius: 9px;
    margin-top: 12px;
    padding: 14px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #85dfff; }
QPushButton {
    background: #223343;
    border: 1px solid #375064;
    border-radius: 6px;
    padding: 7px 13px;
}
QPushButton:hover { background: #2b4355; border-color: #61c9ed; }
QPushButton:pressed { background: #172c3b; }
QPushButton:disabled { color: #566875; background: #18232d; border-color: #26343f; }
QPushButton#primary { background: #087ca0; border-color: #38c9ef; color: white; font-weight: 650; }
QPushButton#danger { background: #61333a; border-color: #a95762; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextBrowser {
    background: #0e171f;
    border: 1px solid #344959;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #167da0;
}
QTableWidget {
    background: #0e171f;
    alternate-background-color: #121e28;
    border: 1px solid #293b49;
    gridline-color: #223441;
}
QTableWidget::item { padding: 5px; }
QTableWidget::item:selected { background: #15516a; }
QHeaderView::section {
    background: #172632;
    color: #a9ccdc;
    border: none;
    border-right: 1px solid #2b3f4d;
    padding: 7px;
}
QStatusBar { background: #0b1117; color: #91a5b5; border-top: 1px solid #253643; }
QProgressBar { background: #0b131b; border: 1px solid #314858; border-radius: 5px; }
QProgressBar::chunk { background: #168caf; border-radius: 4px; }
QProgressBar#health::chunk { background: #39b982; }
QProgressBar#heat::chunk { background: #e68a45; }
QProgressBar#forceCrash::chunk { background: #d94b64; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextBrowser:focus,
QTableWidget:focus, QListWidget:focus { border: 1px solid #57ccec; }
QScrollBar:vertical { background: #101820; width: 11px; }
QScrollBar::handle:vertical { background: #355165; min-height: 28px; border-radius: 5px; }
"""


def _app_data_path() -> Path:
    fallback = DEFAULT_APP_DATA / "backups"
    value = str(QSettings().value("backup_root", str(fallback))).strip()
    return Path(value or fallback).absolute()


def build_restore_preview(
    *,
    slot_name: str,
    current_size: int,
    current_sha256: str,
    backup_size: int,
    backup_sha256: str,
) -> str:
    """生成恢复前可审计的当前/备份槽位差异摘要。"""

    identical = current_size == backup_size and current_sha256.upper() == backup_sha256.upper()
    comparison = "完全相同（恢复不会改变槽位内容）" if identical else "不同（恢复将改变槽位内容）"
    return (
        f"将从所选完整备份恢复 {slot_name}。\n\n"
        f"比较结果：{comparison}\n"
        f"当前槽位：{current_size:,} B\nSHA-256：{current_sha256.upper()}\n\n"
        f"备份槽位：{backup_size:,} B\nSHA-256：{backup_sha256.upper()}\n\n"
        f"恢复只覆盖 {slot_name}；当前整个 Save 会先创建新的保护备份，"
        "其他文件不会被删除或覆盖。"
    )


class WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class ChangePreviewDialog(QDialog):
    def __init__(
        self,
        slot: LoadedSlot,
        changes: tuple[WeaponChange, ...],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("检查待应用修改")
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        heading = QLabel(f"将修改 {slot.display_name} · 共 {len(changes)} 把武器")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        note = QLabel("应用前会备份整个 Save 目录。请确认游戏已经退出。")
        note.setObjectName("warning")
        layout.addWidget(note)
        table = QTableWidget(len(changes), 6)
        table.setHorizontalHeaderLabels(("编号", "武器", "开发状态", "等级", "成长值", "热度减免"))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for row, change in enumerate(changes):
            values = (
                f"{change.weapon.official_number:02d}",
                change.weapon.name_ja,
                f"{change.original.development_state} → {change.target.development_state}",
                f"Lv{change.original.level_index + 1} → Lv{change.target.level_index + 1}",
                f"{change.original.level_progress} → {change.target.level_progress}",
                f"{change.original.heat_reduction} → {change.target.heat_reduction}",
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        form = QFormLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText("可选，例如：全武器正常降热前")
        form.addRow("备份备注", self.note)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("备份并应用")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ForceCrashPreviewDialog(QDialog):
    def __init__(
        self,
        slot: LoadedSlot,
        changes: tuple[ForceCrashChange, ...],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("检查 Force Crash 解锁")
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        heading = QLabel(f"将修改 {slot.display_name} · 共 {len(changes)} 项 Force Crash")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        note = QLabel("只把所选成长记录的开发状态改为 5；应用前会备份整个 Save。")
        note.setObjectName("warning")
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(changes), 4)
        table.setHorizontalHeaderLabels(("编号", "Force Crash", "成长记录", "状态"))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for row, change in enumerate(changes):
            values = (
                f"{change.force_crash.number:02d}",
                f"{FORCE_CRASH_NAMES_ZH[change.force_crash.number]} / {change.force_crash.name_ja}",
                str(change.force_crash.record_index),
                f"{change.original_state} → {change.target_state}",
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        form = QFormLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText("可选，例如：解锁全部 Force Crash 前")
        form.addRow("备份备注", self.note)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("备份并应用")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SlotComparisonDialog(QDialog):
    def __init__(self, workspace: GameWorkspace, parent: QWidget) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("比较两个存档的武器成长")
        self.resize(980, 650)
        layout = QVBoxLayout(self)
        selectors = QHBoxLayout()
        self.left = QComboBox()
        self.right = QComboBox()
        for index, slot in enumerate(workspace.slots):
            self.left.addItem(slot.display_name, index)
            self.right.addItem(slot.display_name, index)
        if len(workspace.slots) > 1:
            self.right.setCurrentIndex(1)
        selectors.addWidget(QLabel("基准"))
        selectors.addWidget(self.left, 1)
        selectors.addWidget(QLabel("比较"))
        selectors.addWidget(self.right, 1)
        layout.addLayout(selectors)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("编号", "武器", "开发状态", "等级", "成长值", "开发 EXP", "降热量")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.left.currentIndexChanged.connect(self._refresh)
        self.right.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        left = self.workspace.slots[int(self.left.currentData())]
        right = self.workspace.slots[int(self.right.currentData())]
        differences: list[
            tuple[WeaponCatalogEntry, WeaponProgressRecord, WeaponProgressRecord]
        ] = []
        for entry in self.workspace.catalog:
            left_record = left.progress.records[entry.official_number * 3]
            right_record = right.progress.records[entry.official_number * 3]
            fields_left = (
                left_record.development_state,
                left_record.level_index,
                left_record.level_progress,
                left_record.development_exp,
                left_record.heat_reduction,
            )
            fields_right = (
                right_record.development_state,
                right_record.level_index,
                right_record.level_progress,
                right_record.development_exp,
                right_record.heat_reduction,
            )
            if fields_left != fields_right:
                differences.append((entry, left_record, right_record))
        self.summary.setText(f"发现 {len(differences)} 把武器存在成长差异；只读比较不会修改存档。")
        self.table.setRowCount(len(differences))
        for row, (entry, left_record, right_record) in enumerate(differences):
            values = (
                f"{entry.official_number:02d}",
                f"{entry.name_zh}\n{entry.name_ja}",
                f"{left_record.development_state} → {right_record.development_state}",
                f"Lv{left_record.level_index + 1} → Lv{right_record.level_index + 1}",
                f"{left_record.level_progress} → {right_record.level_progress}",
                f"{left_record.development_exp} → {right_record.development_exp}",
                f"{left_record.heat_reduction} → {right_record.heat_reduction}",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeRowsToContents()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        self.thread_pool = QThreadPool.globalInstance()
        self.workers: set[Worker] = set()
        self.workspace: GameWorkspace | None = None
        self.loaded_slot: LoadedSlot | None = None
        self.session: EditorSession | None = None
        self.force_crash_session: ForceCrashSession | None = None
        self.realtime_monitor: RealtimeMonitor | None = None
        self._realtime_connection_requested = False
        self._realtime_events: list[str] = []
        self.realtime_poll_timer = QTimer(self)
        self.realtime_poll_timer.setInterval(REALTIME_IDLE_POLL_INTERVAL_MS)
        self.realtime_poll_timer.timeout.connect(self._poll_realtime_monitor)
        self.realtime_reconnect_timer = QTimer(self)
        self.realtime_reconnect_timer.setInterval(5000)
        self.realtime_reconnect_timer.setSingleShot(True)
        self.realtime_reconnect_timer.timeout.connect(self._connect_realtime_monitor)
        self.favorites = self._load_favorites()
        self.weapon_table: QTableWidget
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 820)
        self.setMinimumSize(1050, 680)
        self._build_ui()
        self._build_shortcuts()
        self._restore_geometry()
        self._load_initial_workspace()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        brand = QLabel("BF//MODIFIER")
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.addItems(
            (
                "概览",
                "武器工作台",
                "Force Crash",
                "实时战斗",
                "备份与恢复",
                "诊断",
                "设置",
            )
        )
        self.navigation.currentRowChanged.connect(self._navigate)
        side_layout.addWidget(self.navigation)
        side_layout.addStretch()
        version = QLabel(f"v{__version__}")
        version.setObjectName("muted")
        version.setContentsMargins(14, 8, 8, 15)
        side_layout.addWidget(version)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._overview_page())
        self.pages.addWidget(self._weapons_page())
        self.force_crash_page = self._force_crash_page()
        self.realtime_page = self._realtime_page()
        self.pages.addWidget(self.force_crash_page)
        self.pages.addWidget(self.realtime_page)
        self.pages.addWidget(self._backups_page())
        self.pages.addWidget(self._diagnostics_page())
        self.pages.addWidget(self._settings_page())
        root.addWidget(sidebar)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        status_bar = QStatusBar()
        self.busy_indicator = QProgressBar()
        self.busy_indicator.setRange(0, 0)
        self.busy_indicator.setFixedSize(110, 12)
        self.busy_indicator.setTextVisible(False)
        self.busy_indicator.hide()
        status_bar.addPermanentWidget(self.busy_indicator)
        self.setStatusBar(status_bar)
        self.navigation.setCurrentRow(0)

    def _overview_page(self) -> QWidget:
        page, layout = self._page("概览", "先确认安装与版本，再进入武器工作台。")
        grid = QGridLayout()
        self.installation_value = QLabel("正在检查…")
        self.installation_value.setWordWrap(True)
        self.version_value = QLabel("—")
        self.version_value.setWordWrap(True)
        self.slot_count_value = QLabel("—")
        self.backup_value = QLabel(str(_app_data_path()))
        self.backup_value.setWordWrap(True)
        cards = (
            ("游戏安装", self.installation_value),
            ("游戏版本", self.version_value),
            ("可识别槽位", self.slot_count_value),
            ("备份目录", self.backup_value),
        )
        for index, (title, value) in enumerate(cards):
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)
            value.setFont(QFont(value.font().family(), 12, QFont.Weight.DemiBold))
            box_layout.addWidget(value)
            grid.addWidget(box, index // 2, index % 2)
        layout.addLayout(grid)
        safety = QGroupBox("安全状态")
        safety_layout = QVBoxLayout(safety)
        self.safety_text = QLabel("尚未载入游戏。")
        self.safety_text.setWordWrap(True)
        safety_layout.addWidget(self.safety_text)
        actions = QHBoxLayout()
        open_workspace = QPushButton("重新选择游戏目录")
        open_workspace.clicked.connect(self._choose_game_root)
        go_weapons = QPushButton("打开武器工作台")
        go_weapons.setObjectName("primary")
        go_weapons.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        actions.addWidget(open_workspace)
        actions.addWidget(go_weapons)
        actions.addStretch()
        safety_layout.addLayout(actions)
        layout.addWidget(safety)
        layout.addStretch()
        return page

    def _weapons_page(self) -> QWidget:
        page, layout = self._page(
            "武器工作台",
            "修改先进入草稿；支持 Ctrl/Shift 多选，批量操作只影响当前存档槽位。",
        )
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("存档"))
        self.slot_combo = QComboBox()
        self.slot_combo.setMinimumWidth(230)
        self.slot_combo.currentIndexChanged.connect(self._slot_changed)
        toolbar.addWidget(self.slot_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索中文名、日文名、说明或编号…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh_weapon_table)
        toolbar.addWidget(self.search, 1)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(
            (
                "全部武器",
                "已开发",
                "未开发",
                "可开发",
                "未满级",
                "热度未到最低",
                "已收藏",
                "有待应用修改",
            )
        )
        self.filter_combo.currentIndexChanged.connect(self._refresh_weapon_table)
        toolbar.addWidget(self.filter_combo)
        compare = QPushButton("比较存档")
        compare.clicked.connect(self._compare_slots)
        toolbar.addWidget(compare)
        layout.addLayout(toolbar)

        selection_toolbar = QHBoxLayout()
        self.selection_label = QLabel("已选 0 / 当前 0")
        self.selection_label.setObjectName("muted")
        selection_toolbar.addWidget(self.selection_label)
        selection_toolbar.addStretch()
        select_visible = QPushButton("全选当前结果")
        select_visible.setToolTip("选择搜索和筛选后显示的全部武器；表格聚焦时也可按 Ctrl+A")
        select_visible.clicked.connect(self._select_visible_weapons)
        selection_toolbar.addWidget(select_visible)
        invert_visible = QPushButton("反选当前结果")
        invert_visible.clicked.connect(self._invert_visible_selection)
        selection_toolbar.addWidget(invert_visible)
        select_changed = QPushButton("选择草稿项")
        select_changed.clicked.connect(self._select_changed_weapons)
        selection_toolbar.addWidget(select_changed)
        toggle_favorites = QPushButton("收藏/取消收藏")
        toggle_favorites.clicked.connect(self._toggle_selected_favorites)
        selection_toolbar.addWidget(toggle_favorites)
        clear_selection = QPushButton("取消选择")
        clear_selection.clicked.connect(lambda: self.weapon_table.clearSelection())
        selection_toolbar.addWidget(clear_selection)
        layout.addLayout(selection_toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.weapon_table = QTableWidget(0, 8)
        self.weapon_table.setHorizontalHeaderLabels(
            ("★", "编号", "武器", "状态", "等级", "成长", "当前热度", "草稿")
        )
        self.weapon_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.weapon_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.weapon_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.weapon_table.setAlternatingRowColors(True)
        self.weapon_table.verticalHeader().setVisible(False)
        self.weapon_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.weapon_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.weapon_table.itemSelectionChanged.connect(self._weapon_selection_changed)
        self.weapon_table.cellDoubleClicked.connect(self._toggle_favorite_at_row)
        splitter.addWidget(self.weapon_table)

        detail = QWidget()
        detail.setMinimumWidth(345)
        detail_layout = QVBoxLayout(detail)
        self.detail_title = QLabel("选择一把或多把武器")
        self.detail_title.setObjectName("heading")
        self.detail_title.setWordWrap(True)
        self.detail_original = QLabel("—")
        self.detail_original.setObjectName("muted")
        self.detail_original.setWordWrap(True)
        self.detail_description = QTextBrowser()
        self.detail_description.setMaximumHeight(190)
        self.detail_requirements = QTextBrowser()
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_original)
        detail_layout.addWidget(QLabel("武器说明"))
        detail_layout.addWidget(self.detail_description)
        detail_layout.addWidget(QLabel("开发条件与当前差额"))
        detail_layout.addWidget(self.detail_requirements, 1)

        operations = QGroupBox("对选中武器加入草稿")
        operations_layout = QGridLayout(operations)
        unlock = QPushButton("标记开发完成")
        unlock.clicked.connect(self._stage_unlock)
        master = QPushButton("熟练度练满")
        master.clicked.connect(self._stage_master)
        minimum_heat = QPushButton("降至正常最低热度")
        minimum_heat.clicked.connect(self._stage_minimum_heat)
        complete = QPushButton("一键完成正常养成")
        complete.setObjectName("primary")
        complete.clicked.connect(self._stage_normal_completion)
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 3)
        self.level_spin.setValue(3)
        set_level = QPushButton("设置等级")
        set_level.clicked.connect(self._stage_level)
        discard_selected = QPushButton("移除选中项草稿")
        discard_selected.clicked.connect(self._discard_selected_changes)
        operations_layout.addWidget(unlock, 0, 0)
        operations_layout.addWidget(master, 0, 1)
        operations_layout.addWidget(minimum_heat, 1, 0, 1, 2)
        operations_layout.addWidget(complete, 2, 0, 1, 2)
        operations_layout.addWidget(self.level_spin, 3, 0)
        operations_layout.addWidget(set_level, 3, 1)
        operations_layout.addWidget(discard_selected, 4, 0, 1, 2)
        detail_layout.addWidget(operations)
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
        self.undo_button.clicked.connect(self._undo)
        self.redo_button = QPushButton("重做")
        self.redo_button.clicked.connect(self._redo)
        discard = QPushButton("放弃全部")
        discard.clicked.connect(self._discard_all)
        import_preset = QPushButton("导入预设")
        import_preset.clicked.connect(self._import_preset)
        export_preset = QPushButton("导出预设")
        export_preset.clicked.connect(self._export_preset)
        self.apply_button = QPushButton("检查并应用")
        self.apply_button.setObjectName("primary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_changes)
        for button in (
            self.undo_button,
            self.redo_button,
            discard,
            import_preset,
            export_preset,
            self.apply_button,
        ):
            pending_layout.addWidget(button)
        layout.addWidget(pending)
        return page

    def _force_crash_page(self) -> ForceCrashPage:
        page = ForceCrashPage(
            slot_changed=self._force_crash_slot_changed,
            search_changed=self._refresh_force_crash_table,
            filter_changed=self._refresh_force_crash_table,
            selection_changed=self._force_crash_selection_changed,
            stage_unlock=self._stage_force_crash_unlock,
            discard_selected=self._discard_selected_force_crash_changes,
            undo=self._undo_force_crash,
            redo=self._redo_force_crash,
            discard_all=self._discard_all_force_crash,
            import_preset=self._import_force_crash_preset,
            export_preset=self._export_force_crash_preset,
            apply_changes=self._apply_force_crash_changes,
        )
        self.force_crash_slot_combo = page.slot_combo
        self.force_crash_search = page.search
        self.force_crash_filter = page.filter
        self.force_crash_table = page.table
        self.force_crash_detail_title = page.detail_title
        self.force_crash_detail_state = page.detail_state
        self.force_crash_detail_description = page.detail_description
        self.force_crash_pending_label = page.pending_label
        self.force_crash_undo_button = page.undo_button
        self.force_crash_redo_button = page.redo_button
        self.force_crash_apply_button = page.apply_button
        return page

    def _realtime_page(self) -> RealtimePage:
        page = RealtimePage(
            connect_monitor=self._connect_realtime_monitor,
            stop_monitor=self._stop_realtime_monitor,
            restore_health=self._restore_realtime_health,
            reduce_heat=self._reduce_realtime_heat,
            refill_force_crash=self._refill_realtime_force_crash,
            health_maintenance_changed=self._health_maintenance_changed,
            heat_maintenance_changed=self._heat_maintenance_changed,
            weapon_repeat_changed=self._weapon_repeat_changed,
            health_target_changed=self._health_target_changed,
            heat_target_changed=self._heat_target_changed,
            clear_events=self._clear_realtime_events,
        )
        self.realtime_status = page.status
        self.realtime_connect_button = page.connect_button
        self.realtime_stop_button = page.stop_button
        self.realtime_capabilities = page.capabilities
        self.realtime_auto_reconnect = page.auto_reconnect
        self.realtime_last_stop_reason = page.last_stop_reason
        self.realtime_event_log = page.event_log
        self.realtime_health_value = page.health_value
        self.realtime_health_bar = page.health_bar
        self.realtime_heat_value = page.heat_value
        self.realtime_heat_bar = page.heat_bar
        self.realtime_force_crash_value = page.force_crash_value
        self.realtime_force_crash_bar = page.force_crash_bar
        self.realtime_restore_health_button = page.restore_health_button
        self.realtime_reduce_heat_button = page.reduce_heat_button
        self.realtime_refill_force_crash_button = page.refill_force_crash_button
        self.realtime_effect_status = page.effect_status
        self.realtime_auto_reconnect.toggled.connect(self._auto_reconnect_changed)
        page.practice_preset_button.clicked.connect(self._enable_practice_preset)
        return page

    def _enable_practice_preset(self) -> None:
        """仅为当前玩家对象启用经过逐项门禁的组合；任何失败都不留下半套效果。"""

        monitor = self.realtime_monitor
        if monitor is None:
            return
        try:
            state = monitor.enable_practice_effects()
        except RealtimeEffectError as error:
            self.realtime_page.sync_effect_controls(monitor.snapshot.active_effects)
            self.realtime_page.show_active_effects(monitor.snapshot.active_effects)
            self._sync_realtime_poll_interval(monitor.snapshot)
            self._show_realtime_action_error("一键练习", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self.realtime_page.health_maintenance.blockSignals(True)
        self.realtime_page.heat_maintenance.blockSignals(True)
        self.realtime_page.weapon_repeat.blockSignals(True)
        self.realtime_page.health_target.blockSignals(True)
        self.realtime_page.heat_target.blockSignals(True)
        try:
            self.realtime_page.health_target.setValue(state.max_health)
            self.realtime_page.heat_target.setValue(0)
        finally:
            self.realtime_page.health_target.blockSignals(False)
            self.realtime_page.heat_target.blockSignals(False)
        self.realtime_page.health_maintenance.setChecked(True)
        self.realtime_page.heat_maintenance.setChecked(True)
        self.realtime_page.weapon_repeat.setChecked(True)
        self.realtime_page.health_maintenance.blockSignals(False)
        self.realtime_page.heat_maintenance.blockSignals(False)
        self.realtime_page.weapon_repeat.blockSignals(False)
        self.realtime_page.show_active_effects(monitor.snapshot.active_effects)
        self._sync_realtime_poll_interval(monitor.snapshot)
        self._append_realtime_event("练习组合已启用：锁血、自然冷却与普通武器重复")

    def _connect_realtime_monitor(self) -> None:
        self._realtime_connection_requested = True
        self.realtime_reconnect_timer.stop()
        self.realtime_page.clear_player_state("正在连接游戏…")
        self.realtime_page.reset_effect_controls()
        if self.workspace is None:
            self._realtime_connection_requested = False
            self._show_error("请先选择游戏目录")
            return
        self.realtime_poll_timer.stop()
        if self.realtime_monitor is not None:
            self.realtime_monitor.disconnect("正在重新连接")
            self.realtime_monitor = None
        selected_profile = select_realtime_profile(
            self.workspace.identity,
            self.workspace.game_root,
        )
        if selected_profile is None:
            self._realtime_connection_requested = False
            self.realtime_status.setText("当前游戏版本不支持实时战斗功能。")
            self._record_realtime_stop("当前游戏版本不受支持")
            self._refresh_realtime_capabilities()
            return
        self._refresh_realtime_capabilities()
        self._append_realtime_event("正在连接游戏")
        monitor: RealtimeMonitor | None = None
        try:
            monitor = RealtimeMonitor(
                WindowsProcessMemoryAdapter(),
                selected_profile.profile,
            )
            snapshot = monitor.connect()
        except Exception as error:
            if monitor is not None:
                monitor.disconnect("连接失败")
            self.realtime_monitor = None
            message = f"连接失败：{error}"
            self.realtime_status.setText(message)
            self._record_realtime_stop(message)
            self._schedule_realtime_reconnect()
            return
        self.realtime_monitor = monitor
        self._sync_realtime_poll_interval(snapshot)
        self.realtime_poll_timer.start()
        assert snapshot.instance is not None
        self.realtime_status.setText(
            f"已连接游戏 · PID {snapshot.instance.pid}\n"
            "进入普通战斗或练习模式后自动显示战斗数据。"
        )
        self._append_realtime_event(f"已连接游戏：PID {snapshot.instance.pid}")

    def _poll_realtime_monitor(self) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            self.realtime_poll_timer.stop()
            return
        snapshot = monitor.tick_effects()
        if snapshot.state == "disconnected":
            self._synchronize_realtime_disconnect(monitor, snapshot)
            return
        self._sync_realtime_poll_interval(snapshot)
        assert snapshot.instance is not None
        self.realtime_status.setText(
            f"已连接游戏 · PID {snapshot.instance.pid}\n{snapshot.message}"
        )
        self.realtime_page.show_active_effects(snapshot.active_effects)
        self.realtime_page.sync_effect_controls(snapshot.active_effects)
        if not self._realtime_hud_available():
            self.realtime_page.clear_player_state(
                "当前游戏版本不支持战斗数据。"
            )
            return
        try:
            self._show_realtime_player_state(monitor.read_player_state())
            if self._realtime_weapon_state_available():
                try:
                    self._show_realtime_weapon_state(monitor.read_weapon_state())
                except RealtimeFrameUnstableError:
                    self.realtime_page.clear_weapon_state()
            else:
                self.realtime_page.clear_weapon_state()
        except RealtimeFrameUnstableError as error:
            self.realtime_page.clear_player_state(f"战斗数据更新中，等待下一帧：{error}")
        except RealtimePlayerUnavailableError:
            self.realtime_page.clear_player_state("等待进入普通战斗或练习模式…")
            self.realtime_page.reset_effect_controls()
            self._sync_realtime_poll_interval(monitor.snapshot)
            self.realtime_status.setText(
                f"已连接游戏 · PID {snapshot.instance.pid}\n等待进入战斗。"
            )
        except RealtimeEffectError as error:
            self.realtime_page.clear_player_state(f"暂时无法读取战斗数据：{error}")
            if monitor.snapshot.state == "disconnected":
                self._synchronize_realtime_disconnect(monitor)

    def _restore_realtime_health(self) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            self._show_realtime_action_error("一次回血", "实时会话尚未连接")
            return
        try:
            state = monitor.restore_health_once()
        except RealtimeEffectError as error:
            self._show_realtime_action_error("一次回血", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self._show_realtime_player_state(state)
        self._append_realtime_event(f"一次回血完成：HP {state.health}/{state.max_health}")

    def _reduce_realtime_heat(self) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            self._show_realtime_action_error("一次降热", "实时会话尚未连接")
            return
        target = self.realtime_page.heat_once_target.value()
        try:
            state = monitor.reduce_heat_once(target)
        except RealtimeEffectError as error:
            self._show_realtime_action_error("一次降热", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self._show_realtime_player_state(state)
        self._append_realtime_event(f"一次降热完成：热量 {state.heat}/{state.max_heat}")

    def _refill_realtime_force_crash(self) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            self._show_realtime_action_error("Force Crash 补充", "实时会话尚未连接")
            return
        try:
            state = monitor.refill_force_crash_once()
        except RealtimeEffectError as error:
            self._show_realtime_action_error("Force Crash 补充", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self._show_realtime_player_state(state)
        self._append_realtime_event(
            f"Force Crash 补充完成：{state.force_crash}/{state.max_force_crash}"
        )

    def _health_maintenance_changed(self, enabled: bool) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            if enabled:
                self.realtime_page.health_maintenance.setChecked(False)
            return
        try:
            if enabled:
                monitor.enable_health_maintenance(self.realtime_page.health_target.value())
            else:
                monitor.stop_health_maintenance()
        except RealtimeEffectError as error:
            self.realtime_page.health_maintenance.blockSignals(True)
            self.realtime_page.health_maintenance.setChecked(False)
            self.realtime_page.health_maintenance.blockSignals(False)
            self._sync_realtime_poll_interval(monitor.snapshot)
            self._show_realtime_action_error("血量维持", str(error))
            return
        self.realtime_page.show_active_effects(monitor.snapshot.active_effects)
        self._sync_realtime_poll_interval(monitor.snapshot)
        self._append_realtime_event("血量维持已启用" if enabled else "血量维持已停用")

    def _health_target_changed(self, target: int) -> None:
        if not self.realtime_page.health_maintenance.isChecked():
            return
        monitor = self.realtime_monitor
        if monitor is None:
            return
        try:
            monitor.enable_health_maintenance(target)
        except RealtimeEffectError as error:
            try:
                monitor.stop_health_maintenance()
            except RealtimeEffectError:
                pass
            self.realtime_page.health_maintenance.blockSignals(True)
            self.realtime_page.health_maintenance.setChecked(False)
            self.realtime_page.health_maintenance.blockSignals(False)
            self._sync_realtime_poll_interval(monitor.snapshot)
            self._show_realtime_action_error("更新血量维持目标", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self._append_realtime_event(f"血量维持目标已更新为 {target}")
        self._sync_realtime_poll_interval(monitor.snapshot)

    def _heat_maintenance_changed(self, enabled: bool) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            if enabled:
                self.realtime_page.heat_maintenance.setChecked(False)
            return
        try:
            if enabled:
                monitor.enable_heat_maintenance(self.realtime_page.heat_target.value())
            else:
                monitor.stop_heat_maintenance()
        except RealtimeEffectError as error:
            self.realtime_page.heat_maintenance.blockSignals(True)
            self.realtime_page.heat_maintenance.setChecked(False)
            self.realtime_page.heat_maintenance.blockSignals(False)
            self._sync_realtime_poll_interval(monitor.snapshot)
            self._show_realtime_action_error("热量阈值", str(error))
            return
        self.realtime_page.show_active_effects(monitor.snapshot.active_effects)
        self._sync_realtime_poll_interval(monitor.snapshot)
        self._append_realtime_event(
            "热量阈值 / 武器恢复已启用" if enabled else "热量阈值 / 武器恢复已停用"
        )

    def _heat_target_changed(self, target: int) -> None:
        if not self.realtime_page.heat_maintenance.isChecked():
            return
        monitor = self.realtime_monitor
        if monitor is None:
            return
        try:
            monitor.enable_heat_maintenance(target)
        except RealtimeEffectError as error:
            try:
                monitor.stop_heat_maintenance()
            except RealtimeEffectError:
                pass
            self.realtime_page.heat_maintenance.blockSignals(True)
            self.realtime_page.heat_maintenance.setChecked(False)
            self.realtime_page.heat_maintenance.blockSignals(False)
            self._sync_realtime_poll_interval(monitor.snapshot)
            self._show_realtime_action_error("更新热量阈值", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self._sync_realtime_poll_interval(monitor.snapshot)
        self._append_realtime_event(f"热量阈值 / 武器恢复目标已更新为 {target}")

    def _weapon_repeat_changed(self, enabled: bool) -> None:
        monitor = self.realtime_monitor
        if monitor is None:
            if enabled:
                self.realtime_page.weapon_repeat.setChecked(False)
            return
        try:
            if enabled:
                monitor.enable_weapon_repeat()
            else:
                monitor.stop_weapon_repeat()
        except RealtimeEffectError as error:
            self.realtime_page.weapon_repeat.blockSignals(True)
            self.realtime_page.weapon_repeat.setChecked(False)
            self.realtime_page.weapon_repeat.blockSignals(False)
            self._sync_realtime_poll_interval(monitor.snapshot)
            self._show_realtime_action_error("同连段武器重复", str(error))
            self._synchronize_realtime_disconnect(monitor)
            return
        self.realtime_page.show_active_effects(monitor.snapshot.active_effects)
        self._sync_realtime_poll_interval(monitor.snapshot)
        self._append_realtime_event(
            "同连段武器可重复使用已启用" if enabled else "同连段武器可重复使用已停用"
        )

    def _sync_realtime_poll_interval(self, snapshot: RealtimeSnapshot) -> None:
        if snapshot.state == "connected_read_only" and "weapon_repeat" in snapshot.active_effects:
            interval = REALTIME_WEAPON_REPEAT_POLL_INTERVAL_MS
        elif snapshot.state == "connected_read_only" and snapshot.active_effects:
            interval = REALTIME_EFFECT_POLL_INTERVAL_MS
        else:
            interval = REALTIME_IDLE_POLL_INTERVAL_MS
        if self.realtime_poll_timer.interval() != interval:
            self.realtime_poll_timer.setInterval(interval)

    def _show_realtime_player_state(self, state: PlayerState) -> None:
        self.realtime_page.show_player_state(
            state.health,
            state.max_health,
            state.heat,
            state.max_heat,
            state.battle_state,
            state.force_crash,
            state.max_force_crash,
            state.write_inhibited,
            state.scene_name,
        )

    def _show_realtime_weapon_state(self, state: RealtimeWeaponState) -> None:
        self.realtime_page.show_weapon_state(
            tuple((slot.slot, slot.weapon_id, slot.used_in_combo) for slot in state.slots),
            state.recent_slot,
            state.recent_weapon_id,
            state.combo_active,
            state.history,
        )

    def _show_realtime_action_error(self, action: str, message: str) -> None:
        text = f"{action}失败：{message}"
        self.realtime_status.setText(text)
        self._append_realtime_event(text)

    def _synchronize_realtime_disconnect(
        self,
        monitor: RealtimeMonitor,
        snapshot: RealtimeSnapshot | None = None,
    ) -> None:
        disconnected = snapshot or monitor.snapshot
        if disconnected.state != "disconnected":
            return
        self.realtime_monitor = None
        self.realtime_poll_timer.stop()
        self.realtime_poll_timer.setInterval(REALTIME_IDLE_POLL_INTERVAL_MS)
        self.realtime_status.setText(disconnected.message)
        self._record_realtime_stop(disconnected.message)
        self.realtime_page.clear_player_state(disconnected.message)
        self.realtime_page.reset_effect_controls()
        self._schedule_realtime_reconnect()

    def _realtime_hud_available(self) -> bool:
        if self.workspace is None:
            return False
        selected = select_realtime_profile(
            self.workspace.identity,
            self.workspace.game_root,
        )
        if selected is None or selected.profile.player_state is None:
            return False
        matrix = build_capability_matrix(self.workspace.identity, selected.definition)
        return capability_by_id(matrix, REALTIME_PLAYER_HUD).state != "unavailable"

    def _realtime_weapon_state_available(self) -> bool:
        if self.workspace is None:
            return False
        selected = select_realtime_profile(
            self.workspace.identity,
            self.workspace.game_root,
        )
        if selected is None or selected.profile.weapon_state is None:
            return False
        matrix = build_capability_matrix(self.workspace.identity, selected.definition)
        return capability_by_id(matrix, REALTIME_WEAPON_STATE).state != "unavailable"

    def _stop_realtime_monitor(self) -> None:
        had_monitor = self.realtime_monitor is not None
        reason = (
            "用户执行紧急全停" if had_monitor else "没有活动的实时会话；所有实时写入均为停用状态"
        )
        self._deactivate_realtime_session(reason, record_stop=True)
        event = "自动重连已解除" if had_monitor else "紧急全停：自动重连已解除"
        self._append_realtime_event(event)

    def _auto_reconnect_changed(self, enabled: bool) -> None:
        if enabled:
            self._append_realtime_event("已启用意外断开后的只读自动重连")
            return
        self.realtime_reconnect_timer.stop()
        self._append_realtime_event("已停用自动重连")

    def _schedule_realtime_reconnect(self) -> None:
        if not self._realtime_connection_requested or not self.realtime_auto_reconnect.isChecked():
            return
        self.realtime_reconnect_timer.start()
        self._append_realtime_event("将在 5 秒后尝试只读自动重连")

    def _record_realtime_stop(self, reason: str) -> None:
        self.realtime_last_stop_reason.setText(f"最近停用原因：{reason}")
        self._append_realtime_event(f"实时会话停用：{reason}")

    def _deactivate_realtime_session(self, reason: str, *, record_stop: bool) -> None:
        self._realtime_connection_requested = False
        self.realtime_poll_timer.stop()
        self.realtime_reconnect_timer.stop()
        monitor = self.realtime_monitor
        self.realtime_monitor = None
        if monitor is not None:
            monitor.disconnect(reason)
        self.realtime_page.clear_player_state(reason)
        self.realtime_page.reset_effect_controls()
        if record_stop:
            self.realtime_status.setText(reason)
            self._record_realtime_stop(reason)

    def _append_realtime_event(self, message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._realtime_events.append(f"[{timestamp}] {message}")
        del self._realtime_events[:-200]
        self.realtime_event_log.setPlainText("\n".join(self._realtime_events))
        scrollbar = self.realtime_event_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_realtime_events(self) -> None:
        self._realtime_events.clear()
        self.realtime_event_log.setPlainText("尚无实时事件。")

    def _backups_page(self) -> QWidget:
        page, layout = self._page(
            "备份与恢复",
            "每次应用都会完整备份 Save；恢复只覆盖明确选择的槽位。",
        )
        actions = QHBoxLayout()
        manual = QPushButton("创建手动完整备份")
        manual.clicked.connect(self._manual_backup)
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self._refresh_backups)
        verify = QPushButton("校验所选备份")
        verify.clicked.connect(self._verify_selected_backup)
        actions.addWidget(manual)
        actions.addWidget(refresh)
        actions.addWidget(verify)
        actions.addStretch()
        layout.addLayout(actions)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.backup_list = QListWidget()
        self.backup_list.currentRowChanged.connect(self._backup_selected)
        splitter.addWidget(self.backup_list)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        self.backup_details = QPlainTextEdit()
        self.backup_details.setReadOnly(True)
        panel_layout.addWidget(self.backup_details, 1)
        restore_line = QHBoxLayout()
        restore_line.addWidget(QLabel("恢复到"))
        self.restore_slot_combo = QComboBox()
        restore_line.addWidget(self.restore_slot_combo, 1)
        restore_button = QPushButton("预览并恢复该槽位")
        restore_button.setObjectName("danger")
        restore_button.clicked.connect(self._restore_backup)
        restore_line.addWidget(restore_button)
        panel_layout.addLayout(restore_line)
        splitter.addWidget(panel)
        splitter.setSizes((430, 760))
        layout.addWidget(splitter, 1)
        return page

    def _diagnostics_page(self) -> QWidget:
        page, layout = self._page("诊断", "报告只包含结构、版本指纹和文件名，不导出存档内容。")
        buttons = QHBoxLayout()
        refresh = QPushButton("生成当前诊断")
        refresh.clicked.connect(self._refresh_diagnostics)
        export = QPushButton("导出脱敏报告")
        export.clicked.connect(self._export_diagnostics)
        buttons.addWidget(refresh)
        buttons.addWidget(export)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.diagnostics_text = QPlainTextEdit()
        self.diagnostics_text.setReadOnly(True)
        layout.addWidget(self.diagnostics_text, 1)
        return page

    def _settings_page(self) -> QWidget:
        page, layout = self._page("设置", "路径支持空格和中文；游戏资产与私人存档不会打包进工具。")
        form_box = QGroupBox("路径")
        form = QFormLayout(form_box)
        game_row = QHBoxLayout()
        self.game_root_edit = QLineEdit()
        game_browse = QPushButton("浏览…")
        game_browse.clicked.connect(self._choose_game_root)
        game_row.addWidget(self.game_root_edit, 1)
        game_row.addWidget(game_browse)
        form.addRow("游戏目录", game_row)
        backup_row = QHBoxLayout()
        self.backup_root_edit = QLineEdit(str(_app_data_path()))
        backup_browse = QPushButton("浏览…")
        backup_browse.clicked.connect(self._choose_backup_root)
        backup_row.addWidget(self.backup_root_edit, 1)
        backup_row.addWidget(backup_browse)
        form.addRow("备份目录", backup_row)
        save = QPushButton("保存设置并重新载入")
        save.setObjectName("primary")
        save.clicked.connect(self._save_settings)
        form.addRow("", save)
        layout.addWidget(form_box)
        limits = QGroupBox("当前安全边界")
        limits_layout = QVBoxLayout(limits)
        limits_layout.addWidget(
            QLabel(
                "• 只编辑普通 SaveNNN.dat 中 63 把武器与 21 项 Force Crash 的已证明字段。\n"
                "• Save998 / Save999 等特殊槽位只读；不修改 Config、剧情、CG 或资源包。\n"
                "• 正常最低热度遵循游戏公式，不提供零热或负热。\n"
                "• 实时页会自动识别当前游戏进程和普通／练习战斗状态。\n"
                "• 离开战斗或发动 Force Crash 时自动暂停修改，状态恢复后可继续使用。\n"
                "• Force Crash 不提供持续维持；工具不会模拟按键、激活窗口或控制其他应用。"
            )
        )
        layout.addWidget(limits)
        layout.addStretch()
        return page

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
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
        return page, layout

    def _build_shortcuts(self) -> None:
        undo = QAction(self)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.triggered.connect(self._undo_current_page)
        self.addAction(undo)
        redo = QAction(self)
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        redo.triggered.connect(self._redo_current_page)
        self.addAction(redo)
        refresh = QAction(self)
        refresh.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh.triggered.connect(self._reload_workspace)
        self.addAction(refresh)
        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self._focus_current_search)
        self.addAction(find)

        for index in range(self.navigation.count()):
            navigate = QAction(self)
            navigate.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            navigate.triggered.connect(
                lambda _checked=False, page_index=index: self.navigation.setCurrentRow(page_index)
            )
            self.addAction(navigate)

    def _undo_current_page(self) -> None:
        if self.navigation.currentRow() == 2:
            self._undo_force_crash()
        elif self.navigation.currentRow() == 1:
            self._undo()

    def _redo_current_page(self) -> None:
        if self.navigation.currentRow() == 2:
            self._redo_force_crash()
        elif self.navigation.currentRow() == 1:
            self._redo()

    def _focus_current_search(self) -> None:
        if self.navigation.currentRow() == 2:
            search = self.force_crash_search
        else:
            self.navigation.setCurrentRow(1)
            search = self.search
        search.setFocus()
        search.selectAll()

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 4:
            self._refresh_backups()
        elif index == 5:
            self._refresh_diagnostics()

    def _load_initial_workspace(self) -> None:
        configured = str(self.settings.value("game_root", ""))
        if configured:
            self._load_workspace(Path(configured))
            return
        try:
            root = resolve_game_root(None)
        except ValueError:
            self.installation_value.setText("未找到游戏，请选择目录")
            self.safety_text.setText("尚未找到游戏安装。可在概览或设置页选择游戏目录。")
            self.safety_text.setObjectName("warning")
            self.statusBar().showMessage("未找到游戏安装，等待选择目录", 6000)
        else:
            self._load_workspace(root)

    def _load_workspace(self, root: Path) -> None:
        had_realtime_activity = (
            self.realtime_monitor is not None
            or self._realtime_connection_requested
            or self.realtime_poll_timer.isActive()
            or self.realtime_reconnect_timer.isActive()
        )
        self._deactivate_realtime_session(
            "正在切换游戏工作区",
            record_stop=had_realtime_activity,
        )
        self._run_async(
            "正在载入并校验游戏…",
            lambda: load_workspace(root),
            self._workspace_loaded,
        )

    def _workspace_loaded(self, value: object) -> None:
        if not isinstance(value, GameWorkspace):
            self._show_error("载入结果类型无效")
            return
        had_realtime_activity = (
            self.realtime_monitor is not None
            or self._realtime_connection_requested
            or self.realtime_poll_timer.isActive()
            or self.realtime_reconnect_timer.isActive()
        )
        self._deactivate_realtime_session(
            "游戏工作区已切换",
            record_stop=had_realtime_activity,
        )
        self.workspace = value
        self.settings.setValue("game_root", str(value.game_root))
        self.game_root_edit.setText(str(value.game_root))
        self.installation_value.setText(str(value.game_root))
        self.version_value.setText(value.identity.name)
        self.version_value.setObjectName("good" if value.identity.write_supported else "warning")
        self.version_value.style().unpolish(self.version_value)
        self.version_value.style().polish(self.version_value)
        editable_count = sum(slot.is_editable for slot in value.slots)
        failure_suffix = (
            f"，{len(value.slot_failures)} 个损坏或不兼容" if value.slot_failures else ""
        )
        self.slot_count_value.setText(
            f"{len(value.slots)} 个（{editable_count} 个可编辑{failure_suffix}）"
        )
        if value.identity.write_supported:
            self.safety_text.setText("✓ 版本指纹匹配。普通槽位可在完整备份后安全编辑。")
            self.safety_text.setObjectName("good")
        else:
            self.safety_text.setText("只读模式：" + "；".join(value.identity.reasons))
            self.safety_text.setObjectName("warning")
        self._refresh_realtime_capabilities()
        self.slot_combo.blockSignals(True)
        self.slot_combo.clear()
        self.restore_slot_combo.clear()
        force_crash_slots: list[tuple[str, int]] = []
        for index, slot in enumerate(value.slots):
            self.slot_combo.addItem(slot.display_name, index)
            force_crash_slots.append((slot.display_name, index))
            if slot.is_editable:
                self.restore_slot_combo.addItem(slot.display_name, index)
        self.slot_combo.blockSignals(False)
        self.force_crash_page.set_slots(tuple(force_crash_slots))
        if value.slots:
            first_editable = next(
                (index for index, slot in enumerate(value.slots) if slot.is_editable),
                0,
            )
            self._select_slot(first_editable)
        self._refresh_backups()
        self._refresh_diagnostics()
        self.statusBar().showMessage("游戏和存档已载入", 5000)

    def _refresh_realtime_capabilities(self) -> None:
        if self.workspace is None:
            self.realtime_page.show_capabilities(
                "请先选择游戏目录。",
                can_connect=False,
            )
            self.realtime_page.set_effect_capabilities(
                hud=False,
                health_restore=False,
                health_maintenance=False,
                heat_reduce=False,
                heat_maintenance=False,
                force_crash_refill=False,
                weapon_repeat=False,
            )
            return
        selected = select_realtime_profile(
            self.workspace.identity,
            self.workspace.game_root,
        )
        definition = selected.definition if selected is not None else None
        matrix = build_capability_matrix(self.workspace.identity, definition)
        realtime = tuple(capability for capability in matrix if capability.requires_game_running)
        available = tuple(
            capability.display_name
            for capability in realtime
            if capability.state in {"read_only", "verified_write"}
        )
        self.realtime_page.show_capabilities(
            (
                "可用：" + "、".join(available)
                if selected is not None
                else "当前游戏版本不支持实时战斗功能。"
            ),
            can_connect=selected is not None,
        )
        self.realtime_page.set_effect_capabilities(
            hud=capability_by_id(matrix, REALTIME_PLAYER_HUD).state != "unavailable",
            health_restore=capability_by_id(matrix, REALTIME_HEALTH_RESTORE).state
            == "verified_write",
            health_maintenance=capability_by_id(matrix, REALTIME_HEALTH_MAINTENANCE).state
            == "verified_write",
            heat_reduce=capability_by_id(matrix, REALTIME_HEAT_REDUCE).state == "verified_write",
            heat_maintenance=capability_by_id(matrix, REALTIME_HEAT_MAINTENANCE).state
            == "verified_write",
            force_crash_refill=capability_by_id(matrix, REALTIME_FORCE_CRASH_REFILL).state
            == "verified_write",
            weapon_repeat=capability_by_id(matrix, REALTIME_WEAPON_REPEAT).state
            == "verified_write",
        )

    def _reload_workspace(self) -> None:
        if self.workspace is not None and self._confirm_discard_draft("重新载入游戏和存档"):
            self._load_workspace(self.workspace.game_root)

    def _choose_game_root(self) -> None:
        initial = self.game_root_edit.text() or str(DEFAULT_GAME_ROOT_CANDIDATES[0])
        selected = QFileDialog.getExistingDirectory(self, "选择 BALDR FORCE EXE 游戏目录", initial)
        if selected and self._confirm_discard_draft("切换游戏目录"):
            self.game_root_edit.setText(selected)
            self._load_workspace(Path(selected))

    def _choose_backup_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择备份目录", self.backup_root_edit.text()
        )
        if selected:
            self.backup_root_edit.setText(selected)

    def _save_settings(self) -> None:
        if not self._confirm_discard_draft("保存设置并重新载入"):
            return
        game_root = self.game_root_edit.text().strip()
        if not game_root:
            self._show_error("请先选择游戏目录")
            return
        backup_root = self.backup_root_edit.text().strip() or str(DEFAULT_APP_DATA / "backups")
        self.backup_root_edit.setText(backup_root)
        self.game_root_edit.setText(game_root)
        self.settings.setValue("backup_root", backup_root)
        self.settings.setValue("game_root", game_root)
        self.backup_value.setText(backup_root)
        self._load_workspace(Path(game_root))

    def _confirm_discard_draft(self, action: str) -> bool:
        has_weapon_draft = bool(self.session and self.session.changes)
        has_force_crash_draft = bool(self.force_crash_session and self.force_crash_session.changes)
        if not has_weapon_draft and not has_force_crash_draft:
            return True
        answer = QMessageBox.question(
            self,
            "放弃当前草稿？",
            f"{action}会放弃尚未应用的修改。是否继续？",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _slot_changed(self, combo_index: int) -> None:
        if not self._confirm_discard_draft("切换存档"):
            self._restore_slot_combo_selection()
            return
        self._select_slot(combo_index)

    def _force_crash_slot_changed(self, combo_index: int) -> None:
        if not self._confirm_discard_draft("切换存档"):
            self._restore_slot_combo_selection()
            return
        self._select_slot(combo_index)

    def _restore_slot_combo_selection(self) -> None:
        if self.workspace is None or self.loaded_slot is None:
            return
        index = self.workspace.slots.index(self.loaded_slot)
        self.slot_combo.blockSignals(True)
        self.slot_combo.setCurrentIndex(index)
        self.slot_combo.blockSignals(False)
        self.force_crash_page.set_current_slot(index)

    def _select_slot(self, index: int) -> None:
        if self.workspace is None or not 0 <= index < len(self.workspace.slots):
            return
        self.loaded_slot = self.workspace.slots[index]
        self.session = self.workspace.editor_for(self.loaded_slot)
        self.force_crash_session = self.workspace.force_crash_editor_for(self.loaded_slot)
        self.slot_combo.blockSignals(True)
        self.slot_combo.setCurrentIndex(index)
        self.slot_combo.blockSignals(False)
        self.force_crash_page.set_current_slot(index)
        self._refresh_weapon_table()
        self._refresh_pending_state()
        self._refresh_force_crash_table()
        self._refresh_force_crash_pending_state()

    def _refresh_force_crash_table(self) -> None:
        if self.workspace is None or self.loaded_slot is None or self.force_crash_session is None:
            self.force_crash_table.setRowCount(0)
            return
        query = self.force_crash_search.text().strip().casefold()
        mode = self.force_crash_filter.currentText()
        changed = {change.force_crash.number for change in self.force_crash_session.changes}
        selected = set(self._selected_force_crash_numbers())
        entries = []
        for entry in self.workspace.force_crash_catalog:
            state = self.force_crash_session.target_state_for(entry.number)
            haystack = (
                f"{entry.number:02d} {entry.name_zh} {entry.name_ja} "
                f"{entry.description_zh} {entry.description_ja}"
            ).casefold()
            if query and query not in haystack:
                continue
            if mode == "已解锁" and state != 5:
                continue
            if mode == "未解锁" and state == 5:
                continue
            if mode == "有待应用修改" and entry.number not in changed:
                continue
            entries.append(entry)
        self.force_crash_table.setUpdatesEnabled(False)
        self.force_crash_table.blockSignals(True)
        try:
            self.force_crash_table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                state = self.force_crash_session.target_state_for(entry.number)
                values = (
                    f"{entry.number:02d}",
                    f"{entry.name_zh}\n{entry.name_ja}" if entry.name_zh else entry.name_ja,
                    "已解锁" if state == 5 else f"状态 {state}",
                    str(entry.record_index),
                    "待解锁" if entry.number in changed else "",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, entry.number)
                    if entry.number in changed:
                        item.setForeground(QColor("#70e6ff"))
                    self.force_crash_table.setItem(row, column, item)
                if entry.number in selected:
                    self.force_crash_table.selectRow(row)
            self.force_crash_table.resizeRowsToContents()
        finally:
            self.force_crash_table.blockSignals(False)
            self.force_crash_table.setUpdatesEnabled(True)
        self._force_crash_selection_changed()

    def _selected_force_crash_numbers(self) -> tuple[int, ...]:
        rows = sorted(
            {index.row() for index in self.force_crash_table.selectionModel().selectedRows()}
        )
        numbers: list[int] = []
        for row in rows:
            item = self.force_crash_table.item(row, 0)
            if item is not None:
                numbers.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(numbers)

    def _force_crash_selection_changed(self) -> None:
        numbers = self._selected_force_crash_numbers()
        if not numbers or self.workspace is None or self.force_crash_session is None:
            self.force_crash_detail_title.setText("选择一项或多项 Force Crash")
            self.force_crash_detail_state.setText("支持 Ctrl / Shift 多选。")
            self.force_crash_detail_description.setPlainText("—")
            return
        entry = self.workspace.force_crash_catalog[numbers[0]]
        state = self.force_crash_session.target_state_for(entry.number)
        suffix = f" · 另选 {len(numbers) - 1} 项" if len(numbers) > 1 else ""
        self.force_crash_detail_title.setText(
            f"{entry.name_zh or entry.name_ja}{suffix}"
        )
        self.force_crash_detail_state.setText(
            f"{entry.name_ja} · 编号 {entry.number:02d} · 成长记录 {entry.record_index} · "
            + ("已解锁" if state == 5 else f"状态 {state}")
        )
        self.force_crash_detail_description.setPlainText(
            f"游戏资源中的原文：{entry.description_ja or '暂无'}"
        )

    def _refresh_weapon_table(self) -> None:
        if self.workspace is None or self.loaded_slot is None or self.session is None:
            self.weapon_table.setRowCount(0)
            return
        query = self.search.text().strip().casefold()
        mode = self.filter_combo.currentText()
        changed = {change.weapon.official_number for change in self.session.changes}
        selected_numbers = set(self._selected_weapon_numbers())
        entries: list[WeaponCatalogEntry] = []
        for entry in self.workspace.catalog:
            target = self.session.target_for(entry.official_number)
            haystack = (
                f"{entry.official_number:02d} {entry.name_zh} {entry.name_ja} "
                f"{entry.description_zh} {entry.description_ja}"
            ).casefold()
            if query and query not in haystack:
                continue
            if mode == "已开发" and target.development_state != 5:
                continue
            if mode == "未开发" and target.development_state == 5:
                continue
            if mode == "可开发" and target.development_state != 2:
                continue
            if mode == "未满级" and target.level_index == 2:
                continue
            if mode == "热度未到最低" and target.heat_reduction >= entry.base_heat - int(
                entry.base_heat / 3
            ):
                continue
            if mode == "已收藏" and entry.official_number not in self.favorites:
                continue
            if mode == "有待应用修改" and entry.official_number not in changed:
                continue
            entries.append(entry)
        self.weapon_table.setUpdatesEnabled(False)
        self.weapon_table.blockSignals(True)
        try:
            self.weapon_table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                target = self.session.target_for(entry.official_number)
                effective_heat = max(
                    entry.base_heat - target.heat_reduction,
                    int(entry.base_heat / 3),
                )
                values = (
                    "★" if entry.official_number in self.favorites else "☆",
                    f"{entry.official_number:02d}",
                    f"{entry.name_zh}\n{entry.name_ja}",
                    "已开发"
                    if target.development_state == 5
                    else f"状态 {target.development_state}",
                    f"Lv{target.level_index + 1}",
                    "MAX" if target.level_index == 2 else str(target.level_progress),
                    f"{effective_heat} / {entry.base_heat}",
                    "已修改" if entry.official_number in changed else "",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, entry.official_number)
                    if entry.official_number in changed:
                        item.setForeground(QColor("#70e6ff"))
                    self.weapon_table.setItem(row, column, item)
                if entry.official_number in selected_numbers:
                    self.weapon_table.selectRow(row)
            self.weapon_table.resizeRowsToContents()
        finally:
            self.weapon_table.blockSignals(False)
            self.weapon_table.setUpdatesEnabled(True)
        self._weapon_selection_changed()

    def _selected_weapon_numbers(self) -> tuple[int, ...]:
        rows = sorted({index.row() for index in self.weapon_table.selectionModel().selectedRows()})
        numbers: list[int] = []
        for row in rows:
            item = self.weapon_table.item(row, 0)
            if item is not None:
                numbers.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(numbers)

    def _weapon_selection_changed(self) -> None:
        numbers = self._selected_weapon_numbers()
        self._refresh_selection_label()
        if (
            not numbers
            or self.workspace is None
            or self.loaded_slot is None
            or self.session is None
        ):
            self.detail_title.setText("选择一把或多把武器")
            self.detail_original.setText("支持 Ctrl / Shift 多选，也可使用上方批量选择按钮。")
            self.detail_description.setPlainText("—")
            self.detail_requirements.setPlainText("—")
            return
        number = numbers[0]
        entry = self.workspace.catalog[number]
        record = self.loaded_slot.progress.records[number * 3]
        selected_suffix = f" · 另选 {len(numbers) - 1} 把" if len(numbers) > 1 else ""
        self.detail_title.setText(f"{entry.name_zh}{selected_suffix}\n{entry.name_ja}")
        normal_minimum = int(entry.base_heat / 3)
        self.detail_original.setText(
            f"官方编号 {number:02d} · 基础热度 {entry.base_heat} · "
            f"正常最低 {normal_minimum}\n"
            f"当前：状态 {record.development_state} / Lv{record.level_index + 1} / "
            f"成长 {record.reported_level_progress} / 降热 {record.heat_reduction}"
        )
        self.detail_description.setPlainText(
            f"游戏资源中的原文：{entry.description_ja or '暂无'}"
        )
        if not entry.requirements:
            requirements = "初始武器，无开发前置。"
        else:
            lines = []
            for requirement in entry.requirements:
                prerequisite = self.loaded_slot.progress.records[requirement.weapon_number * 3]
                missing = max(0, requirement.game_required_exp - prerequisite.development_exp)
                state = "已满足" if missing == 0 else f"还差 {missing} EXP"
                lines.append(
                    f"• #{requirement.weapon_number:02d} {requirement.name_zh} / "
                    f"{requirement.name_ja}：游戏判定 {requirement.game_required_exp} EXP，"
                    f"当前 {prerequisite.development_exp}（{state}）"
                )
            requirements = "\n".join(lines)
        if any(change.weapon.official_number == number for change in self.session.changes):
            requirements += "\n\n此武器已有草稿修改。"
        self.detail_requirements.setPlainText(requirements)

    def _refresh_selection_label(self) -> None:
        selected = len(self._selected_weapon_numbers())
        visible = self.weapon_table.rowCount()
        self.selection_label.setText(f"已选 {selected} / 当前 {visible}")
        self.selection_label.setObjectName("good" if selected else "muted")
        self.selection_label.style().unpolish(self.selection_label)
        self.selection_label.style().polish(self.selection_label)

    def _select_visible_weapons(self) -> None:
        self.weapon_table.selectAll()

    def _invert_visible_selection(self) -> None:
        selection = self.weapon_table.selectionModel()
        selected_rows = {index.row() for index in selection.selectedRows()}
        self.weapon_table.clearSelection()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        model = self.weapon_table.model()
        for row in range(self.weapon_table.rowCount()):
            if row not in selected_rows:
                selection.select(model.index(row, 0), flags)

    def _select_changed_weapons(self) -> None:
        changed = {
            change.weapon.official_number
            for change in (self.session.changes if self.session is not None else ())
        }
        self.weapon_table.clearSelection()
        selection = self.weapon_table.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        model = self.weapon_table.model()
        for row in range(self.weapon_table.rowCount()):
            item = self.weapon_table.item(row, 0)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole)) in changed:
                selection.select(model.index(row, 0), flags)

    def _toggle_selected_favorites(self) -> None:
        numbers = set(self._selected_weapon_numbers())
        if not numbers:
            self._show_error("请先选择至少一把武器")
            return
        if numbers.issubset(self.favorites):
            self.favorites.difference_update(numbers)
            message = f"已取消收藏 {len(numbers)} 把武器"
        else:
            self.favorites.update(numbers)
            message = f"已收藏 {len(numbers)} 把武器"
        self.settings.setValue("favorites", sorted(self.favorites))
        self._refresh_weapon_table()
        self.statusBar().showMessage(message, 3500)

    def _toggle_favorite_at_row(self, row: int, _column: int) -> None:
        item = self.weapon_table.item(row, 0)
        if item is None:
            return
        number = int(item.data(Qt.ItemDataRole.UserRole))
        if number in self.favorites:
            self.favorites.remove(number)
        else:
            self.favorites.add(number)
        self.settings.setValue("favorites", sorted(self.favorites))
        self._refresh_weapon_table()

    def _stage_unlock(self) -> None:
        self._stage(lambda session, numbers: session.stage_unlock(numbers))

    def _compare_slots(self) -> None:
        if self.workspace is None:
            return
        SlotComparisonDialog(self.workspace, self).exec()

    def _stage_master(self) -> None:
        self._stage(lambda session, numbers: session.stage_master(numbers))

    def _stage_minimum_heat(self) -> None:
        self._stage(lambda session, numbers: session.stage_normal_minimum_heat(numbers))

    def _stage_normal_completion(self) -> None:
        self._stage(lambda session, numbers: session.stage_normal_completion(numbers))

    def _stage_level(self) -> None:
        level_index = self.level_spin.value() - 1
        self._stage(lambda session, numbers: session.stage_level(numbers, level_index=level_index))

    def _stage(self, operation: Callable[[EditorSession, tuple[int, ...]], None]) -> None:
        if self.session is None or self.loaded_slot is None:
            return
        if self.force_crash_session is not None and self.force_crash_session.changes:
            self._show_error("请先应用或放弃 Force Crash 草稿，再修改普通武器")
            return
        if not self.loaded_slot.is_editable:
            self._show_error("特殊槽位在首版中只读")
            return
        numbers = self._selected_weapon_numbers()
        if not numbers:
            self._show_error("请先选择至少一把武器")
            return
        try:
            operation(self.session, numbers)
        except ValueError as error:
            self._show_error(str(error))
            return
        self._refresh_weapon_table()
        self._refresh_pending_state()
        self.statusBar().showMessage(f"已将 {len(numbers)} 把武器加入草稿", 3500)

    def _stage_force_crash_unlock(self) -> None:
        if self.force_crash_session is None or self.loaded_slot is None:
            return
        if self.session is not None and self.session.changes:
            self._show_error("请先应用或放弃普通武器草稿，再修改 Force Crash")
            return
        if not self.loaded_slot.is_editable:
            self._show_error("特殊槽位只读")
            return
        numbers = self._selected_force_crash_numbers()
        if not numbers:
            self._show_error("请先选择至少一项 Force Crash")
            return
        try:
            self.force_crash_session.stage_unlock(numbers)
        except ValueError as error:
            self._show_error(str(error))
            return
        self._refresh_force_crash_table()
        self._refresh_force_crash_pending_state()
        self.statusBar().showMessage(
            f"已将 {len(numbers)} 项 Force Crash 加入解锁草稿",
            3500,
        )

    def _discard_selected_force_crash_changes(self) -> None:
        if self.force_crash_session is None:
            return
        numbers = self._selected_force_crash_numbers()
        if not numbers:
            self._show_error("请先选择至少一项 Force Crash")
            return
        removed = self.force_crash_session.discard_changes(numbers)
        if not removed:
            self.statusBar().showMessage("选中 Force Crash 没有草稿修改", 3500)
            return
        self._refresh_force_crash_table()
        self._refresh_force_crash_pending_state()

    def _undo_force_crash(self) -> None:
        if self.force_crash_session is not None and self.force_crash_session.undo():
            self._refresh_force_crash_table()
            self._refresh_force_crash_pending_state()

    def _redo_force_crash(self) -> None:
        if self.force_crash_session is not None and self.force_crash_session.redo():
            self._refresh_force_crash_table()
            self._refresh_force_crash_pending_state()

    def _discard_all_force_crash(self) -> None:
        if self.force_crash_session is not None and self.force_crash_session.changes:
            self.force_crash_session.discard_all()
            self._refresh_force_crash_table()
            self._refresh_force_crash_pending_state()

    def _refresh_force_crash_pending_state(self) -> None:
        changes = self.force_crash_session.changes if self.force_crash_session is not None else ()
        self.force_crash_pending_label.setText(
            f"待应用：{len(changes)} 项 Force Crash" if changes else "没有待应用修改"
        )
        self.force_crash_pending_label.setObjectName("good" if changes else "muted")
        self.force_crash_undo_button.setEnabled(
            bool(self.force_crash_session and self.force_crash_session.can_undo)
        )
        self.force_crash_redo_button.setEnabled(
            bool(self.force_crash_session and self.force_crash_session.can_redo)
        )
        writable = bool(
            changes
            and self.workspace
            and self.workspace.identity.write_supported
            and self.loaded_slot
            and self.loaded_slot.is_editable
        )
        self.force_crash_apply_button.setEnabled(writable)

    def _export_force_crash_preset(self) -> None:
        if self.force_crash_session is None:
            return
        try:
            text = self.force_crash_session.export_preset(name="Force Crash 解锁预设")
        except ValueError as error:
            self._show_error(str(error))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Force Crash 预设",
            "Force-Crash-预设.json",
            "JSON (*.json)",
        )
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self.statusBar().showMessage("Force Crash 预设已导出", 4000)

    def _import_force_crash_preset(self) -> None:
        if (
            self.force_crash_session is None
            or self.loaded_slot is None
            or not self.loaded_slot.is_editable
        ):
            self._show_error("当前槽位不可编辑")
            return
        if self.session is not None and self.session.changes:
            self._show_error("请先应用或放弃普通武器草稿")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Force Crash 预设",
            "",
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            self.force_crash_session.import_preset(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._show_error(str(error))
            return
        self._refresh_force_crash_table()
        self._refresh_force_crash_pending_state()

    def _apply_force_crash_changes(self) -> None:
        if self.workspace is None or self.loaded_slot is None or self.force_crash_session is None:
            return
        dialog = ForceCrashPreviewDialog(
            self.loaded_slot,
            self.force_crash_session.changes,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        workspace = self.workspace
        slot = self.loaded_slot
        session = self.force_crash_session
        self._run_async(
            "正在创建完整备份并解锁 Force Crash…",
            lambda: apply_force_crash_session(
                workspace,
                slot,
                session,
                backup_root=_app_data_path(),
                note=dialog.note.text(),
            ),
            lambda _result: self._after_force_crash_apply(),
        )

    def _after_force_crash_apply(self) -> None:
        QMessageBox.information(
            self,
            "应用成功",
            "Force Crash 解锁已通过备份、替换与读取校验。",
        )
        self.force_crash_session = None
        self._reload_workspace()

    def _discard_selected_changes(self) -> None:
        if self.session is None:
            return
        numbers = self._selected_weapon_numbers()
        if not numbers:
            self._show_error("请先选择至少一把武器")
            return
        removed = self.session.discard_changes(numbers)
        if not removed:
            self.statusBar().showMessage("选中武器没有草稿修改", 3500)
            return
        self._refresh_weapon_table()
        self._refresh_pending_state()
        self.statusBar().showMessage(f"已从草稿移除 {removed} 把武器", 3500)

    def _undo(self) -> None:
        if self.session is not None and self.session.undo():
            self._refresh_weapon_table()
            self._refresh_pending_state()

    def _redo(self) -> None:
        if self.session is not None and self.session.redo():
            self._refresh_weapon_table()
            self._refresh_pending_state()

    def _discard_all(self) -> None:
        if self.session is not None and self.session.changes:
            self.session.discard_all()
            self._refresh_weapon_table()
            self._refresh_pending_state()

    def _refresh_pending_state(self) -> None:
        changes = self.session.changes if self.session is not None else ()
        self.pending_label.setText(
            f"待应用：{len(changes)} 把武器" if changes else "没有待应用修改"
        )
        self.pending_label.setObjectName("good" if changes else "muted")
        self.undo_button.setEnabled(bool(self.session and self.session.can_undo))
        self.redo_button.setEnabled(bool(self.session and self.session.can_redo))
        writable = bool(
            changes
            and self.workspace
            and self.workspace.identity.write_supported
            and self.loaded_slot
            and self.loaded_slot.is_editable
        )
        self.apply_button.setEnabled(writable)

    def _export_preset(self) -> None:
        if self.session is None:
            return
        try:
            text = self.session.export_preset(name="武器修改预设")
        except ValueError as error:
            self._show_error(str(error))
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出预设", "武器预设.json", "JSON (*.json)")
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self.statusBar().showMessage("预设已导出", 4000)

    def _import_preset(self) -> None:
        if self.session is None or self.loaded_slot is None or not self.loaded_slot.is_editable:
            self._show_error("当前槽位不可编辑")
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入预设", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.session.import_preset(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._show_error(str(error))
            return
        self._refresh_weapon_table()
        self._refresh_pending_state()

    def _apply_changes(self) -> None:
        if self.workspace is None or self.loaded_slot is None or self.session is None:
            return
        dialog = ChangePreviewDialog(self.loaded_slot, self.session.changes, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        workspace, slot, session = self.workspace, self.loaded_slot, self.session
        self._run_async(
            "正在创建完整备份并应用修改…",
            lambda: apply_session(
                workspace,
                slot,
                session,
                backup_root=_app_data_path(),
                note=dialog.note.text(),
            ),
            lambda _result: self._after_apply(),
        )

    def _after_apply(self) -> None:
        QMessageBox.information(self, "应用成功", "存档已通过备份、替换与读取校验。")
        self.session = None
        self._reload_workspace()

    def _manual_backup(self) -> None:
        if self.workspace is None:
            return
        workspace = self.workspace
        self._run_async(
            "正在校验并创建完整备份…",
            lambda: create_manual_backup(
                workspace,
                backup_root=_app_data_path(),
            ),
            lambda _result: self._after_backup(),
        )

    def _after_backup(self) -> None:
        self._refresh_backups()
        QMessageBox.information(self, "备份成功", "完整 Save 备份已通过逐文件哈希校验。")

    def _refresh_backups(self) -> None:
        self.backup_list.clear()
        for backup in list_backups(_app_data_path() / "snapshots"):
            try:
                local_time = (
                    datetime.fromisoformat(backup.created_utc)
                    .astimezone()
                    .strftime("%Y-%m-%d %H:%M:%S")
                )
            except ValueError:
                local_time = backup.created_utc
            item = QListWidgetItem(f"{local_time}\n{backup.operation} · {backup.file_count} 个文件")
            item.setData(Qt.ItemDataRole.UserRole, backup)
            self.backup_list.addItem(item)
        self.backup_details.setPlainText("选择备份查看详情。")

    def _selected_backup(self) -> BackupInfo | None:
        item = self.backup_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, BackupInfo) else None

    def _backup_selected(self, _row: int) -> None:
        backup = self._selected_backup()
        if backup is None:
            return
        try:
            files = list_backup_files(backup.directory)
            file_text = "\n".join(f"  {item.relative_path}  ({item.size} B)" for item in files)
            self.backup_details.setPlainText(
                f"状态：完整性校验通过\n操作：{backup.operation}\n备注：{backup.note or '无'}\n"
                f"来源：{backup.source_save_directory}\n目录：{backup.directory}\n\n文件：\n{file_text}"
            )
        except Exception as error:
            self.backup_details.setPlainText(f"备份校验失败：{error}")

    def _verify_selected_backup(self) -> None:
        backup = self._selected_backup()
        if backup is None:
            self._show_error("请先选择一个备份")
            return
        try:
            verified = verify_backup(backup.directory)
        except Exception as error:
            self._show_error(f"校验失败：{error}")
        else:
            QMessageBox.information(self, "校验通过", f"{verified.file_count} 个文件完整无误。")

    def _restore_backup(self) -> None:
        if self.workspace is None:
            return
        backup = self._selected_backup()
        if backup is None:
            self._show_error("请先选择一个备份")
            return
        source_index = self.restore_slot_combo.currentData()
        if not isinstance(source_index, int):
            self._show_error("请选择要恢复的普通槽位")
            return
        slot = self.workspace.slots[source_index]
        try:
            matching_files = {
                item.relative_path: item for item in list_backup_files(backup.directory)
            }
            backup_file = matching_files.get(slot.path.name)
            if backup_file is None:
                raise ValueError(f"所选备份不含 {slot.path.name}")
            current_size = slot.path.stat().st_size
            current_sha256 = sha256_file(slot.path)
        except Exception as error:
            self._show_error(f"无法生成恢复预览：{error}")
            return
        preview = build_restore_preview(
            slot_name=slot.path.name,
            current_size=current_size,
            current_sha256=current_sha256,
            backup_size=backup_file.size,
            backup_sha256=backup_file.sha256,
        )
        answer = QMessageBox.warning(
            self,
            "确认恢复存档",
            preview,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        workspace = self.workspace
        self._run_async(
            "正在校验备份并恢复槽位…",
            lambda: restore_slot(
                workspace,
                slot,
                backup.directory,
                backup_root=_app_data_path(),
                expected_backup_sha256=backup_file.sha256,
            ),
            lambda _result: self._after_restore(),
        )

    def _after_restore(self) -> None:
        QMessageBox.information(self, "恢复成功", "目标槽位已恢复，并已保留恢复前完整快照。")
        self.session = None
        self.force_crash_session = None
        self._reload_workspace()

    def _diagnostic_report(self) -> dict[str, object]:
        if self.workspace is None:
            return {"status": "not_loaded"}
        return {
            "schema": 1,
            "application": APP_NAME,
            "installation": {
                "name": self.workspace.identity.name,
                "known": self.workspace.identity.is_known,
                "write_supported": self.workspace.identity.write_supported,
                "reasons": self.workspace.identity.reasons,
                "fingerprints": self.workspace.identity.fingerprints,
            },
            "resources": {
                "weapon_count": len(self.workspace.catalog),
                "waz_record_count": len(self.workspace.waz.records),
            },
            "slots": [
                {
                    "file": slot.path.name,
                    "size": len(slot.raw),
                    "sha256": slot.sha256,
                    "editable": slot.is_editable,
                    "completed_weapons": sum(
                        slot.progress.records[index * 3].development_state == 5
                        for index in range(63)
                    ),
                }
                for slot in self.workspace.slots
            ],
            "slot_failures": [
                {
                    "file": failure.path.name,
                    "number": failure.number,
                    "message": failure.message,
                }
                for failure in self.workspace.slot_failures
            ],
            "backups": [
                {
                    "created_utc": backup.created_utc,
                    "operation": backup.operation,
                    "file_count": backup.file_count,
                }
                for backup in list_backups(_app_data_path() / "snapshots")
            ],
        }

    def _refresh_diagnostics(self) -> None:
        self.diagnostics_text.setPlainText(
            json.dumps(self._diagnostic_report(), ensure_ascii=False, indent=2)
        )

    def _export_diagnostics(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出脱敏诊断", "bfmod-diagnostics.json", "JSON (*.json)"
        )
        if path:
            Path(path).write_text(
                json.dumps(self._diagnostic_report(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.statusBar().showMessage("脱敏诊断已导出", 4000)

    def _run_async(
        self,
        message: str,
        operation: Callable[[], object],
        on_success: Callable[[object], None],
    ) -> None:
        self.setEnabled(False)
        self.busy_indicator.show()
        self.statusBar().showMessage(message)
        worker = Worker(operation)
        self.workers.add(worker)
        worker.signals.succeeded.connect(on_success)
        worker.signals.failed.connect(self._show_error)
        worker.signals.finished.connect(lambda: self._worker_finished(worker))
        self.thread_pool.start(worker)

    def _worker_finished(self, worker: Worker) -> None:
        self.workers.discard(worker)
        if self.workers:
            return
        self.busy_indicator.hide()
        self.setEnabled(True)
        self.statusBar().showMessage("就绪", 2500)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "操作未完成", message)
        self.statusBar().showMessage("操作未完成", 5000)

    def _load_favorites(self) -> set[int]:
        value = self.settings.value("favorites", [])
        if isinstance(value, int):
            value = [value]
        if not isinstance(value, list):
            return set()
        return {int(item) for item in value if str(item).isdigit() and 0 <= int(item) < 63}

    def _restore_geometry(self) -> None:
        geometry = self.settings.value("window_geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: Any) -> None:
        if self.workers:
            QMessageBox.information(
                self,
                "后台操作尚未完成",
                "请等待当前读取、备份或提交操作完成后再退出。",
            )
            event.ignore()
            return
        if (self.session is not None and self.session.changes) or (
            self.force_crash_session is not None and self.force_crash_session.changes
        ):
            answer = QMessageBox.question(
                self,
                "退出并放弃草稿？",
                "还有未应用的修改。确定退出吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._deactivate_realtime_session("窗口关闭", record_stop=False)
        self.settings.setValue("window_geometry", self.saveGeometry())
        event.accept()


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setStyle("Fusion")
    _configure_windows_font(app)
    app.setStyleSheet(STYLE_SHEET)
    return app


def _configure_windows_font(app: QApplication) -> None:
    """显式加载系统中文字体，兼容缺少 Qt 内置字体目录的独立环境。"""

    candidates = (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "Deng.ttf",
    )
    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            font = QFont(families[0], 10)
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            app.setFont(font)
            return


def main() -> int:
    app = create_application()
    DEFAULT_APP_DATA.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(DEFAULT_APP_DATA / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(
            None,
            APP_NAME,
            "修改器已经在运行。为避免并发写入，不会启动第二个实例。",
        )
        return 2
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
