"""設定(設計書 §14-10)。

    タグ管理、フラグ閾値、近似リンク閾値、基準フォント、否定語リスト、
    バックアップ/復元

タグだけは DB(問題と結び付く)、残りは ``settings.json``(``core.config``)。
近似リンク閾値を変えたときは**リンクを張り直す**。古いリンクが残ると露出管理が
実際より厳しく効き続けるため(``core.bank.rebuild_all_links``)。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.bank import delete_tag, ensure_tag, rebuild_all_links, rename_tag, tag_usage
from ..core.config import MIN_SHARED_RANGE, FontSettings, Settings
from ..core.migrate import list_backups
from ..core.stats import FlagThresholds
from ..core.typing_rules import DEFAULT_NEGATIVE_WORDS
from .workspace import Workspace


class SettingsView(QWidget):
    """設定タブ。"""

    #: 設定を保存した(閾値が変わると一覧のフラグ表示も変わるため、全画面に配る)。
    settingsChanged = Signal()
    #: DB を差し替えた(復元)。開き直しが要る。
    databaseRestored = Signal()

    def __init__(self, workspace: Workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self._build_tags(), 1)
        right = QVBoxLayout()
        right.addWidget(self._build_thresholds())
        right.addWidget(self._build_fonts())
        right.addWidget(self._build_words())
        right.addWidget(self._build_backup())
        right.addStretch(1)
        top.addLayout(right, 1)
        layout.addLayout(top)

        buttons = QHBoxLayout()
        #: 通知は非モーダルの 1 行に出す。保存のたびにダイアログを閉じさせない。
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        self.revert_button = QPushButton("既定に戻す", self)
        self.revert_button.clicked.connect(self._revert)
        self.save_button = QPushButton("保存", self)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.load(self.workspace.settings)
        self.refresh()

    # -- タグ管理 -----------------------------------------------------------
    def _build_tags(self) -> QGroupBox:
        box = QGroupBox("タグ管理", self)
        layout = QVBoxLayout(box)

        self.tag_table = QTableWidget(0, 2, box)
        self.tag_table.setHorizontalHeaderLabels(["タグ", "問題数"])
        self.tag_table.horizontalHeader().setStretchLastSection(True)
        self.tag_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tag_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tag_table)

        row = QHBoxLayout()
        for text, slot in (
            ("追加", self._add_tag),
            ("改名", self._rename_tag),
            ("削除", self._delete_tag),
        ):
            button = QPushButton(text, box)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def refresh(self) -> None:
        """タグ一覧を読み直す。"""
        rows = tag_usage(self.workspace.session)
        self.tag_table.setRowCount(len(rows))
        for i, (tag, count) in enumerate(rows):
            name_item = QTableWidgetItem(tag.name)
            name_item.setData(Qt.ItemDataRole.UserRole, tag.id)
            self.tag_table.setItem(i, 0, name_item)
            self.tag_table.setItem(i, 1, QTableWidgetItem(str(count)))

    def _selected_tag(self):
        row = self.tag_table.currentRow()
        if row < 0:
            return None
        item = self.tag_table.item(row, 0)
        from ..core.db import Tag

        return self.workspace.session.get(Tag, item.data(Qt.ItemDataRole.UserRole))

    def _add_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "タグの追加", "タグ名")
        if not ok or not name.strip():
            return
        ensure_tag(self.workspace.session, name.strip())
        self.workspace.commit()
        self.refresh()

    def _rename_tag(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return
        name, ok = QInputDialog.getText(self, "タグの改名", "新しい名前", text=tag.name)
        if not ok or not name.strip():
            return
        if name.strip() != tag.name and self._tag_exists(name.strip()):
            answer = QMessageBox.question(
                self,
                "タグの統合",
                f"「{name.strip()}」は既にあります。統合しますか。\n"
                f"「{tag.name}」が付いた問題は「{name.strip()}」に付け替えられます。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        rename_tag(self.workspace.session, tag, name.strip())
        self.workspace.commit()
        self.refresh()
        self.settingsChanged.emit()

    def _tag_exists(self, name: str) -> bool:
        return any(t.name == name for t, _ in tag_usage(self.workspace.session))

    def _delete_tag(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return
        answer = QMessageBox.question(
            self,
            "タグの削除",
            f"「{tag.name}」を削除しますか。問題からタグが外れます(問題は残ります)。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_tag(self.workspace.session, tag)
        self.workspace.commit()
        self.refresh()
        self.settingsChanged.emit()

    # -- フラグ閾値・近似リンク閾値 -----------------------------------------
    def _build_thresholds(self) -> QGroupBox:
        box = QGroupBox("フラグ閾値・近似リンク閾値", self)
        form = QFormLayout(box)

        self.dead_rate = QDoubleSpinBox(box)
        self.dead_rate.setRange(0.0, 1.0)
        self.dead_rate.setSingleStep(0.01)
        self.dead_rate.setDecimals(3)
        form.addRow("死んだ選択肢(周辺マーク率 <)", self.dead_rate)

        self.overselect_rate = QDoubleSpinBox(box)
        self.overselect_rate.setRange(0.0, 1.0)
        self.overselect_rate.setSingleStep(0.01)
        self.overselect_rate.setDecimals(3)
        form.addRow("指示個数違反率(>)", self.overselect_rate)

        self.low_disc = QDoubleSpinBox(box)
        self.low_disc.setRange(-1.0, 1.0)
        self.low_disc.setSingleStep(0.01)
        self.low_disc.setDecimals(3)
        form.addRow("低識別とみなす識別係数(<)", self.low_disc)

        self.persistent = QSpinBox(box)
        self.persistent.setRange(1, 10)
        form.addRow("何回続いたら持続的低識別か", self.persistent)

        self.min_shared = QSpinBox(box)
        # 設計書 §6.3 の表で自動リンクの対象になるのは共通 3〜4 項目だけ。
        # それ以外の値は ``should_autolink`` に弾かれ、設定しても何も変わらない。
        self.min_shared.setRange(MIN_SHARED_RANGE[0], MIN_SHARED_RANGE[1])
        self.min_shared.setToolTip(
            "近似セットとしてリンクする共通項目数の下限。3 なら 2 肢差し替えまで、"
            "4 なら 1 肢差し替えだけをリンクする(設計書 §6.3)。"
        )
        form.addRow("近似リンクの共通項目数(≧)", self.min_shared)
        return box

    # -- 基準フォント -------------------------------------------------------
    def _build_fonts(self) -> QGroupBox:
        box = QGroupBox("基準フォント(冊子出力)", self)
        form = QFormLayout(box)
        self.mincho = QLineEdit(box)
        self.gothic = QLineEdit(box)
        self.latin = QLineEdit(box)
        form.addRow("基準(明朝)", self.mincho)
        form.addRow("強調(ゴシック)", self.gothic)
        form.addRow("ラテン文字", self.latin)

        self.columns = QSpinBox(box)
        self.columns.setRange(1, 3)
        form.addRow("段数", self.columns)

        self.font_size = QDoubleSpinBox(box)
        self.font_size.setRange(6.0, 20.0)
        self.font_size.setSingleStep(0.5)
        form.addRow("文字サイズ(pt)", self.font_size)
        return box

    # -- 否定語リスト -------------------------------------------------------
    def _build_words(self) -> QGroupBox:
        box = QGroupBox("否定語リスト(設計書 §4)", self)
        layout = QVBoxLayout(box)
        layout.addWidget(
            QLabel("1 行に 1 語。強調規則のチェックはこの一覧で否定形を判定する。", box)
        )
        self.words = QPlainTextEdit(box)
        self.words.setMaximumHeight(90)
        layout.addWidget(self.words)
        return box

    # -- バックアップ / 復元 -------------------------------------------------
    def _build_backup(self) -> QGroupBox:
        box = QGroupBox("バックアップ / 復元", self)
        layout = QVBoxLayout(box)
        self.db_label = QLabel(str(self.workspace.db_file), box)
        self.db_label.setWordWrap(True)
        layout.addWidget(self.db_label)

        row = QHBoxLayout()
        backup_button = QPushButton("いますぐバックアップ", box)
        backup_button.clicked.connect(self._backup)
        restore_button = QPushButton("バックアップから復元", box)
        restore_button.clicked.connect(self._restore)
        row.addWidget(backup_button)
        row.addWidget(restore_button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _backup(self) -> None:
        dest = self.workspace.backup()
        self.status.setText(f"控えを作りました: {dest}" if dest else "DB がまだありません")

    def _restore(self) -> None:
        backups = list_backups()
        start = str(backups[0].parent if backups else self.workspace.db_file.parent)
        selected, _ = QFileDialog.getOpenFileName(
            self, "復元するバックアップ", start, "SQLite (*.sqlite)"
        )
        if not selected:
            return
        answer = QMessageBox.question(
            self,
            "復元",
            f"いまの DB を {Path(selected).name} で置き換えます。\n"
            "置き換える前に、いまの DB の控えを自動で取ります。続けますか。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.restore_from(Path(selected))

    def restore_from(self, backup_file: Path) -> None:
        """復元してタブに開き直しを促す(ファイル選択を挟まないのでテストから呼べる)。"""
        from ..core.migrate import restore_database

        self.workspace.commit()
        self.workspace.close()
        previous = restore_database(backup_file, self.workspace.db_file)
        self.status.setText(
            f"復元しました。直前の DB の控え: {previous}" if previous else "復元しました。"
        )
        self.databaseRestored.emit()

    # -- 読み書き -----------------------------------------------------------
    def load(self, settings: Settings) -> None:
        self.dead_rate.setValue(settings.thresholds.dead_distractor_rate)
        self.overselect_rate.setValue(settings.thresholds.overselect_rate)
        self.low_disc.setValue(settings.thresholds.low_disc)
        self.persistent.setValue(settings.thresholds.persistent_min_exams)
        self.min_shared.setValue(settings.min_shared)

        self.mincho.setText(settings.fonts.mincho)
        self.gothic.setText(settings.fonts.gothic)
        self.latin.setText(settings.fonts.latin)
        self.columns.setValue(settings.fonts.columns)
        self.font_size.setValue(settings.fonts.font_size_pt)

        self.words.setPlainText("\n".join(settings.negative_words))

    def collect(self) -> Settings:
        """入力欄から設定を組み立てる。"""
        words = tuple(w.strip() for w in self.words.toPlainText().splitlines() if w.strip())
        return Settings(
            thresholds=FlagThresholds(
                dead_distractor_rate=self.dead_rate.value(),
                overselect_rate=self.overselect_rate.value(),
                low_disc=self.low_disc.value(),
                persistent_min_exams=self.persistent.value(),
            ),
            fonts=FontSettings(
                mincho=self.mincho.text().strip() or FontSettings().mincho,
                gothic=self.gothic.text().strip() or FontSettings().gothic,
                latin=self.latin.text().strip() or FontSettings().latin,
                columns=self.columns.value(),
                font_size_pt=self.font_size.value(),
            ),
            negative_words=words or DEFAULT_NEGATIVE_WORDS,
            min_shared=self.min_shared.value(),
        )

    def save(self) -> Settings:
        """設定を保存する。近似リンク閾値が変わっていればリンクを張り直す。"""
        previous = self.workspace.settings
        settings = self.collect()
        self.workspace.update_settings(settings)
        message = "保存しました"

        if settings.min_shared != previous.min_shared:
            n = rebuild_all_links(self.workspace.session, min_shared=settings.min_shared)
            self.workspace.commit()
            message += f"。閾値が変わったので近似リンクを張り直しました({n} 組)"
        self.status.setText(message)
        self.settingsChanged.emit()
        return settings

    def _revert(self) -> None:
        self.load(Settings())
