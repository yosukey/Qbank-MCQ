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
from .bank_view import BankView
from .question_detail import QuestionDetail
from .question_editor import QuestionEditor
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
        self.bank_view = BankView(self.workspace, self)
        self.bank_view.createRequested.connect(self.open_editor)
        self.bank_view.duplicateRequested.connect(self.open_duplicate)
        self.bank_view.editRequested.connect(lambda qid: self.open_editor(question_id=qid))
        self.bank_view.detailRequested.connect(self.open_detail)

        self.settings_view = SettingsView(self.workspace, self)
        self.settings_view.settingsChanged.connect(self.refresh_all)
        self.settings_view.databaseRestored.connect(self._reopen)

        self.tabs.addTab(self.bank_view, "問題バンク")
        self.tabs.addTab(self.settings_view, "設定")

    # -- 問題の編集と詳細(設計書 §14-2, §14-3)-----------------------------
    def open_editor(
        self,
        question_id: int | None = None,
        *,
        choice_set_id: int | None = None,
        derive_from_question_id: int | None = None,
    ) -> QuestionEditor:
        """編集ダイアログを開く。閉じたら一覧を読み直す。"""
        editor = QuestionEditor(
            self.workspace,
            question_id=question_id,
            choice_set_id=choice_set_id,
            derive_from_question_id=derive_from_question_id,
            parent=self,
        )
        editor.finished.connect(lambda _: self.refresh_all())
        editor.show()
        return editor

    def open_duplicate(self, question_id: int) -> QuestionEditor:
        """複製作成(設計書 §14-1)。保存すると派生になる(§2.2)。"""
        return self.open_editor(derive_from_question_id=question_id)

    def open_detail(self, question_id: int) -> QuestionDetail:
        detail = QuestionDetail(self.workspace, question_id, parent=self)
        # 設計書 §2.6: 詳細から改訂へ直接進める。
        detail.reviseRequested.connect(lambda qid: self.open_editor(question_id=qid))
        detail.show()
        return detail

    def show_question(self, question_id: int) -> None:
        """バンクのタブに切り替えてその問題を選ぶ(フラグ一覧からの導線)。"""
        self.tabs.setCurrentWidget(self.bank_view)
        self.bank_view.select_question(question_id)

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
