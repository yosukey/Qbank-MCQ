"""メインウィンドウ。タブで画面を束ねる(設計書 §14)。

タブの並びは設計書 §14 の番号どおり。**局面の取り違えを防ぐ**ため(設計書 §1.4)、
局面A(過去問一括取込)と局面B(統計取込)は別のタブに分け、統計取込のタブでは
問題を作る導線を一切置かない。

どのタブも ``refresh()`` を持ち、他のタブが DB を変えたら呼ばれる。GUI は 1 本の
セッションを共有しているため(``ui.workspace``)、読み直しは安い。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget, QWidget

from .. import __version__
from .settings_view import SettingsView
from .workspace import Workspace

log = logging.getLogger(__name__)


@runtime_checkable
class RefreshableTab(Protocol):
    """DB が変わったら読み直せるタブ。"""

    def refresh(self) -> None: ...


class MainWindow(QMainWindow):
    def __init__(self, workspace: Workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle(f"ItemBank {__version__} — 口腔組織学 試験問題バンク")
        self.resize(1180, 780)

        self.tabs = QTabWidget(self)
        self.setCentralWidget(self.tabs)
        self._build_tabs()
        self._build_menu()
        self._show_startup_status()

    # -- 組み立て -----------------------------------------------------------
    def _build_tabs(self) -> None:
        self.settings_view = SettingsView(self.workspace, self)
        self.settings_view.settingsChanged.connect(self.refresh_all)
        self.settings_view.databaseRestored.connect(self._reopen)

        self.tabs.addTab(self.settings_view, "設定")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("ファイル")

        backup = QAction("バックアップを取る", self)
        backup.triggered.connect(self._backup)
        file_menu.addAction(backup)

        refresh = QAction("読み直す", self)
        refresh.setShortcut("F5")
        refresh.triggered.connect(self.refresh_all)
        file_menu.addAction(refresh)

        file_menu.addSeparator()
        quit_action = QAction("終了", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("ヘルプ")
        about = QAction("このアプリについて", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _show_startup_status(self) -> None:
        """DB の場所とスキーマ移行の結果を出す(設計書 §15)。"""
        migration = self.workspace.migration
        message = f"DB: {self.workspace.db_file}"
        if migration.changed:
            message += f"  /  スキーマ {migration.from_version} → {migration.to_version} に移行"
            if migration.backup:
                message += f"(バックアップ: {migration.backup.name})"
        self.statusBar().showMessage(message)

    # -- 動作 ---------------------------------------------------------------
    def refresh_all(self) -> None:
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if isinstance(widget, RefreshableTab):
                widget.refresh()

    def _backup(self) -> None:
        dest = self.workspace.backup()
        self.statusBar().showMessage(
            f"バックアップ: {dest}" if dest else "DB がまだありません", 8000
        )

    def _reopen(self) -> None:
        """復元後に DB を開き直す。"""
        self.workspace = Workspace.open(self.workspace.db_file)
        self.tabs.clear()
        self._build_tabs()
        self._show_startup_status()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "ItemBank について",
            f"ItemBank {__version__}\n"
            "口腔組織学 試験問題バンクシステム\n\n"
            f"DB: {self.workspace.db_file}\n"
            f"設定: {self.workspace.settings.min_shared} 項目以上で近似リンク",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt の命名
        try:
            self.workspace.commit()
        except Exception:  # pragma: no cover - 保存に失敗しても閉じる
            log.exception("終了時のコミットに失敗しました")
            self.workspace.rollback()
        self.workspace.close()
        super().closeEvent(event)
