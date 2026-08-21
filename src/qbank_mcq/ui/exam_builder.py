"""試験セット作成(設計書 §14-7)。

    条件入力 → タイプ別候補一覧 → 差し替え → finalize前チェック → 確定

選定そのものは ``core.selection.select_candidates``(純関数)。画面は条件を組み立て、
結果を並べ、差し替えを受け付けるだけにする。

確定(finalize)は一方向である。設計書 §13.3:

    確定後はセット・使用版・正答を変更ロックし、恒久記録とする

そのため確定済みの試験を開いたときは、差し替えの操作をすべて無効にする。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.db import E_DRAFT, Exam
from ..core.exam import (
    ExamLockedError,
    build_candidates,
    check_finalize,
    create_exam,
    exam_summary,
    finalize_exam,
    selection_context,
    set_exam_items,
)
from ..core.selection import Candidate, SelectionConditions, select_candidates
from ..core.typing_rules import ITEM_TYPES
from ..core.validate import ExamLimits
from .common import fill_issue_list, number, p_with_type, plain

log = logging.getLogger(__name__)

CANDIDATE_HEADERS = ("ID", "タイプ", "否定", "設問", "正答率", "識別係数", "最終出題", "フラグ")


def candidate_cells(candidate: Candidate) -> list[str]:
    return [
        str(candidate.question_id),
        candidate.item_type or "?",
        "否定" if candidate.negative else "",
        plain(candidate.stem_html)[:44],
        p_with_type(candidate.p, candidate.item_type),
        number(candidate.disc),
        str(candidate.last_exam_year or "—"),
        "、".join(sorted(candidate.flags)),
    ]


class ExamBuilderView(QWidget):
    """試験セット作成のタブ。"""

    #: 試験の状態が変わった(作成・確定)。
    examChanged = Signal(int)

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.candidates: list[Candidate] = []
        self.selected: list[Candidate] = []
        self.exam: Exam | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_conditions())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_selected())
        splitter.addWidget(self._build_pool())
        splitter.setSizes([620, 460])
        layout.addWidget(splitter, 1)

        layout.addWidget(self._build_finalize())
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # -- 条件(設計書 §13.1)------------------------------------------------
    def _build_conditions(self) -> QGroupBox:
        box = QGroupBox("選定条件", self)
        grid = QGridLayout(box)

        self.total = QSpinBox(box)
        self.total.setRange(1, 200)
        self.total.setValue(50)
        grid.addWidget(QLabel("問数", box), 0, 0)
        grid.addWidget(self.total, 0, 1)

        self.type_spins: dict[str, QSpinBox] = {}
        for i, item_type in enumerate(ITEM_TYPES):
            spin = QSpinBox(box)
            spin.setRange(0, 200)
            spin.setToolTip("0 ならタイプ別配分を指定しない")
            grid.addWidget(QLabel(item_type, box), 0, 2 + i * 2)
            grid.addWidget(spin, 0, 3 + i * 2)
            self.type_spins[item_type] = spin

        self.tag_edit = QLineEdit(box)
        self.tag_edit.setPlaceholderText("発生=10、エナメル質=5")
        grid.addWidget(QLabel("タグ配分", box), 1, 0)
        grid.addWidget(self.tag_edit, 1, 1, 1, 3)

        self.min_disc = QDoubleSpinBox(box)
        self.min_disc.setRange(-1.01, 1.0)
        self.min_disc.setSingleStep(0.05)
        self.min_disc.setDecimals(2)
        self.min_disc.setSpecialValueText("下限なし")
        self.min_disc.setValue(-1.01)
        grid.addWidget(QLabel("識別係数 ≧", box), 1, 4)
        grid.addWidget(self.min_disc, 1, 5)

        self.recent_years = QSpinBox(box)
        self.recent_years.setRange(0, 10)
        self.recent_years.setSpecialValueText("しない")
        grid.addWidget(QLabel("直近 n 年の除外", box), 1, 6)
        grid.addWidget(self.recent_years, 1, 7)

        self.current_year = QSpinBox(box)
        self.current_year.setRange(2000, 2100)
        self.current_year.setValue(2026)
        grid.addWidget(QLabel("今年", box), 1, 8)
        grid.addWidget(self.current_year, 1, 9)

        self.new_ratio = QDoubleSpinBox(box)
        self.new_ratio.setRange(0.0, 1.0)
        self.new_ratio.setSingleStep(0.05)
        self.new_ratio.setDecimals(2)
        self.new_ratio.setToolTip("新作(統計のない問題)を混ぜる割合(設計書 §13.1)")
        grid.addWidget(QLabel("新作混入率", box), 2, 0)
        grid.addWidget(self.new_ratio, 2, 1)

        self.max_per_set = QSpinBox(box)
        self.max_per_set.setRange(1, 10)
        self.max_per_set.setValue(2)
        self.max_per_set.setToolTip("同一・近似セットからの出題上限(設計書 §6.4-1)")
        grid.addWidget(QLabel("セット上限", box), 2, 2)
        grid.addWidget(self.max_per_set, 2, 3)

        self.max_negative = QSpinBox(box)
        self.max_negative.setRange(0, 200)
        self.max_negative.setSpecialValueText("上限なし")
        grid.addWidget(QLabel("否定形上限", box), 2, 4)
        grid.addWidget(self.max_negative, 2, 5)

        self.select_button = QPushButton("候補を選定する", box)
        self.select_button.clicked.connect(self.run_selection)
        grid.addWidget(self.select_button, 2, 8, 1, 2)
        return box

    # -- 一覧 ---------------------------------------------------------------
    def _build_selected(self) -> QGroupBox:
        box = QGroupBox("出題セット(上から出題順)", self)
        layout = QVBoxLayout(box)
        self.selected_table = self._table(box)
        layout.addWidget(self.selected_table)

        row = QHBoxLayout()
        for text, slot in (
            ("↑", lambda: self._move(-1)),
            ("↓", lambda: self._move(1)),
            ("外す", self._remove_selected),
        ):
            button = QPushButton(text, box)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_pool(self) -> QGroupBox:
        box = QGroupBox("バンクの残り(差し替え用)", self)
        layout = QVBoxLayout(box)
        self.pool_table = self._table(box)
        layout.addWidget(self.pool_table)

        row = QHBoxLayout()
        add = QPushButton("出題セットに入れる", box)
        add.clicked.connect(self._add_selected)
        row.addWidget(add)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _table(self, parent: QWidget) -> QTableWidget:
        table = QTableWidget(0, len(CANDIDATE_HEADERS), parent)
        table.setHorizontalHeaderLabels(list(CANDIDATE_HEADERS))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        return table

    def _build_finalize(self) -> QGroupBox:
        box = QGroupBox("確定(設計書 §13.3)", self)
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.exam_box = QComboBox(box)
        self.exam_box.currentIndexChanged.connect(self._on_exam_selected)
        row.addWidget(QLabel("試験", box))
        row.addWidget(self.exam_box, 1)

        self.name_edit = QLineEdit(box)
        self.name_edit.setPlaceholderText("定期試験2026")
        row.addWidget(QLabel("名称", box))
        row.addWidget(self.name_edit)

        self.date_edit = QLineEdit(box)
        self.date_edit.setPlaceholderText("2026-02-10")
        row.addWidget(QLabel("試験日", box))
        row.addWidget(self.date_edit)

        self.create_button = QPushButton("この構成で試験を作る", box)
        self.create_button.clicked.connect(self.create_or_update_exam)
        row.addWidget(self.create_button)

        self.check_button = QPushButton("finalize 前チェック", box)
        self.check_button.clicked.connect(self.run_check)
        row.addWidget(self.check_button)

        self.finalize_button = QPushButton("確定する", box)
        self.finalize_button.clicked.connect(self.run_finalize)
        row.addWidget(self.finalize_button)
        layout.addLayout(row)

        self.check_list = QListWidget(box)
        self.check_list.setMaximumHeight(120)
        layout.addWidget(self.check_list)
        return box

    # -- 読み込み -----------------------------------------------------------
    def refresh(self) -> None:
        self.candidates = build_candidates(self.workspace.session)
        self._reload_exams()
        self._reload_pool()
        self._reload_selected()

    def _reload_exams(self) -> None:
        previous = self.exam_box.currentData()
        self.exam_box.blockSignals(True)
        self.exam_box.clear()
        self.exam_box.addItem("(新しい試験)", None)
        for exam in self.workspace.session.query(Exam).order_by(Exam.id).all():
            summary = exam_summary(self.workspace.session, exam)
            self.exam_box.addItem(
                f"#{exam.id} {exam.name or ''}({summary['status']}, {summary['n_items']} 問)",
                exam.id,
            )
        index = self.exam_box.findData(previous)
        self.exam_box.setCurrentIndex(index if index >= 0 else 0)
        self.exam_box.blockSignals(False)

    def _on_exam_selected(self) -> None:
        exam_id = self.exam_box.currentData()
        self.exam = self.workspace.session.get(Exam, exam_id) if exam_id else None
        if self.exam is not None:
            by_version = {c.qversion_id: c for c in self.candidates}
            self.selected = [
                by_version[item.qversion_id]
                for item in self.exam.items
                if item.qversion_id in by_version
            ]
            self.name_edit.setText(self.exam.name or "")
            self.date_edit.setText(self.exam.exam_date or "")
            if len(self.selected) != len(self.exam.items):
                # 旧版で出題された問題は最新版の候補一覧に出てこない。
                self.status.setText(
                    f"この試験の {len(self.exam.items)} 問のうち "
                    f"{len(self.selected)} 問が現在の最新版です(旧版での出題は編集できません)"
                )
        self._update_locked()
        self._reload_selected()
        self._reload_pool()

    def _update_locked(self) -> None:
        """確定済みの試験は変更ロック(設計書 §13.3)。"""
        locked = self.exam is not None and self.exam.status != E_DRAFT
        for widget in (self.create_button, self.finalize_button, self.select_button):
            widget.setEnabled(not locked)
        self.selected_table.setEnabled(not locked)
        self.pool_table.setEnabled(not locked)
        if locked:
            self.status.setText(
                f"試験 {self.exam.id} は {self.exam.status} です。"
                "確定後はセット・使用版・正答を変更できません(設計書 §13.3)"
            )

    def _reload_selected(self) -> None:
        self._fill(self.selected_table, self.selected)

    def _reload_pool(self) -> None:
        chosen = {c.question_id for c in self.selected}
        self.pool = [c for c in self.candidates if c.question_id not in chosen]
        self._fill(self.pool_table, self.pool)

    def _fill(self, table: QTableWidget, rows: list[Candidate]) -> None:
        table.setRowCount(len(rows))
        for row, candidate in enumerate(rows):
            for column, text in enumerate(candidate_cells(candidate)):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, candidate.question_id)
                table.setItem(row, column, item)
        table.resizeColumnsToContents()

    # -- 選定 ---------------------------------------------------------------
    def conditions(self) -> SelectionConditions:
        types = {t: spin.value() for t, spin in self.type_spins.items() if spin.value()}
        tags: dict[str, int] = {}
        for chunk in self.tag_edit.text().replace(",", "、").split("、"):
            name, _, count = chunk.partition("=")
            if name.strip() and count.strip().isdigit():
                tags[name.strip()] = int(count)

        return SelectionConditions(
            total=self.total.value(),
            type_distribution=types or None,
            tag_distribution=tags or None,
            min_disc=self.min_disc.value() if self.min_disc.value() > -1.005 else None,
            exclude_recent_years=self.recent_years.value() or None,
            current_year=self.current_year.value(),
            new_item_ratio=self.new_ratio.value() or None,
            limits=ExamLimits(
                max_per_set_group=self.max_per_set.value(),
                max_negative=self.max_negative.value() or None,
            ),
        )

    def run_selection(self) -> None:
        families, links = selection_context(self.workspace.session, self.candidates)
        result = select_candidates(
            self.candidates, self.conditions(), derivation_families=families, set_links=links
        )
        self.selected = list(result.selected)
        self._reload_selected()
        self._reload_pool()

        message = f"候補 {len(self.candidates)} 件から {len(self.selected)} 問を選定"
        if result.unmet:
            message += " / 満たせなかった条件: " + "、".join(result.unmet)
        self.status.setText(message)

    # -- 差し替え -----------------------------------------------------------
    def _selected_row(self, table: QTableWidget) -> int:
        return table.currentRow()

    def _move(self, delta: int) -> None:
        row = self._selected_row(self.selected_table)
        target = row + delta
        if row < 0 or not 0 <= target < len(self.selected):
            return
        self.selected[row], self.selected[target] = self.selected[target], self.selected[row]
        self._reload_selected()
        self.selected_table.selectRow(target)

    def _remove_selected(self) -> None:
        row = self._selected_row(self.selected_table)
        if 0 <= row < len(self.selected):
            self.selected.pop(row)
            self._reload_selected()
            self._reload_pool()

    def _add_selected(self) -> None:
        row = self._selected_row(self.pool_table)
        if 0 <= row < len(self.pool):
            self.selected.append(self.pool[row])
            self._reload_selected()
            self._reload_pool()

    # -- 試験として保存・確定 ------------------------------------------------
    def create_or_update_exam(self) -> Exam | None:
        """いまの構成で試験を作る(既に選んでいれば差し替える)。"""
        if not self.selected:
            self.status.setText("出題セットが空です")
            return None

        session = self.workspace.session
        if self.exam is None:
            self.exam = create_exam(
                session,
                name=self.name_edit.text().strip() or "無題の試験",
                exam_date=self.date_edit.text().strip() or None,
            )
        else:
            self.exam.name = self.name_edit.text().strip() or self.exam.name
            self.exam.exam_date = self.date_edit.text().strip() or self.exam.exam_date

        try:
            set_exam_items(
                session,
                self.exam,
                [(i + 1, c.qversion_id) for i, c in enumerate(self.selected)],
            )
        except ExamLockedError as exc:
            self.workspace.rollback()
            self.status.setText(str(exc))
            return None

        self.workspace.commit()
        self.status.setText(
            f"試験 {self.exam.id}「{self.exam.name}」に {len(self.selected)} 問を割り当てました"
            "(status=draft)"
        )
        self._reload_exams()
        self.exam_box.setCurrentIndex(self.exam_box.findData(self.exam.id))
        self.examChanged.emit(self.exam.id)
        return self.exam

    def limits(self) -> ExamLimits:
        types = {t: spin.value() for t, spin in self.type_spins.items() if spin.value()}
        return ExamLimits(
            max_per_set_group=self.max_per_set.value(),
            max_negative=self.max_negative.value() or None,
            type_distribution=types or None,
        )

    def run_check(self) -> None:
        """finalize 前チェック(設計書 §13.3)。確定はしない。"""
        if self.exam is None:
            self.status.setText("先に「この構成で試験を作る」を押してください")
            return
        report = check_finalize(self.workspace.session, self.exam, limits=self.limits())
        fill_issue_list(self.check_list, report.issues)
        self.status.setText("確定できます" if not report.blocked else "確定できません")

    def run_finalize(self) -> bool:
        if self.exam is None:
            self.status.setText("先に「この構成で試験を作る」を押してください")
            return False

        report = check_finalize(self.workspace.session, self.exam, limits=self.limits())
        fill_issue_list(self.check_list, report.issues)
        if report.blocked:
            self.status.setText("確定できません。ブロック項目を直してください")
            return False
        if report.warnings and not self._confirm_warnings(report.warnings):
            return False

        report = finalize_exam(self.workspace.session, self.exam, limits=self.limits())
        if report.blocked:
            self.workspace.rollback()
            self.status.setText("確定できませんでした")
            return False

        self.workspace.commit()
        # ロックの表示を先に更新する。あとにすると、_update_locked が出す
        # 「変更できません」で確定の知らせが上書きされてしまう。
        self._update_locked()
        self._reload_exams()
        self.status.setText(
            f"試験 {self.exam.id} を確定しました(status={self.exam.status})。"
            "以後この試験の構成は変更できません"
        )
        self.examChanged.emit(self.exam.id)
        return True

    def _confirm_warnings(self, warnings: list) -> bool:
        answer = QMessageBox.question(
            self,
            "警告があります",
            "次の警告が出ています。確定しますか。\n\n"
            + "\n".join(f"・{issue.message}" for issue in warnings),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
