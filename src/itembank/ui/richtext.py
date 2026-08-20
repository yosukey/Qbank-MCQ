"""HTML 断片と ``QTextDocument`` の相互変換、および書式ボタン付きの編集欄。

設計書 §14 の最後の一文が、この画面まわりの設計を決めている:

    QTextDocument が ``<strong>`` ``<i>`` ``<sup>`` ``<sub>`` を解釈するため、
    **保存形式をそのまま表示・編集に使える**

ただし ``QTextDocument.toHtml()`` は CSS 付きの完全な HTML 文書を返すため、そのまま
保存形式には使えない。そこで**文書モデルを走査して run 列を作り、``core.text`` の
``runs_to_html`` に渡す**。docx 取込と同じ関数を通るので、書式の正規化(隣接 run の
結合、ネスト順序)が取込側と一致する(設計書 §5.1-2, §5.3)。

設問は 1 段落として保存する。許可タグに改行はなく(設計書 §3.1)、日本語の本文は
段落をまたいでも語間に空白を要しないため、段落の境界は区切りなしで連結する。
編集欄側では Enter を無効にして段落を作らせない。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAction,
    QFont,
    QKeySequence,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextEdit, QToolButton, QVBoxLayout, QWidget

from ..core.text import ALLOWED_TAGS, Run, merge_runs, render_choice, runs_to_html

#: 書式ボタンは強調・イタリック・上付き・下付きの 4 つのみ(設計書 §14-2)。
FORMAT_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("strong", "強調", "Ctrl+B"),
    ("i", "斜体", "Ctrl+I"),
    ("sup", "上付", "Ctrl+Shift+P"),
    ("sub", "下付", "Ctrl+Shift+B"),
)


# ---------------------------------------------------------------------------
# 文書モデル ⇔ HTML 断片
# ---------------------------------------------------------------------------


def format_tags(fmt: QTextCharFormat) -> frozenset[str]:
    """文字書式を許可タグの集合に写す。**許可外の書式は捨てる**(設計書 §3.1)。"""
    tags: set[str] = set()
    if fmt.fontWeight() >= QFont.Weight.Bold:
        tags.add("strong")
    if fmt.fontItalic():
        tags.add("i")
    align = fmt.verticalAlignment()
    if align == QTextCharFormat.VerticalAlignment.AlignSuperScript:
        tags.add("sup")
    elif align == QTextCharFormat.VerticalAlignment.AlignSubScript:
        tags.add("sub")
    return frozenset(tags)


def tags_to_format(tags: frozenset[str]) -> QTextCharFormat:
    """許可タグの集合を文字書式に写す(``format_tags`` の逆)。"""
    fmt = QTextCharFormat()
    fmt.setFontWeight(QFont.Weight.Bold if "strong" in tags else QFont.Weight.Normal)
    fmt.setFontItalic("i" in tags)
    if "sup" in tags:
        fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
    elif "sub" in tags:
        fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
    else:
        fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignNormal)
    return fmt


def document_runs(document: QTextDocument) -> list[Run]:
    """文書を ``(テキスト, 書式集合)`` の並びにする。段落の境界は区切りなしで繋ぐ。"""
    runs: list[Run] = []
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.text():
                # U+2028(行区切り)は Shift+Enter で入る。保存形式には表せない。
                text = fragment.text().replace("\u2028", "")
                if text:
                    runs.append((text, format_tags(fragment.charFormat())))
            it += 1
        block = block.next()
    return merge_runs(runs)


def document_to_html(document: QTextDocument) -> str:
    """文書を保存形式の HTML 断片にする。"""
    return runs_to_html(document_runs(document))


def html_to_document(html: str, document: QTextDocument | None = None) -> QTextDocument:
    """HTML 断片を文書に流し込む。``document_to_html`` と往復する。

    ``QTextDocument.setHtml`` に任せず run 単位で組み立てる。setHtml は連続空白を
    HTML の規則で畳んでしまい、``胎生 3-4 週`` のような**意味のある空白**を壊す
    恐れがあるため(設計書 §7、実装計画 §11)。
    """
    from ..core.text import html_to_runs

    doc = document if document is not None else QTextDocument()
    doc.clear()
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    for text, tags in html_to_runs(html):
        cursor.insertText(text, tags_to_format(frozenset(tags)))
    cursor.endEditBlock()
    return doc


# ---------------------------------------------------------------------------
# 編集欄
# ---------------------------------------------------------------------------


class RichTextEdit(QTextEdit):
    """許可タグ 4 種だけを扱う 1 段落の編集欄。

    Enter は段落を作らないよう握りつぶす。貼り付けは書式を落として素のテキストで
    受ける(Word から色や下線が紛れ込むのを防ぐ。設計書 §3.1)。
    """

    htmlChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.textChanged.connect(lambda: self.htmlChanged.emit(self.fragment_html()))

    # -- 内容 ---------------------------------------------------------------
    def fragment_html(self) -> str:
        """保存形式の HTML 断片。"""
        return document_to_html(self.document())

    def set_fragment_html(self, html: str) -> None:
        was_blocked = self.blockSignals(True)
        html_to_document(html, self.document())
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.blockSignals(was_blocked)
        self.htmlChanged.emit(self.fragment_html())

    # -- 書式 ---------------------------------------------------------------
    def toggle_tag(self, tag: str) -> None:
        """選択範囲(なければこれから打つ文字)の書式を切り替える。"""
        if tag not in ALLOWED_TAGS:
            raise ValueError(f"許可されていないタグです: {tag}")
        cursor = self.textCursor()
        current = format_tags(cursor.charFormat())
        tags = set(current)
        if tag in tags:
            tags.discard(tag)
        else:
            tags.add(tag)
            # 上付きと下付きは同時に立てられない。
            if tag == "sup":
                tags.discard("sub")
            elif tag == "sub":
                tags.discard("sup")
        fmt = tags_to_format(frozenset(tags))
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        self.setCurrentCharFormat(fmt)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt の命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.ignore()
            return
        super().keyPressEvent(event)


class RichTextField(QWidget):
    """``RichTextEdit`` に 4 つの書式ボタンを添えたひとまとまり(設計書 §14-2)。"""

    htmlChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, height: int = 90) -> None:
        super().__init__(parent)
        self.edit = RichTextEdit(self)
        self.edit.setMinimumHeight(height)
        self.edit.htmlChanged.connect(self.htmlChanged)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.buttons: dict[str, QToolButton] = {}
        for tag, label, shortcut in FORMAT_BUTTONS:
            button = QToolButton(self)
            button.setText(label)
            button.setToolTip(f"<{tag}>  {shortcut}")
            button.clicked.connect(lambda _=False, t=tag: self.edit.toggle_tag(t))
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False, t=tag: self.edit.toggle_tag(t))
            self.edit.addAction(action)
            bar.addWidget(button)
            self.buttons[tag] = button
        bar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.edit)

    def fragment_html(self) -> str:
        return self.edit.fragment_html()

    def set_fragment_html(self, html: str) -> None:
        self.edit.set_fragment_html(html)


class KintouPreview(QLabel):
    """均等割の印字プレビュー(設計書 §14-2, §7)。

    保存形は空白を持たないが、冊子には ``横　紋`` と全角空白を挟んで印字される。
    編集中にどう印字されるかを併記しないと、この差が見えない。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)

    def show_choices(
        self, choice_htmls: list[str], overrides: list[str | None] | None = None
    ) -> None:
        overrides = list(overrides or [])
        overrides += [None] * (len(choice_htmls) - len(overrides))
        lines = []
        for label, html, override in zip("abcde", choice_htmls, overrides, strict=False):
            printed = render_choice(html, override)
            mark = " ←均等割" if printed != html else ""
            lines.append(f"{label}　{printed}{mark}")
        self.setText("印字プレビュー<br>" + "<br>".join(lines) if lines else "印字プレビュー")
