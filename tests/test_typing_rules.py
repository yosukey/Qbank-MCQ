"""``core.typing_rules`` のテスト(設計書 §4, §11)。"""

from __future__ import annotations

import pytest

from itembank.core.typing_rules import (
    TYPE_A,
    TYPE_X2,
    TYPE_X3,
    TYPE_X4,
    TYPE_XX,
    check_emphasis_rule,
    derive_item_type,
    derive_item_type_detail,
    is_negative,
    normalize_correct,
    validate_correct,
)


def blocking(issues: list) -> list[str]:
    return [i.code for i in issues if i.blocking]


def codes(issues: list) -> list[str]:
    return [i.code for i in issues]


# ---------------------------------------------------------------------------
# タイプ導出(設計書 §11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stem,expected",
    [
        ("エナメル質について正しいのはどれか。1つ選べ。", TYPE_A),
        ("正しいのはどれか。2つ選べ。", TYPE_X2),
        ("正しいのはどれか。3つ選べ。", TYPE_X3),
        ("正しいのはどれか。4つ選べ。", TYPE_X4),
        ("正しいものをすべて選べ。", TYPE_XX),
        ("正しいものを全て選べ。", TYPE_XX),
        ("正しいのはどれか。１つ選べ。", TYPE_A),  # 全角数字は NFKC で半角に
        ("正しいのはどれか。二つ選べ。", TYPE_X2),  # 漢数字
        ("正しいのはどれか。2つを選べ。", TYPE_X2),  # 「を」入り
    ],
)
def test_derive_item_type(stem: str, expected: str) -> None:
    assert derive_item_type(stem) == expected


def test_derive_item_type_sees_through_tags() -> None:
    """指示文言のパースは必ずタグ除去後(設計書 §3.2)。"""
    stem = "酸に溶け<strong>ない</strong>のはどれか。1つ選べ。"
    assert derive_item_type(stem) == TYPE_A


def test_five_choose_five_is_rejected() -> None:
    """設計書 §11: 「5つ選べ」は成立しない。"""
    d = derive_item_type_detail("正しいのはどれか。5つ選べ。")
    assert d.item_type is None
    assert "すべて選べ" in (d.reason or "")


def test_missing_instruction_is_reported() -> None:
    d = derive_item_type_detail("エナメル質について正しいのはどれか。")
    assert d.item_type is None
    assert d.reason


# ---------------------------------------------------------------------------
# 正答個数の検証(設計書 §11)
# ---------------------------------------------------------------------------


def test_xx_allows_four_correct_answers() -> None:
    """実装計画 §4 M2 の受入条件: 「すべて選べ・正答4個」(問23相当)。"""
    stem = "エナメル器に由来するものをすべて選べ。"
    assert derive_item_type(stem) == TYPE_XX
    assert validate_correct("abce", TYPE_XX) == []


def test_xx_allows_one_through_five() -> None:
    for correct in ["a", "ab", "abc", "abcd", "abcde"]:
        assert validate_correct(correct, TYPE_XX) == []


@pytest.mark.parametrize(
    "item_type,ok,ng",
    [(TYPE_A, "c", "ac"), (TYPE_X2, "ad", "a"), (TYPE_X3, "abe", "ab"), (TYPE_X4, "abce", "abcde")],
)
def test_fixed_count_types_block_mismatch(item_type: str, ok: str, ng: str) -> None:
    assert validate_correct(ok, item_type) == []
    assert "correct_count" in blocking(validate_correct(ng, item_type))


def test_empty_correct_blocks() -> None:
    assert "correct_empty" in blocking(validate_correct("", TYPE_A))


def test_bad_label_blocks() -> None:
    assert "correct_bad_label" in blocking(validate_correct("af", TYPE_X2))


def test_duplicate_label_blocks() -> None:
    assert "correct_duplicate" in blocking(validate_correct("aa", TYPE_X2))


def test_unknown_type_is_reported_not_ignored() -> None:
    assert "type_unknown" in blocking(validate_correct("a", None))


def test_normalize_correct_sorts_and_dedupes() -> None:
    assert normalize_correct("da") == "ad"
    assert normalize_correct("EDCBA") == "abcde"
    assert normalize_correct(" d a ") == "ad"


# ---------------------------------------------------------------------------
# 強調規則(設計書 §4)
# ---------------------------------------------------------------------------


def test_is_negative_uses_strong_only() -> None:
    assert is_negative("酸に溶け<strong>ない</strong>のはどれか。1つ選べ。")
    assert not is_negative("酸に溶けるのはどれか。1つ選べ。")


def test_missing_emphasis_is_warned_not_blocked() -> None:
    issues = check_emphasis_rule("酸に溶けないのはどれか。1つ選べ。", [])
    assert codes(issues) == ["emphasis_missing"]
    assert blocking(issues) == []  # 否定語リストが網羅的とは限らないためブロックしない


def test_emphasis_without_negative_word_is_warned() -> None:
    issues = check_emphasis_rule("酸に<strong>溶ける</strong>のはどれか。1つ選べ。", [])
    assert codes(issues) == ["emphasis_unexpected"]


def test_correct_negative_form_is_clean() -> None:
    assert check_emphasis_rule("酸に溶け<strong>ない</strong>のはどれか。1つ選べ。", []) == []


def test_emphasis_in_choice_is_a_rule_violation() -> None:
    """強調は設問文のみ。選択肢にあれば規則違反(設計書 §4 の表)。"""
    issues = check_emphasis_rule(
        "正しいのはどれか。1つ選べ。", ["象牙質", "<strong>エナメル質</strong>", "セメント質"]
    )
    assert codes(issues) == ["emphasis_in_choice"]  # 設問文自体は規則どおり
    assert issues[-1].context["index"] == 1
    assert blocking(issues) == []
