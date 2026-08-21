"""``core.choiceset`` のテスト(設計書 §6)。"""

from __future__ import annotations

import pytest

from qbank_mcq.core.choiceset import (
    RELATION_CANDIDATE,
    RELATION_IDENTICAL,
    RELATION_NEAR,
    audit_tagless_duplicates,
    build_links,
    choice_set_signature,
    correct_to_item_nos,
    format_choice_order,
    identity_order,
    item_no_to_label,
    item_nos_to_correct,
    label_to_item_no,
    ordered_items,
    parse_choice_order,
    relation_for,
    resolve_choice_order,
    set_similarity,
    should_autolink,
    validate_items,
)

SET_A = ["エナメル質", "象牙質", "セメント質", "歯髄", "歯根膜"]


def test_signature_ignores_order() -> None:
    """順序をセットから外したので、並びが違うだけなら同じ署名(設計書 §6.1)。"""
    assert choice_set_signature(SET_A) == choice_set_signature(list(reversed(SET_A)))


def test_signature_differs_on_one_item() -> None:
    other = [*SET_A[:4], "歯肉"]
    assert choice_set_signature(SET_A) != choice_set_signature(other)


def test_signature_is_stable_hex() -> None:
    sig = choice_set_signature(SET_A)
    assert len(sig) == 64 and int(sig, 16) >= 0


def test_validate_items() -> None:
    assert validate_items(SET_A) == []
    assert validate_items(SET_A[:4])  # 5 項目でない
    assert validate_items([*SET_A[:4], "エナメル質"])  # 重複
    assert validate_items([*SET_A[:4], "  "])  # 空


# ---------------------------------------------------------------------------
# 近似セット(設計書 §6.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shared,relation,autolink",
    [
        (5, RELATION_IDENTICAL, False),
        (4, RELATION_NEAR, True),
        (3, RELATION_NEAR, True),
        (2, RELATION_CANDIDATE, False),
        (1, None, False),
        (0, None, False),
    ],
)
def test_relation_table(shared: int, relation: str | None, autolink: bool) -> None:
    assert relation_for(shared) == relation
    assert should_autolink(shared) is autolink


def test_set_similarity() -> None:
    assert set_similarity(SET_A, SET_A) == 5
    assert set_similarity(SET_A, [*SET_A[:4], "歯肉"]) == 4


def test_links_are_not_transitively_closed() -> None:
    """A〜B、B〜C でも A〜C は自動生成しない(設計書 §6.3)。"""
    a = ["1", "2", "3", "4", "5"]
    b = ["3", "4", "5", "6", "7"]  # a と 3 共通
    c = ["5", "6", "7", "8", "9"]  # b と 3 共通、a とは 1 共通
    links = build_links({1: a, 2: b, 3: c})
    pairs = {(x.set_a, x.set_b) for x in links}
    assert (1, 2) in pairs
    assert (2, 3) in pairs
    assert (1, 3) not in pairs


def test_links_carry_shared_count_and_relation() -> None:
    links = build_links({1: SET_A, 2: [*SET_A[:4], "歯肉"]})
    assert len(links) == 1
    assert links[0].shared == 4
    assert links[0].relation == RELATION_NEAR


def test_audit_finds_tagless_duplicates() -> None:
    """マークアップ付け忘れの保険(設計書 §6.2)。"""
    dupes = audit_tagless_duplicates(
        {1: ["<i>Streptococcus</i>", "象牙質"], 2: ["Streptococcus", "歯髄"]}
    )
    assert dupes == {"Streptococcus": [1, 2]}


def test_audit_ignores_identical_html() -> None:
    assert audit_tagless_duplicates({1: ["象牙質"], 2: ["象牙質"]}) == {}


# ---------------------------------------------------------------------------
# 並び順(設計書 §8 の choice_order)
# ---------------------------------------------------------------------------


def test_parse_choice_order() -> None:
    assert parse_choice_order("31524") == (3, 1, 5, 2, 4)
    assert identity_order() == "12345"


@pytest.mark.parametrize("bad", ["1234", "112345", "11234", "abcde", "123456"])
def test_parse_choice_order_rejects_non_permutation(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_choice_order(bad)


def test_format_choice_order_validates() -> None:
    assert format_choice_order([3, 1, 5, 2, 4]) == "31524"
    with pytest.raises(ValueError):
        format_choice_order([1, 1, 2, 3, 4])


def test_label_item_mapping() -> None:
    """'31524' = a←項目3, b←項目1, …(設計書 §8)。"""
    assert label_to_item_no("a", "31524") == 3
    assert label_to_item_no("b", "31524") == 1
    assert label_to_item_no("e", "31524") == 4
    assert item_no_to_label(3, "31524") == "a"
    assert item_no_to_label(4, "31524") == "e"


def test_ordered_items() -> None:
    items = {1: "い", 2: "ろ", 3: "は", 4: "に", 5: "ほ"}
    assert ordered_items(items, "31524") == [
        ("a", 3, "は"),
        ("b", 1, "い"),
        ("c", 5, "ほ"),
        ("d", 2, "ろ"),
        ("e", 4, "に"),
    ]


def test_correct_maps_to_stable_item_ids() -> None:
    """並び順が変わっても項目単位で追跡できる(設計書 §6.4-5)。"""
    assert correct_to_item_nos("ad", "31524") == (2, 3)
    assert item_nos_to_correct((2, 3), "31524") == "ad"


def test_correct_item_ids_survive_reshuffle() -> None:
    """同じ項目集合を別の並びで出しても、項目 ID での正答は同じ。"""
    first = correct_to_item_nos("ad", "12345")  # a←1, d←4 → (1, 4)
    second_order = "41253"  # a←4, b←1, ...
    assert item_nos_to_correct(first, second_order) == "ab"


def test_resolve_choice_order_from_printed_sequence() -> None:
    items = {1: "い", 2: "ろ", 3: "は", 4: "に", 5: "ほ"}
    assert resolve_choice_order(items, ["は", "い", "ほ", "ろ", "に"]) == "31524"


def test_resolve_choice_order_rejects_foreign_item() -> None:
    items = {1: "い", 2: "ろ", 3: "は", 4: "に", 5: "ほ"}
    with pytest.raises(ValueError):
        resolve_choice_order(items, ["は", "い", "ほ", "ろ", "へ"])
