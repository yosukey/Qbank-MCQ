"""HTML 断片 ⇔ QTextDocument(``ui.richtext``)。

設計書 §14 は「QTextDocument が許可タグ 4 種を解釈するので保存形式をそのまま
表示・編集に使える」とする。それが**本当に往復するか**をここで押さえる。
壊れやすいのは書式そのものより空白で(実装計画 §11)、``Krause 小体``
``胎生 3-4 週`` が編集欄を通っただけで変わらないことを明示的に見る。
"""

from __future__ import annotations

import pytest

from itembank.core.text import normalize_choice

pytest.importorskip("PySide6")

from itembank.ui.richtext import (  # noqa: E402 - importorskip の後に読む
    RichTextEdit,
    RichTextField,
    document_to_html,
    format_tags,
    html_to_document,
    tags_to_format,
)

ROUNDTRIP_CASES = [
    "酸に溶け<strong>ない</strong>のはどれか。1つ選べ。",
    "エナメル質の説明で正しいのはどれか。すべて選べ。",
    "H<sub>2</sub>O",
    "Ca<sup>2+</sup>",
    "<i>Streptococcus mutans</i>",
    "<strong><i>強調かつ斜体</i></strong>",
    "前<strong>後</strong>で挟む",
    "",
]

#: 空白を保つべき語(実装計画 §11: 一律削除は用語を壊す)。
SPACED_TERMS = ["Krause 小体", "滑膜 A 型細胞", "胎生 3-4 週"]


@pytest.mark.parametrize("html", ROUNDTRIP_CASES)
def test_roundtrip(qapp, html: str) -> None:
    assert document_to_html(html_to_document(html)) == html


@pytest.mark.parametrize("term", SPACED_TERMS)
def test_spaced_terms_survive_the_editor(qapp, term: str) -> None:
    """編集欄を通しただけで用語が詰まってはいけない。"""
    edit = RichTextEdit()
    edit.set_fragment_html(term)
    assert edit.fragment_html() == term
    # 保存経路(正規化)を通しても変わらない。
    assert normalize_choice(edit.fragment_html()) == term


def test_escaping_survives(qapp) -> None:
    """``＜以上 50 設問＞`` のような山括弧をタグと取り違えない(``core.text`` と同じ規約)。"""
    html = "a &lt; b &amp; c"
    doc = html_to_document(html)
    assert doc.toPlainText() == "a < b & c"
    assert document_to_html(doc) == html


def test_unsupported_formatting_is_dropped(qapp) -> None:
    """下線や色は保存形式に無い(設計書 §3.1)。文書側で付いていても捨てる。"""
    from PySide6.QtGui import QTextCharFormat, QTextCursor

    doc = html_to_document("下線付き")
    cursor = QTextCursor(doc)
    cursor.select(QTextCursor.SelectionType.Document)
    fmt = QTextCharFormat()
    fmt.setFontUnderline(True)
    cursor.mergeCharFormat(fmt)

    assert document_to_html(doc) == "下線付き"


def test_adjacent_same_format_runs_merge(qapp) -> None:
    """書式が同じ隣接 run は結合される(設計書 §5.1-2 と同じ規約)。"""
    from PySide6.QtGui import QTextCursor

    from itembank.ui.richtext import tags_to_format as fmt_of

    doc = html_to_document("")
    cursor = QTextCursor(doc)
    cursor.insertText("Strep", fmt_of(frozenset({"i"})))
    cursor.insertText("tococcus", fmt_of(frozenset({"i"})))
    assert document_to_html(doc) == "<i>Streptococcus</i>"


def test_format_tags_roundtrip(qapp) -> None:
    for tags in (frozenset(), frozenset({"strong"}), frozenset({"i", "sup"})):
        assert format_tags(tags_to_format(tags)) == tags


def test_toggle_tag_applies_to_selection(qapp) -> None:
    from PySide6.QtGui import QTextCursor

    edit = RichTextEdit()
    edit.set_fragment_html("酸に溶けないのはどれか。")
    cursor = edit.textCursor()
    cursor.setPosition(len("酸に溶け"))
    cursor.setPosition(len("酸に溶けない"), QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)
    edit.toggle_tag("strong")

    assert edit.fragment_html() == "酸に溶け<strong>ない</strong>のはどれか。"

    edit.setTextCursor(cursor)
    edit.toggle_tag("strong")
    assert edit.fragment_html() == "酸に溶けないのはどれか。"


def test_sup_and_sub_are_exclusive(qapp) -> None:
    from PySide6.QtGui import QTextCursor

    edit = RichTextEdit()
    edit.set_fragment_html("Ca2+")
    cursor = edit.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)
    edit.toggle_tag("sub")
    edit.setTextCursor(cursor)
    edit.toggle_tag("sup")

    assert edit.fragment_html() == "Ca<sup>2+</sup>"


def test_toggle_rejects_unknown_tag(qapp) -> None:
    edit = RichTextEdit()
    with pytest.raises(ValueError):
        edit.toggle_tag("u")


def test_field_has_exactly_four_buttons(qapp) -> None:
    """書式ボタンは 4 つのみ(設計書 §14-2)。"""
    field = RichTextField()
    assert sorted(field.buttons) == ["i", "strong", "sub", "sup"]


def test_html_changed_signal(qapp) -> None:
    seen: list[str] = []
    field = RichTextField()
    field.htmlChanged.connect(seen.append)
    field.set_fragment_html("かたち")
    assert seen[-1] == "かたち"
