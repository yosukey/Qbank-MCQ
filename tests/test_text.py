"""``core.text`` のテスト。

実装計画 §6 の方針どおり、正常系より**境界と反例**を厚くする。とくに
「均等割で削ってはいけない例」は実装計画 §4 M2 の受入条件に明記されている。
"""

from __future__ import annotations

import pytest

from itembank.core.text import (
    escape_text,
    has_tag,
    html_to_runs,
    is_kintou,
    merge_runs,
    normalize_choice,
    normalize_stem,
    render_choice,
    runs_to_html,
    sanitize_html,
    strip_tags,
)

F = frozenset


# ---------------------------------------------------------------------------
# 均等割(設計書 §7)
# ---------------------------------------------------------------------------

#: 設計書 §7 が挙げる均等割の実例。全角空白を挟んだ日本語 2 文字。
KINTOU_CASES = ["横　紋", "死　帯", "頰　骨", "導　管", "歯　堤", "乳　腺"]

#: **一律に空白を削ると壊れる**例。実装計画 §4 M2 の受入条件。
MUST_NOT_CHANGE = [
    "Krause 小体",
    "Merkel 盤",
    "滑膜 A 型細胞",
    "胎生 3-4 週",
    "epithelial pearl",
]


@pytest.mark.parametrize("src", KINTOU_CASES)
def test_kintou_is_removed(src: str) -> None:
    got = normalize_choice(src)
    assert " " not in got and "　" not in got
    assert len(got) == 2


@pytest.mark.parametrize("src", MUST_NOT_CHANGE)
def test_non_kintou_is_untouched(src: str) -> None:
    """ラテン文字を含むもの・3 文字以上のものは一切変更されない。"""
    assert normalize_choice(src) == src


def test_kintou_accepts_halfwidth_space_too() -> None:
    """NFKC は U+3000 を半角空白に変える。規則が両方を許容するので順序に依存しない。"""
    assert normalize_choice("横 紋") == "横紋"
    assert normalize_choice("横　紋") == "横紋"
    assert is_kintou("横 紋") and is_kintou("横　紋")


def test_kintou_needs_the_whole_string() -> None:
    """3 文字目があれば均等割ではない。"""
    assert not is_kintou("横　紋筋")
    assert normalize_choice("横　紋筋") == "横 紋筋"  # NFKC で U+3000 が半角になるだけ


def test_render_restores_kintou() -> None:
    assert render_choice("横紋") == "横　紋"
    assert render_choice("Krause 小体") == "Krause 小体"
    assert render_choice("エナメル質") == "エナメル質"  # 5 文字なので対象外


def test_render_override_wins() -> None:
    assert render_choice("横紋", "横／紋") == "横／紋"


def test_kintou_roundtrip() -> None:
    for src in KINTOU_CASES:
        assert render_choice(normalize_choice(src)) == src


# ---------------------------------------------------------------------------
# タグ処理(設計書 §3.1)
# ---------------------------------------------------------------------------


def test_only_four_tags_survive() -> None:
    res = sanitize_html('<p class="x">A<b>B</b><i>C</i><span>D</span><sup>2</sup></p>')
    assert res.html == "AB<i>C</i>D<sup>2</sup>"
    assert any("<p>" in r for r in res.removals)
    assert any("<b>" in r for r in res.removals)


def test_attributes_are_dropped_but_content_kept() -> None:
    res = sanitize_html('<i lang="la">Streptococcus</i>')
    assert res.html == "<i>Streptococcus</i>"
    assert any("属性を除去" in r for r in res.removals)


def test_unclosed_tag_is_completed() -> None:
    res = sanitize_html("酸に溶け<strong>ない")
    assert res.html == "酸に溶け<strong>ない</strong>"


def test_crossed_nesting_is_repaired() -> None:
    assert sanitize_html("<i><sup>x</i></sup>").html == "<i><sup>x</sup></i>"


def test_escaping_is_idempotent() -> None:
    once = sanitize_html("a & b < c").html
    assert once == "a &amp; b &lt; c"
    assert sanitize_html(once).html == once


def test_nfkc_never_creates_tags_from_fullwidth_brackets() -> None:
    """全角 ``＜`` を NFKC で半角にしてしまうと本文がタグになる。テキストのみ正規化する。"""
    got = normalize_stem("＜以上 50 設問＞")
    assert got == "&lt;以上 50 設問&gt;"
    assert strip_tags(got) == "<以上 50 設問>"


def test_strip_tags_sees_through_markup() -> None:
    """否定形はタグが語中に入る。素朴な文字列一致では引っかからない(設計書 §3.2)。"""
    html = "酸に溶け<strong>ない</strong>のはどれか。1つ選べ。"
    assert "溶けない" not in html
    assert "溶けない" in strip_tags(html)


def test_empty_and_adjacent_tags_collapse() -> None:
    assert sanitize_html("<i></i>abc").html == "abc"
    assert sanitize_html("<i>Strep</i><i>tococcus</i>").html == "<i>Streptococcus</i>"


def test_adjacent_tags_across_space_are_not_merged() -> None:
    """空白は元の docx で非イタリック。往復で書式を変えないため結合しない。"""
    assert sanitize_html("<i>a</i> <i>b</i>").html == "<i>a</i> <i>b</i>"


def test_has_tag_rejects_unknown_tag() -> None:
    assert has_tag("<strong>x</strong>", "strong")
    assert not has_tag("<i>x</i>", "strong")
    with pytest.raises(ValueError):
        has_tag("<b>x</b>", "b")


def test_escape_text() -> None:
    assert escape_text("a&b<c>d") == "a&amp;b&lt;c&gt;d"


# ---------------------------------------------------------------------------
# run と HTML(設計書 §5.1-2, §5.3)
# ---------------------------------------------------------------------------


def test_merge_runs_joins_same_format() -> None:
    """1 つの語が複数 run に割れているのを先に結合する。"""
    runs = [("Strep", F({"i"})), ("tococcus", F({"i"})), (" 属", F())]
    assert merge_runs(runs) == [("Streptococcus", F({"i"})), (" 属", F())]


def test_merge_runs_drops_empty() -> None:
    assert merge_runs([("", F()), ("a", F())]) == [("a", F())]


def test_runs_to_html_without_merge_would_split() -> None:
    runs = [("Strep", F({"i"})), ("tococcus", F({"i"}))]
    assert runs_to_html(runs) == "<i>Streptococcus</i>"


def test_runs_to_html_nesting_is_canonical() -> None:
    a = runs_to_html([("x", F({"i", "strong"}))])
    b = runs_to_html([("x", F({"strong", "i"}))])
    assert a == b == "<strong><i>x</i></strong>"


def test_runs_html_roundtrip() -> None:
    for html in [
        "H<sub>2</sub>O",
        "Ca<sup>2+</sup>",
        "酸に溶け<strong>ない</strong>のはどれか。",
        "<i>Streptococcus</i> mutans",
        "a &amp; b",
        "<strong><i>x</i></strong>y",
    ]:
        assert runs_to_html(html_to_runs(html)) == html


def test_runs_to_html_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        runs_to_html([("x", F({"u"}))])
