"""過去問一括取込(設計書 §14-6、局面A)。

    docx+集計CSV指定 → 書式付きプレビュー → 目視確認 → 一括登録

**局面Aは一度きり**(設計書 §1.1)。平常運用で毎年ここを通ることはない。毎年の
統計は「統計取込」タブ(局面B)で確定済みの試験に与える。取り違えると同じ問題が
二重登録されるので(設計書 §1.4, §17)、タブを分けたうえで画面にもそう書く。

登録そのものは ``core.importer.import_parsed_exam`` が行う。CLI と同じ経路である。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.importer import ImportReport, import_parsed_exam
from ..core.validate import ParsedQuestionView, cross_validate_import, validate_stats_import
from ..io.csv_stats import StatsFormatError, parse_stats_csv
from ..io.docx_read import parse_docx
from .common import fill_issue_list, plain

log = logging.getLogger(__name__)


class ImportView(QWidget):
    """過去問一括取込のタブ。"""

    #: 取り込んで試験ができた(exam_id)。他タブの読み直しに使う。
    imported = Signal(int)

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.parsed = None
        self.stats_file = None
        self.issues: list = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "局面A(初期構築)。過去問の docx と集計 CSV をまとめてバンクに入れる。"
                "毎年の統計は「統計取込」タブから確定済みの試験に与える(設計書 §1.1, §1.4)。",
                self,
            )
        )
        layout.addWidget(self._build_inputs())
        layout.addWidget(self._build_preview(), 1)
        layout.addLayout(self._build_buttons())

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # -- 組み立て -----------------------------------------------------------
    def _build_inputs(self) -> QGroupBox:
        box = QGroupBox("入力", self)
        form = QFormLayout(box)

        self.docx_edit, docx_row = self._file_row("問題 docx", "Word (*.docx)")
        form.addRow("問題 docx", docx_row)
        self.stats_edit, stats_row = self._file_row("集計 CSV", "CSV (*.csv)")
        form.addRow("集計 CSV(任意)", stats_row)

        self.name_edit = QLineEdit(box)
        self.name_edit.setPlaceholderText("空なら docx のファイル名(CSV にメタ行があればそちら)")
        form.addRow("試験名", self.name_edit)

        self.date_edit = QLineEdit(box)
        self.date_edit.setPlaceholderText("2025-02-10")
        form.addRow("試験日", self.date_edit)

        self.course_edit = QLineEdit(box)
        form.addRow("科目", self.course_edit)
        self.cohort_edit = QLineEdit(box)
        form.addRow("学年・期", self.cohort_edit)

        self.force_check = QCheckBox("不整合があっても登録する", box)
        self.force_check.setToolTip(
            "検証で止まった内容をそのまま入れる。**原則として使わない**"
            "(設計書 §17: 想定と違う形を黙って読み替えない)。"
        )
        form.addRow("強行", self.force_check)
        return box

    def _file_row(self, label: str, filters: str) -> tuple[QLineEdit, QWidget]:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(holder)
        button = QPushButton("選ぶ…", holder)
        button.clicked.connect(lambda: self._pick(edit, label, filters))
        row.addWidget(edit, 1)
        row.addWidget(button)
        return edit, holder

    def _pick(self, edit: QLineEdit, label: str, filters: str) -> None:
        start = edit.text() or str(paths.imports_dir())
        selected, _ = QFileDialog.getOpenFileName(self, f"{label} を選ぶ", start, filters)
        if selected:
            edit.setText(selected)

    def _build_preview(self) -> QGroupBox:
        box = QGroupBox("プレビュー(書式付き)と相互検証", self)
        layout = QVBoxLayout(box)
        self.preview = QTextBrowser(box)
        layout.addWidget(self.preview, 3)
        self.issue_list = QListWidget(box)
        self.issue_list.setMaximumHeight(140)
        layout.addWidget(self.issue_list, 1)
        return box

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.preview_button = QPushButton("読み込んでプレビュー", self)
        self.preview_button.clicked.connect(self.load_preview)
        self.import_button = QPushButton("この内容で一括登録", self)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.run_import)
        row.addWidget(self.preview_button)
        row.addWidget(self.import_button)
        row.addStretch(1)
        return row

    # -- 動作 ---------------------------------------------------------------
    def refresh(self) -> None:
        """他タブからの読み直し。取込画面は入力を持ち越す(消すと選び直しになる)。"""

    def load_preview(self) -> bool:
        """docx と CSV を読み、相互検証まで通してプレビューする。"""
        docx_path = self.docx_edit.text().strip()
        if not docx_path:
            self.status.setText("問題 docx を選んでください")
            return False

        try:
            self.parsed = parse_docx(docx_path, images_dir=paths.images_dir())
        except Exception as exc:  # 壊れた docx を掴んでも落とさない
            log.exception("docx を読めませんでした")
            self.status.setText(f"docx を読めませんでした: {exc}")
            self.import_button.setEnabled(False)
            return False

        self.stats_file = None
        stats_path = self.stats_edit.text().strip()
        if stats_path:
            try:
                self.stats_file = parse_stats_csv(stats_path)
            except StatsFormatError as exc:
                self.status.setText(f"集計 CSV を読めませんでした: {exc}")
                self.import_button.setEnabled(False)
                return False

        self.issues = list(self.parsed.issues)
        if self.stats_file is not None:
            views = [
                ParsedQuestionView(q.number, q.stem_html, tuple(q.choice_htmls))
                for q in self.parsed.questions
            ]
            self.issues.extend(cross_validate_import(views, self.stats_file.rows))
            self.issues.extend(self._stats_chain_issues())

        self.preview.setHtml(self._preview_html())
        fill_issue_list(self.issue_list, self.issues)
        self.status.setText(self._summary_text())
        self.import_button.setEnabled(not self.blocking_issues() or self.force_check.isChecked())
        return True

    def _stats_chain_issues(self) -> list:
        """検証チェーン(設計書 §9.2)を**プレビューの時点で**通す。

        列の欠落や「人数か割合か」の取り違えは、登録してから知るのでは遅い。
        照合相手の正答には CSV 自身のものを使う。局面Aではバンクの正答もこの CSV から
        入る(``core.importer``)ので、登録後に行う照合と同じ突き合わせになる。
        """
        return validate_stats_import(
            self.stats_file.rows,
            {row.position: row.correct for row in self.stats_file.rows},
            pattern_columns_found=self.stats_file.pattern_columns_found,
            missing_fixed_columns=self.stats_file.missing_fixed_columns,
            n_examinees=self.stats_file.meta.n_examinees,
            n_non_mcq=len(self.stats_file.non_mcq_rows),
        )

    def blocking_issues(self) -> list:
        return [i for i in self.issues if i.blocking]

    def _summary_text(self) -> str:
        parts = [f"docx: {len(self.parsed.questions)} 設問"]
        if self.parsed.skipped:
            parts.append(f"読み飛ばした体裁行 {len(self.parsed.skipped)} 行")
        if self.parsed.unexpected_formats:
            parts.append(f"想定外の書式 {len(self.parsed.unexpected_formats)} 箇所")
        if self.stats_file is not None:
            meta = self.stats_file.meta
            parts.append(f"集計CSV[{self.stats_file.dialect}] 選択式 {self.stats_file.n_rows} 行")
            # 設計書 §9.2-2: 導出した受験者数は必ず画面に出す。
            derived = "(度数合計から導出)" if meta.n_examinees is None else ""
            parts.append(f"受験者数 {meta.effective_n}{derived}")
            if self.stats_file.non_mcq_rows:
                positions = "、".join(str(r.position) for r in self.stats_file.non_mcq_rows)
                parts.append(
                    f"選択式でない設問 {len(self.stats_file.non_mcq_rows)} 問を除外(問{positions})"
                )
        blockers = len(self.blocking_issues())
        parts.append(f"不整合 {blockers} 件 / 警告 {len(self.issues) - blockers} 件")
        return " / ".join(parts)

    def _preview_html(self) -> str:
        """設問を**書式付きで**並べる。目視確認がこの画面の目的(設計書 §14-6)。"""
        correct_by_position = (
            {r.position: r.correct for r in self.stats_file.rows} if self.stats_file else {}
        )
        blocks: list[str] = []
        for question in self.parsed.questions:
            correct = correct_by_position.get(question.number, "")
            lines = [f"<p><b>問 {question.number}</b>　{question.stem_html}"]
            for label, html in zip("abcde", question.choice_htmls, strict=False):
                mark = "✔" if label in correct else "　"
                lines.append(f"<br>{mark} {label}　{html}")
            if question.image_paths:
                lines.append(f"<br><i>画像 {len(question.image_paths)} 枚</i>")
            lines.append("</p>")
            blocks.append("".join(lines))
        return "\n".join(blocks) or "<p>設問が抽出できませんでした</p>"

    def run_import(self) -> ImportReport | None:
        """一括登録する。プレビューを通していなければ先に通す。"""
        if self.parsed is None and not self.load_preview():
            return None
        if self.blocking_issues() and not self.force_check.isChecked():
            self.status.setText("不整合があるため登録しませんでした(強行するには「強行」を入れる)")
            return None

        report = import_parsed_exam(
            self.workspace.session,
            self.parsed,
            self.stats_file,
            name=self.name_edit.text().strip() or Path(self.docx_edit.text()).stem,
            exam_date=self.date_edit.text().strip() or None,
            course=self.course_edit.text().strip() or None,
            cohort=self.cohort_edit.text().strip() or None,
            source_file=self.stats_edit.text().strip(),
            force=self.force_check.isChecked(),
        )

        # **登録は残す。** 確定(finalize)や統計の検証で止まっても、抽出できた設問を
        # 捨てる理由にはならない。正答が分からない設問は下書きとして入り、あとから
        # 正答を入れて確定できる(設計書 §2.5)。統計だけが残っているなら、CSV を
        # 直して「統計取込」タブ(局面B)から与えられる。CLI も同じ振る舞いをする。
        self.workspace.commit()
        if report.stats_issues:
            fill_issue_list(self.issue_list, report.stats_issues)

        self.status.setText(self._report_text(report))
        self.import_button.setEnabled(False)
        self.imported.emit(report.exam.id)
        return report

    def _report_text(self, report: ImportReport) -> str:
        parts = [f"試験 {report.exam.id}「{report.exam.name}」に {len(report.registered)} 問を登録"]
        if report.blocked:
            parts.append(f"ただし {report.blocked_reason}(バンクへの登録は残っています)")
        if report.drafts:
            numbers = "、".join(str(q.number) for q in report.drafts)
            parts.append(f"正答不明のため下書き {len(report.drafts)} 問(問{numbers})")
        if report.duplicates:
            numbers = "、".join(str(q.number) for q in report.duplicates)
            parts.append(f"重複の疑い {len(report.duplicates)} 件(問{numbers})")
        if report.stats_written:
            parts.append(f"統計を {report.stats_written} 問に取り込み")
        if report.flagged:
            flagged = "、".join(f"問{p}" for p, _, _ in report.flagged)
            parts.append(f"要点検 {len(report.flagged)} 問({flagged})")

        failed = [q for q in report.questions if not q.registered]
        if failed:
            reasons = "、".join(
                f"問{q.number}: {plain(q.issues[0].message) if q.issues else '不明'}"
                for q in failed
            )
            parts.append(f"登録できなかった設問 {len(failed)} 問({reasons})")
        return " / ".join(parts)
