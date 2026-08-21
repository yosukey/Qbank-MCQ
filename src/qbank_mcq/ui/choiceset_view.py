"""選択肢セット(設計書 §14-4)。

    セット一覧、近似セットの関連図、セット内全設問 × 項目のマーク率マトリクス、
    **「このセットで新しい問いを作る」導線**、統合・リンク編集、タグ除去一致の監査

マトリクスは「この項目はどの問い方のときに効いたか」を横に読む表である。順序が
変わっても項目単位で追える(設計書 §6.4-5)ので、行は設問、列は**項目番号**にする。
印字記号(a〜e)を列にすると、並び順の違う設問が同じ列に混ざって読めなくなる。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.bank import add_link, merge_choice_sets, remove_link
from ..core.choiceset import audit_tagless_duplicates
from ..core.db import ChoiceSet
from ..core.reporting import ChoiceSetSummary, choice_set_summaries, set_item_matrix
from .common import plain, rate


class ChoiceSetView(QWidget):
    """選択肢セットのタブ。"""

    #: 「このセットで新しい問いを作る」(設計書 §2.4)。choice_set_id を渡す。
    createFromSetRequested = Signal(int)
    #: 一覧の問題を開きたい(マトリクスの行から)。
    questionRequested = Signal(int)

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.summaries: list[ChoiceSetSummary] = []

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_list())
        splitter.addWidget(self._build_detail())
        splitter.setSizes([380, 700])
        layout.addWidget(splitter, 1)
        layout.addLayout(self._build_buttons())

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # -- 組み立て -----------------------------------------------------------
    def _build_list(self) -> QGroupBox:
        box = QGroupBox("セット一覧", self)
        layout = QVBoxLayout(box)
        self.set_table = QTableWidget(0, 4, box)
        self.set_table.setHorizontalHeaderLabels(["ID", "項目", "設問数", "近似"])
        self.set_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.set_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.set_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.set_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.set_table.itemSelectionChanged.connect(self._on_set_selected)
        layout.addWidget(self.set_table)
        return box

    def _build_detail(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        items_box = QGroupBox("項目(項目番号は並び順ではなく安定 ID)", panel)
        items_layout = QVBoxLayout(items_box)
        self.items_label = QLabel("", items_box)
        self.items_label.setTextFormat(Qt.TextFormat.RichText)
        self.items_label.setWordWrap(True)
        items_layout.addWidget(self.items_label)
        layout.addWidget(items_box)

        links_box = QGroupBox("近似セットの関連(推移的に閉じない)", panel)
        links_layout = QVBoxLayout(links_box)
        self.link_table = QTableWidget(0, 4, links_box)
        self.link_table.setHorizontalHeaderLabels(["相手", "共通項目", "関係", "メモ"])
        self.link_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.link_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.link_table.setMaximumHeight(140)
        links_layout.addWidget(self.link_table)
        layout.addWidget(links_box)

        matrix_box = QGroupBox("設問 × 項目のマーク率", panel)
        matrix_layout = QVBoxLayout(matrix_box)
        self.matrix_table = QTableWidget(0, 0, matrix_box)
        self.matrix_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.matrix_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.matrix_table.doubleClicked.connect(self._open_selected_question)
        matrix_layout.addWidget(self.matrix_table)
        layout.addWidget(matrix_box, 1)
        return panel

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.create_button = QPushButton("このセットで新しい問いを作る", self)
        self.create_button.clicked.connect(self._create_from_set)
        self.merge_button = QPushButton("別のセットに統合する", self)
        self.merge_button.clicked.connect(self._merge)
        self.link_button = QPushButton("リンクを追加", self)
        self.link_button.clicked.connect(self._add_link)
        self.unlink_button = QPushButton("リンクを解除", self)
        self.unlink_button.clicked.connect(self._remove_link)
        self.audit_button = QPushButton("タグ除去一致の監査", self)
        self.audit_button.clicked.connect(self.run_audit)

        for button in (
            self.create_button,
            self.merge_button,
            self.link_button,
            self.unlink_button,
            self.audit_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        return row

    # -- 読み込み -----------------------------------------------------------
    def refresh(self) -> None:
        selected = self.selected_set_id()
        self.summaries = choice_set_summaries(self.workspace.session)

        self.set_table.setRowCount(len(self.summaries))
        for row, summary in enumerate(self.summaries):
            cells = [
                str(summary.set_id),
                summary.preview(),
                str(summary.n_questions),
                str(len(summary.links)),
            ]
            for column, text in enumerate(cells):
                self.set_table.setItem(row, column, QTableWidgetItem(text))

        if self.summaries:
            index = next(
                (i for i, s in enumerate(self.summaries) if s.set_id == selected),
                0,
            )
            self.set_table.selectRow(index)
        else:
            self._show_summary(None)

    def selected_set_id(self) -> int | None:
        row = self.set_table.currentRow() if hasattr(self, "set_table") else -1
        if 0 <= row < len(self.summaries):
            return self.summaries[row].set_id
        return None

    def _on_set_selected(self) -> None:
        row = self.set_table.currentRow()
        self._show_summary(self.summaries[row] if 0 <= row < len(self.summaries) else None)

    def _show_summary(self, summary: ChoiceSetSummary | None) -> None:
        if summary is None:
            self.items_label.setText("セットがありません")
            self.link_table.setRowCount(0)
            self.matrix_table.setRowCount(0)
            self.matrix_table.setColumnCount(0)
            return

        self.items_label.setText("<br>".join(f"{no}　{html}" for no, html in summary.items))

        self.link_table.setRowCount(len(summary.links))
        for row, link in enumerate(summary.links):
            cells = [
                str(link.other_id),
                str(link.shared or ""),
                link.relation or "",
                link.note or "",
            ]
            for column, text in enumerate(cells):
                self.link_table.setItem(row, column, QTableWidgetItem(text))

        self._show_matrix(summary)

    def _show_matrix(self, summary: ChoiceSetSummary) -> None:
        rows = set_item_matrix(self.workspace.session, summary.set_id)
        item_nos = [no for no, _ in summary.items]
        headers = ["問題", "版", "試験", "設問"] + [
            f"{no}: {plain(html)[:8]}" for no, html in summary.items
        ]

        self.matrix_table.setColumnCount(len(headers))
        self.matrix_table.setHorizontalHeaderLabels(headers)
        self.matrix_table.setRowCount(len(rows))

        for row, entry in enumerate(rows):
            fixed = [
                str(entry.question_id),
                f"v{entry.version_no}",
                str(entry.exam_date or "—"),
                plain(entry.stem_html)[:40],
            ]
            for column, text in enumerate(fixed):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, entry.question_id)
                self.matrix_table.setItem(row, column, cell)

            for offset, item_no in enumerate(item_nos):
                value = entry.rates.get(item_no)
                cell = QTableWidgetItem(rate(value))
                if item_no in entry.correct_item_nos:
                    # 正答だった項目。マーク率の高低の意味が錯乱肢と逆になる。
                    cell.setText(f"✔ {rate(value)}")
                self.matrix_table.setItem(row, len(fixed) + offset, cell)
        self.matrix_table.resizeColumnsToContents()

    # -- 操作 ---------------------------------------------------------------
    def _create_from_set(self) -> None:
        set_id = self.selected_set_id()
        if set_id is not None:
            self.createFromSetRequested.emit(set_id)

    def _open_selected_question(self) -> None:
        item = self.matrix_table.currentItem()
        if item is None:
            return
        question_id = item.data(Qt.ItemDataRole.UserRole)
        if question_id is not None:
            self.questionRequested.emit(int(question_id))

    def _merge(self) -> None:
        source_id = self.selected_set_id()
        if source_id is None:
            return
        others = [s.set_id for s in self.summaries if s.set_id != source_id]
        if not others:
            self.status.setText("統合先になるセットがありません")
            return

        target_id, ok = QInputDialog.getItem(
            self,
            "セットの統合",
            f"セット {source_id} をどのセットに統合しますか",
            [str(i) for i in others],
            0,
            False,
        )
        if not ok:
            return
        self.merge_into(source_id, int(target_id))

    def merge_into(self, source_id: int, target_id: int) -> bool:
        """統合を実行する(ダイアログを挟まないのでテストから呼べる)。"""
        session = self.workspace.session
        source = session.get(ChoiceSet, source_id)
        target = session.get(ChoiceSet, target_id)
        try:
            moved = merge_choice_sets(session, source, target)
        except ValueError as exc:
            self.workspace.rollback()
            self.status.setText(f"統合できません: {exc}")
            return False

        self.workspace.commit()
        self.status.setText(f"セット {source_id} を {target_id} に統合しました({moved} 版)")
        self.refresh()
        return True

    def _add_link(self) -> None:
        set_id = self.selected_set_id()
        if set_id is None:
            return
        others = [s.set_id for s in self.summaries if s.set_id != set_id]
        if not others:
            return
        other, ok = QInputDialog.getItem(
            self, "リンクの追加", "どのセットと関連づけますか", [str(i) for i in others], 0, False
        )
        if not ok:
            return
        add_link(self.workspace.session, set_id, int(other), note="手動で追加")
        self.workspace.commit()
        self.refresh()

    def _remove_link(self) -> None:
        set_id = self.selected_set_id()
        row = self.link_table.currentRow()
        if set_id is None or row < 0:
            return
        other = int(self.link_table.item(row, 0).text())
        if remove_link(self.workspace.session, set_id, other):
            self.workspace.commit()
            self.status.setText(f"セット {set_id} と {other} のリンクを解除しました")
            self.refresh()

    def run_audit(self) -> dict[str, list[int]]:
        """タグ除去一致の監査(設計書 §6.2, §17)。

        マークアップの付け忘れで同じ用語が別項目になっていないかを見る。
        """
        sets = {s.set_id: [html for _, html in s.items] for s in self.summaries}
        duplicates = audit_tagless_duplicates(sets)
        if not duplicates:
            self.status.setText("タグ除去で一致する項目はありません")
            return {}

        lines = [f"マークアップ違いの疑い {len(duplicates)} 件:"]
        for text, ids in sorted(duplicates.items()):
            lines.append(f"「{text}」 セット {ids}")
        self.status.setText("　".join(lines))
        return duplicates
