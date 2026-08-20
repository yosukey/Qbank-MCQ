"""選択肢アイテム(設計書 §14-5)。用語単位の実績一覧(§6.5)。

    「レッチウス条」
      登場回数: 8回(うち正答 3回 / 錯乱肢 5回)
      正答であるときの正答率: 中央値 52%
      錯乱肢であるときのマーク率: 中央値 31%  ← 強力な錯乱肢
      最も混同される相手: 「エブネル線」(同時出題6回中5回で誤選択の主軸)

「よく効く錯乱肢」と「誰も選ばない死んだ選択肢」を用語レベルで把握するための画面。
集計は ``core.stats.aggregate_item_performance`` が行い、ここは並べるだけにする。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.reporting import choice_item_appearances
from ..core.stats import DEFAULT_THRESHOLDS, ItemPerformance, aggregate_item_performance
from .common import plain, rate

HEADERS = (
    "用語",
    "登場",
    "正答として",
    "錯乱肢として",
    "正答時の正答率(中央値)",
    "錯乱肢時のマーク率(中央値)",
    "最も混同される相手",
    "評価",
)


def verdict(performance: ItemPerformance, dead_rate: float) -> str:
    """用語レベルの評価。閾値は設定画面のもの(設計書 §12, §14-10)。"""
    median = performance.median_mark_rate_when_distractor
    if performance.as_distractor == 0:
        return "錯乱肢としての実績なし"
    if median is None:
        return "—"
    if median < dead_rate:
        return "死んだ選択肢"
    if median >= 0.25:
        return "強力な錯乱肢"
    return "ふつう"


class ItemView(QWidget):
    """選択肢アイテムのタブ。"""

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.rows: list[ItemPerformance] = []

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.keyword = QLineEdit(self)
        self.keyword.setPlaceholderText("用語で絞る(タグ除去後の文字列)")
        self.keyword.textChanged.connect(self._apply_filter)
        top.addWidget(self.keyword, 1)

        self.only_distractors = QCheckBox("錯乱肢として出た用語だけ", self)
        self.only_distractors.stateChanged.connect(self._apply_filter)
        top.addWidget(self.only_distractors)
        layout.addLayout(top)

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(list(HEADERS))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)
        self.refresh()

    def refresh(self) -> None:
        self.rows = aggregate_item_performance(choice_item_appearances(self.workspace.session))
        self.rows.sort(key=lambda p: (-p.appearances, plain(p.text_html)))
        self._apply_filter()

    def visible_rows(self) -> list[ItemPerformance]:
        keyword = self.keyword.text().strip()
        rows = self.rows
        if keyword:
            rows = [p for p in rows if keyword in plain(p.text_html)]
        if self.only_distractors.isChecked():
            rows = [p for p in rows if p.as_distractor > 0]
        return rows

    def _apply_filter(self) -> None:
        rows = self.visible_rows()
        dead_rate = self.workspace.settings.thresholds.dead_distractor_rate

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row, performance in enumerate(rows):
            cells = [
                plain(performance.text_html),
                str(performance.appearances),
                str(performance.as_correct),
                str(performance.as_distractor),
                rate(performance.median_p_when_correct),
                rate(performance.median_mark_rate_when_distractor),
                self._confusion_text(performance),
                verdict(performance, dead_rate),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, performance.text_html)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)

        if not self.rows:
            self.status.setText(
                "統計を取り込んだ試験がまだありません(用語の実績は統計から作られます)"
            )
        else:
            self.status.setText(
                f"{len(rows)} / {len(self.rows)} 用語"
                f"(死んだ選択肢の閾値 {DEFAULT_THRESHOLDS.dead_distractor_rate:.0%} → "
                f"設定は {dead_rate:.0%})"
            )

    def _confusion_text(self, performance: ItemPerformance) -> str:
        if not performance.top_confused_with:
            return "—"
        return (
            f"{plain(performance.top_confused_with)}"
            f"(同時出題 {performance.co_occurrences} 回中 {performance.top_confused_count} 回)"
        )
