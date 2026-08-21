"""統計取込(設計書 §14-9、局面B)。

    **試験を選択** → CSV指定 → 検証チェーン → 確定 → **フラグ一覧と改訂導線**。
    **導出した受験者数と、飛ばした記述式の件数・出題番号を必ず画面に出す**
    (§9.2-2、§10.2-(4))

**試験を先に選ぶ**のがこの画面の要点である(設計書 §1.4, §9.1)。局面Bで問題を
作れてしまうと、同じ問題が毎年二重登録される。そのためこの画面には問題を作る
導線が一切ない。出題セットは finalize 時点で「どの版を何番として出したか」が
記録済みなので、集計 CSV は**そのセットに統計を与えるだけ**でよい(設計書 §1.2)。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.db import E_DRAFT, Exam
from ..core.exam import apply_stats, exam_summary, flagged_after_import
from ..core.validate import validate_stats_import
from ..io.csv_stats import StatsFile, StatsFormatError, parse_stats_csv
from .common import fill_issue_list

log = logging.getLogger(__name__)


class StatsImportView(QWidget):
    """統計取込のタブ。"""

    #: 「この問題を改訂する」(設計書 §2.6, §9.3)。question_id を渡す。
    reviseRequested = Signal(int)
    #: 取り込んだ(exam_id)。
    imported = Signal(int)

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.stats_file: StatsFile | None = None
        self.issues: list = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "局面B(平常運用)。**先に試験を選ぶ**。この画面では問題を作れない"
                "(同じ問題の二重登録を防ぐため。設計書 §1.4)。",
                self,
            )
        )
        layout.addWidget(self._build_target())
        layout.addWidget(self._build_validation(), 1)
        layout.addWidget(self._build_flags(), 1)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # -- 組み立て -----------------------------------------------------------
    def _build_target(self) -> QGroupBox:
        box = QGroupBox("① 試験を選ぶ  ② 集計 CSV を指定する", self)
        row = QHBoxLayout(box)

        self.exam_box = QComboBox(box)
        self.exam_box.currentIndexChanged.connect(self._on_exam_changed)
        row.addWidget(QLabel("試験", box))
        row.addWidget(self.exam_box, 1)

        self.csv_edit = QLineEdit(box)
        pick = QPushButton("集計 CSV…", box)
        pick.clicked.connect(self._pick_csv)
        row.addWidget(self.csv_edit, 2)
        row.addWidget(pick)

        self.validate_button = QPushButton("検証する", box)
        self.validate_button.clicked.connect(self.run_validation)
        row.addWidget(self.validate_button)

        self.apply_button = QPushButton("取り込む", box)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.run_import)
        row.addWidget(self.apply_button)
        return box

    def _build_validation(self) -> QGroupBox:
        box = QGroupBox("③ 検証チェーン(設計書 §9.2)", self)
        layout = QVBoxLayout(box)

        #: 導出した受験者数・飛ばした記述式は必ず出す(設計書 §9.2-2、§10.2-(4))。
        self.summary_label = QLabel("", box)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.issue_list = QListWidget(box)
        layout.addWidget(self.issue_list)
        return box

    def _build_flags(self) -> QGroupBox:
        box = QGroupBox("④ 取込後のフラグ一覧と改訂導線(設計書 §9.3, §2.6)", self)
        layout = QVBoxLayout(box)

        self.flag_table = QTableWidget(0, 3, box)
        self.flag_table.setHorizontalHeaderLabels(["出題番号", "問題ID", "フラグ"])
        self.flag_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.flag_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.flag_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.flag_table.doubleClicked.connect(self._revise_selected)
        layout.addWidget(self.flag_table)

        row = QHBoxLayout()
        self.revise_button = QPushButton("この問題を改訂する", box)
        self.revise_button.clicked.connect(self._revise_selected)
        row.addWidget(self.revise_button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _pick_csv(self) -> None:
        start = self.csv_edit.text() or str(paths.imports_dir())
        selected, _ = QFileDialog.getOpenFileName(self, "集計 CSV を選ぶ", start, "CSV (*.csv)")
        if selected:
            self.csv_edit.setText(selected)

    # -- 読み込み -----------------------------------------------------------
    def refresh(self) -> None:
        previous = self.exam_box.currentData()
        self.exam_box.blockSignals(True)
        self.exam_box.clear()
        for exam in self.workspace.session.query(Exam).order_by(Exam.id).all():
            summary = exam_summary(self.workspace.session, exam)
            self.exam_box.addItem(
                f"#{exam.id} {exam.name or ''}({summary['status']}, {summary['n_items']} 問)",
                exam.id,
            )
        index = self.exam_box.findData(previous)
        self.exam_box.setCurrentIndex(index if index >= 0 else 0)
        self.exam_box.blockSignals(False)
        self._on_exam_changed()

    def current_exam(self) -> Exam | None:
        exam_id = self.exam_box.currentData()
        return self.workspace.session.get(Exam, exam_id) if exam_id else None

    def _on_exam_changed(self) -> None:
        self.apply_button.setEnabled(False)
        exam = self.current_exam()
        self._show_flags(exam)
        if exam is None:
            self.status.setText("試験がまだありません")
            return
        if exam.status == E_DRAFT:
            # 設計書 §9.1: 統計を与えられるのは確定済みの試験だけ。
            self.status.setText(
                f"試験 {exam.id} はまだ確定していません。先に「試験セット」タブで確定してください"
                "(設計書 §9.1)"
            )
            self.validate_button.setEnabled(False)
            return
        self.validate_button.setEnabled(True)
        self.status.setText(
            f"試験 {exam.id}({exam.status})。"
            + ("既に統計があります。取り込むと上書きされます" if exam.status == "imported" else "")
        )

    # -- 検証 ---------------------------------------------------------------
    def run_validation(self) -> bool:
        exam = self.current_exam()
        if exam is None or exam.status == E_DRAFT:
            return False

        path = self.csv_edit.text().strip()
        if not path:
            self.status.setText("集計 CSV を選んでください")
            return False

        try:
            self.stats_file = parse_stats_csv(path)
        except StatsFormatError as exc:
            self.stats_file = None
            self.summary_label.setText("")
            self.issue_list.clear()
            self.issue_list.addItem(f"[ブロック] {exc}")
            self.apply_button.setEnabled(False)
            self.status.setText("集計 CSV を読めませんでした")
            return False

        exam_items = {item.position: item.correct_asked for item in exam.items}
        self.issues = validate_stats_import(
            self.stats_file.rows,
            exam_items,
            pattern_columns_found=self.stats_file.pattern_columns_found,
            missing_fixed_columns=self.stats_file.missing_fixed_columns,
            n_examinees=self.stats_file.meta.n_examinees,
            n_non_mcq=len(self.stats_file.non_mcq_rows),
        )
        fill_issue_list(self.issue_list, self.issues)
        self.summary_label.setText(self.summary_text())

        blocked = any(i.blocking for i in self.issues)
        self.apply_button.setEnabled(not blocked)
        self.status.setText("取り込めます" if not blocked else "検証で止まりました。取り込めません")
        return not blocked

    def summary_text(self) -> str:
        """導出した受験者数と、飛ばした記述式(設計書 §9.2-2、§10.2-(4))。"""
        if self.stats_file is None:
            return ""
        meta = self.stats_file.meta
        parts = [f"形式: {self.stats_file.dialect}", f"選択式 {self.stats_file.n_rows} 行"]
        if meta.n_examinees is None:
            parts.append(f"受験者数 {meta.effective_n}(度数合計から導出。CSV にメタ行が無い)")
        else:
            parts.append(f"受験者数 {meta.n_examinees}(CSV のメタ行)")

        if self.stats_file.non_mcq_rows:
            positions = "、".join(f"問{r.position}" for r in self.stats_file.non_mcq_rows)
            parts.append(
                f"統計の対象外として飛ばした設問 {len(self.stats_file.non_mcq_rows)} 件"
                f"({positions})"
            )
        else:
            parts.append("飛ばした設問なし")
        return " / ".join(parts)

    # -- 取込 ---------------------------------------------------------------
    def run_import(self) -> bool:
        exam = self.current_exam()
        if exam is None or self.stats_file is None:
            return False
        if any(i.blocking for i in self.issues):
            self.status.setText("検証に失敗しているため取り込みません")
            return False

        result = apply_stats(
            self.workspace.session,
            exam,
            self.stats_file.rows,
            source_file=self.csv_edit.text().strip(),
            disc_type=self.stats_file.meta.disc_type,
            n_examinees=self.stats_file.meta.effective_n,
            thresholds=self.workspace.settings.thresholds,
        )
        self.workspace.commit()

        # 読み直しを先に済ませる。あとにすると refresh が出す状態表示で
        # 取込の知らせが上書きされてしまう。
        self.refresh()
        self._show_flags(exam)
        self.apply_button.setEnabled(False)
        self.status.setText(
            f"統計を {result.written} 問に取り込みました(status={exam.status})。"
            f"要点検 {len(result.flagged)} 問"
        )
        self.imported.emit(exam.id)
        return True

    def _show_flags(self, exam: Exam | None) -> None:
        flagged = flagged_after_import(self.workspace.session, exam) if exam else []
        self.flag_table.setRowCount(len(flagged))
        for row, (position, question_id, flags) in enumerate(flagged):
            for column, text in enumerate((str(position), str(question_id), "、".join(flags))):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, question_id)
                self.flag_table.setItem(row, column, item)
        self.revise_button.setEnabled(bool(flagged))

    def _revise_selected(self) -> None:
        row = self.flag_table.currentRow()
        if row < 0:
            return
        question_id = self.flag_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
        if question_id is not None:
            self.reviseRequested.emit(int(question_id))
