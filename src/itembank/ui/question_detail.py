"""問題詳細(設計書 §14-3)。

    版履歴、**派生系譜**、正答率・識別係数の推移、選択パターン度数の可視化
    (周辺マーク率、**誤答パターン上位5件**、部分正答分布、無回答率)、
    **「この問題を改訂する」導線**

最後の導線が §2.6 の要点である。フラグが付いた問題から改訂に直接進めることで、
「分析結果が作問に還る経路」が 1 画面で閉じる。

材料は ``core.reporting.question_history`` が集める。画面は並べるだけにして、
集計を UI 側に書かない(実装計画 §5)。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.choiceset import ordered_items
from ..core.db import ChoiceSet, Question
from ..core.reporting import Appearance, QuestionHistory, question_history
from ..core.typing_rules import LABELS, REQUIRED_COUNT, derive_item_type_detail, is_negative
from .charts import ChartCanvas
from .common import number, p_with_type, plain, rate, rich_label


class QuestionDetail(QDialog):
    """1 問の履歴と実績。"""

    #: 「この問題を改訂する」(設計書 §2.6)。question_id を渡す。
    reviseRequested = Signal(int)

    def __init__(self, workspace, question_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.question_id = question_id
        self.history: QuestionHistory = question_history(workspace.session, question_id)

        self.setWindowTitle(f"問題 {question_id} の詳細")
        self.resize(920, 780)
        self._build()
        self.show_appearance(self.history.appearances[-1] if self.history.appearances else None)

    # -- 組み立て -----------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_summary())

        middle = QHBoxLayout()
        middle.addWidget(self._build_versions(), 1)
        middle.addWidget(self._build_lineage(), 1)
        layout.addLayout(middle)

        # 図に十分な高さを渡す。表に伸び代を持たせると軸が潰れて何も読めなくなる。
        layout.addWidget(self._build_appearances(), 0)
        layout.addWidget(self._build_charts(), 1)
        layout.addWidget(self._build_buttons())

    def _build_summary(self) -> QGroupBox:
        box = QGroupBox("設問", self)
        layout = QVBoxLayout(box)
        version = self.history.latest
        stem = version.stem_html if version else ""
        derivation = derive_item_type_detail(stem)

        layout.addWidget(rich_label(stem or "(版がありません)", box))
        if version is not None:
            cset = self.workspace.session.get(ChoiceSet, version.choice_set_id)
            printed = ordered_items(cset.items_by_no(), version.choice_order) if cset else []
            for label, _, html in printed:
                mark = "✔" if label in version.correct else "　"
                layout.addWidget(rich_label(f"{mark} {label}　{html}", box))

        note = (
            f"タイプ {derivation.item_type or '?'}"
            f" / {'否定形' if is_negative(stem) else '肯定形'}"
            f" / 正答 {version.correct if version else '—'}"
            f" / 状態 {self.history.status}"
        )
        layout.addWidget(QLabel(note, box))
        return box

    def _build_versions(self) -> QGroupBox:
        box = QGroupBox("版履歴", self)
        layout = QVBoxLayout(box)
        table = QTableWidget(len(self.history.versions), 4, box)
        table.setMaximumHeight(150)
        table.setHorizontalHeaderLabels(["版", "正答", "セット", "作成"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, version in enumerate(self.history.versions):
            for column, text in enumerate(
                (
                    f"v{version.version_no}",
                    version.correct,
                    str(version.choice_set_id),
                    (version.created_at or "")[:10],
                )
            ):
                table.setItem(row, column, QTableWidgetItem(text))
        table.resizeColumnsToContents()
        layout.addWidget(table)
        return box

    def _build_lineage(self) -> QGroupBox:
        """派生系譜(設計書 §2.3)。似た問題の増殖を目に見えるようにする。"""
        box = QGroupBox("派生系譜", self)
        layout = QVBoxLayout(box)

        parent = self.history.parent
        if parent is None:
            layout.addWidget(QLabel("派生元はありません", box))
        else:
            layout.addWidget(
                QLabel(f"問題 {parent.question_id} の v{parent.version_no} から派生", box)
            )
            layout.addWidget(rich_label(f"　{plain(parent.stem_html)[:60]}", box))

        children = self.history.children
        layout.addWidget(QLabel(f"この問題から {len(children)} 問が派生", box))
        for child in children[:6]:
            version = child.latest_version
            text = plain(version.stem_html)[:50] if version else ""
            layout.addWidget(QLabel(f"　・問題 {child.id}: {text}", box))
        layout.addStretch(1)
        return box

    def _build_appearances(self) -> QGroupBox:
        box = QGroupBox("出題実績(行を選ぶとグラフが切り替わる)", self)
        layout = QVBoxLayout(box)

        headers = ["試験", "日付", "問", "版", "N", "正答率", "識別係数", "無回答", "フラグ"]
        self.appearance_table = QTableWidget(len(self.history.appearances), len(headers), box)
        self.appearance_table.setHorizontalHeaderLabels(headers)
        self.appearance_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.appearance_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.appearance_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.appearance_table.setMaximumHeight(160)

        item_type = derive_item_type_detail(
            self.history.latest.stem_html if self.history.latest else ""
        ).item_type
        for row, appearance in enumerate(self.history.appearances):
            cells = [
                appearance.exam_name or str(appearance.exam_id),
                appearance.exam_date or "—",
                str(appearance.position),
                f"v{appearance.version_no}",
                str(appearance.n or "—"),
                p_with_type(appearance.p, item_type),
                number(appearance.disc),
                rate(appearance.blank_rate),
                "、".join(appearance.flags),
            ]
            for column, text in enumerate(cells):
                self.appearance_table.setItem(row, column, QTableWidgetItem(text))
        self.appearance_table.resizeColumnsToContents()
        self.appearance_table.itemSelectionChanged.connect(self._on_appearance_selected)
        layout.addWidget(self.appearance_table)
        return box

    def _build_charts(self) -> QGroupBox:
        box = QGroupBox("選択パターンの可視化(設計書 §14-3)", self)
        grid = QGridLayout(box)
        self.mark_chart = ChartCanvas(box)
        self.wrong_chart = ChartCanvas(box)
        self.partial_chart = ChartCanvas(box)
        self.trend_chart = ChartCanvas(box)
        grid.addWidget(self.mark_chart, 0, 0)
        grid.addWidget(self.wrong_chart, 0, 1)
        grid.addWidget(self.partial_chart, 1, 0)
        grid.addWidget(self.trend_chart, 1, 1)
        return box

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(self)
        revise = QPushButton("この問題を改訂する", self)
        revise.setToolTip("フラグ → 改訂 → 次回出題の流れを 1 画面で閉じる(設計書 §2.6)")
        revise.clicked.connect(self._request_revision)
        buttons.addButton(revise, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        return buttons

    # -- 表示 ---------------------------------------------------------------
    def _on_appearance_selected(self) -> None:
        rows = {index.row() for index in self.appearance_table.selectedIndexes()}
        if not rows:
            return
        self.show_appearance(self.history.appearances[min(rows)])

    def show_appearance(self, appearance: Appearance | None) -> None:
        """1 回の出題ぶんのグラフを描く。推移だけは全出題を通して描く。"""
        self._draw_trend()

        if appearance is None or not appearance.has_stats:
            for canvas in (self.mark_chart, self.wrong_chart, self.partial_chart):
                canvas.show_message("統計がまだありません")
            return

        self.mark_chart.draw_mark_rates(appearance.sel, appearance.correct_asked)
        self.wrong_chart.draw_top_wrong(appearance.top_wrong())

        version = next(
            (v for v in self.history.versions if v.id == appearance.qversion_id),
            self.history.latest,
        )
        item_type = derive_item_type_detail(version.stem_html).item_type if version else None
        required = REQUIRED_COUNT.get(item_type or "") or len(appearance.correct_asked)
        self.partial_chart.draw_partial(appearance.partial(), required)

    def _draw_trend(self) -> None:
        with_stats = self.history.with_stats()
        labels = [f"{a.year or a.exam_id}\nv{a.version_no}" for a in with_stats]
        breaks = [
            i
            for i in range(1, len(with_stats))
            if with_stats[i].version_no != with_stats[i - 1].version_no
        ]
        self.trend_chart.draw_trend(
            labels,
            [a.p for a in with_stats],
            [a.disc for a in with_stats],
            version_breaks=breaks,
        )

    def _request_revision(self) -> None:
        self.reviseRequested.emit(self.question_id)
        self.accept()


def format_correct_marks(version, choice_set: ChoiceSet) -> list[str]:
    """印字順に「正答かどうか」を並べた表示用の記号列(一覧のツールチップ用)。"""
    printed = ordered_items(choice_set.items_by_no(), version.choice_order)
    return [
        f"{'✔' if label in version.correct else '　'}{label}"
        for label, _, _ in printed
        if label in LABELS
    ]


def question_title(session, question_id: int) -> str:
    """ウィンドウ見出し用の短い説明。"""
    question = session.get(Question, question_id)
    version = question.latest_version if question else None
    return plain(version.stem_html)[:40] if version else f"問題 {question_id}"
