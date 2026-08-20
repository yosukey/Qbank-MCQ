"""出力(設計書 §14-8)。

    冊子docx / 正答キー / 教員用照合表

統計レポート(設計書 §13.2)も同じ場所から出す。出力先の既定は
``%APPDATA%\\ItemBank\\exports``(設計書 §15: exe と同居させない)。

冊子の体裁は設定画面の基準フォントを使う(設計書 §14-10)。ここで
``DEFAULT_WRITER_CONFIG`` を使ってしまうと、設定を変えても冊子が変わらない。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.db import E_DRAFT, Exam
from ..core.exam import booklet_sources, exam_summary
from ..core.reporting import crosswalk_rows, report_rows
from ..io.csv_key import answer_key_filename, rows_from_exam_items, write_answer_key
from ..io.docx_write import BookletItem, write_booklet
from ..io.xlsx_report import write_crosswalk, write_stats_report

log = logging.getLogger(__name__)

#: 出力の種類。``key`` は ss-database に読ませる正答キー(設計書 §10.1)。
KINDS = (
    ("booklet", "問題冊子(.docx)"),
    ("key", "正答キー(.csv)"),
    ("crosswalk", "教員用照合表(.xlsx)"),
    ("report", "統計レポート(.xlsx)"),
)


class ExportView(QWidget):
    """出力のタブ。"""

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_target())
        layout.addWidget(self._build_kinds())

        row = QHBoxLayout()
        self.export_button = QPushButton("選んだものを書き出す", self)
        self.export_button.clicked.connect(self.export_selected)
        row.addWidget(self.export_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.result_list = QListWidget(self)
        layout.addWidget(self.result_list, 1)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # -- 組み立て -----------------------------------------------------------
    def _build_target(self) -> QGroupBox:
        box = QGroupBox("対象", self)
        row = QHBoxLayout(box)
        self.exam_box = QComboBox(box)
        self.exam_box.currentIndexChanged.connect(self._update_hint)
        row.addWidget(QLabel("試験", box))
        row.addWidget(self.exam_box, 1)

        self.out_edit = QLineEdit(str(paths.exports_dir()), box)
        pick = QPushButton("出力先…", box)
        pick.clicked.connect(self._pick_dir)
        row.addWidget(self.out_edit, 2)
        row.addWidget(pick)
        return box

    def _build_kinds(self) -> QGroupBox:
        box = QGroupBox("出力するもの", self)
        row = QHBoxLayout(box)
        self.kind_checks: dict[str, QCheckBox] = {}
        for kind, label in KINDS:
            check = QCheckBox(label, box)
            check.setChecked(True)
            row.addWidget(check)
            self.kind_checks[kind] = check
        row.addStretch(1)
        return box

    def _pick_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "出力先", self.out_edit.text())
        if selected:
            self.out_edit.setText(selected)

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
        self._update_hint()

    def _update_hint(self) -> None:
        exam = self.current_exam()
        if exam is None:
            self.status.setText("試験がまだありません")
            return
        if exam.status == E_DRAFT:
            # 確定前の冊子は「まだ変わりうる版」を刷ることになる(設計書 §13.3)。
            self.status.setText(
                f"試験 {exam.id} は draft です。確定前の出力は下見用として扱ってください"
            )
        else:
            self.status.setText(f"試験 {exam.id}({exam.status})")

    def current_exam(self) -> Exam | None:
        exam_id = self.exam_box.currentData()
        return self.workspace.session.get(Exam, exam_id) if exam_id else None

    # -- 書き出し -----------------------------------------------------------
    def selected_kinds(self) -> list[str]:
        return [kind for kind, check in self.kind_checks.items() if check.isChecked()]

    def export_selected(self) -> list[Path]:
        exam = self.current_exam()
        if exam is None:
            return []
        return self.export(exam, self.selected_kinds(), Path(self.out_edit.text()))

    def export(self, exam: Exam, kinds: list[str], out_dir: Path) -> list[Path]:
        """指定の出力物を書き出し、書けたパスを返す。"""
        session = self.workspace.session
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        self.result_list.clear()

        for kind in kinds:
            try:
                path = self._write_one(session, exam, kind, out_dir)
            except Exception as exc:  # 1 つ失敗しても残りは書き出す
                log.exception("%s の書き出しに失敗しました", kind)
                self.result_list.addItem(f"{kind}: 失敗 — {exc}")
                continue
            written.append(path)
            self.result_list.addItem(f"{kind}: {path}")

        self.status.setText(f"{len(written)} 件を書き出しました({out_dir})")
        return written

    def _write_one(self, session, exam: Exam, kind: str, out_dir: Path) -> Path:
        if kind == "key":
            pairs = [(item.position, item.correct_asked) for item in exam.items]
            return write_answer_key(
                rows_from_exam_items(pairs), out_dir / answer_key_filename(exam.id)
            )

        if kind == "booklet":
            items = [
                BookletItem(
                    position=source.position,
                    stem_html=source.stem_html,
                    choices=source.choices,
                    image_path=source.image_path,
                    render_overrides=source.render_overrides,
                )
                for source in booklet_sources(session, exam)
            ]
            return write_booklet(
                items,
                out_dir / f"booklet_{exam.id}.docx",
                title=exam.name or None,
                # 設定画面の基準フォント(設計書 §14-10)。
                config=self.workspace.settings.writer_config(),
            )

        if kind == "crosswalk":
            return write_crosswalk(
                crosswalk_rows(session, exam),
                out_dir / f"crosswalk_{exam.id}.xlsx",
                exam_name=exam.name,
            )

        if kind == "report":
            stems = {s.position: s.stem_html for s in booklet_sources(session, exam)}
            return write_stats_report(
                report_rows(session, exam),
                out_dir / f"report_{exam.id}.xlsx",
                exam_name=exam.name,
                stem_texts=stems,
            )

        raise ValueError(f"知らない出力です: {kind}")
