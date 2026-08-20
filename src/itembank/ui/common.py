"""画面まわりの小道具。表示の決まりごとをここに集める。

決まりごとは 2 つだけ:

- **一覧やフィルタに出す文字列はタグ除去後**(設計書 §3.2)。``plain()`` を通す
- **正答率は必ずタイプと併記**(設計書 §12)。``p_with_type()`` を通す
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.text import strip_tags
from ..core.typing_rules import ValidationIssue

#: 検証結果の見出し。ブロックと警告を混ぜて見せない(設計書 §9.2)。
BLOCK_PREFIX = "[ブロック]"
WARN_PREFIX = "[警告]"


def plain(html: str) -> str:
    """一覧・検索に使う素のテキスト(設計書 §3.2)。"""
    return strip_tags(html)


def p_with_type(p: float | None, item_type: str | None) -> str:
    """正答率の表示。**タイプを外して見せない**(設計書 §12, §13.1)。"""
    if p is None:
        return f"—({item_type or '?'})"
    return f"{p:.0%}({item_type or '?'})"


def rate(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def number(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def rich_label(html: str, parent: QWidget | None = None) -> QLabel:
    """許可タグ 4 種をそのまま描く表示用ラベル。"""
    label = QLabel(html, parent)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def issue_lines(issues: Sequence[ValidationIssue]) -> list[str]:
    """ブロックを先、警告を後に並べた表示行。"""
    blockers = [f"{BLOCK_PREFIX} {i.message}" for i in issues if i.blocking]
    warnings = [f"{WARN_PREFIX} {i.message}" for i in issues if not i.blocking]
    return blockers + warnings


def fill_issue_list(widget: QListWidget, issues: Sequence[ValidationIssue]) -> None:
    """検証結果をリストに流し込む。ブロックは赤く出す。"""
    widget.clear()
    for issue in issues:
        item = QListWidgetItem(f"{BLOCK_PREFIX if issue.blocking else WARN_PREFIX} {issue.message}")
        if issue.blocking:
            item.setForeground(Qt.GlobalColor.red)
        widget.addItem(item)
    if not issues:
        widget.addItem(QListWidgetItem("問題ありません"))


class IssueDialog(QDialog):
    """検証結果だけを見せる確認ダイアログ。"""

    def __init__(
        self, issues: Sequence[ValidationIssue], *, title: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.list = QListWidget(self)
        fill_issue_list(self.list, issues)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addWidget(buttons)
        self.resize(560, 320)
