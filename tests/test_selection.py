"""選定エンジンのテスト(設計書 §13.1)。"""

from __future__ import annotations

from qbank_mcq.core.selection import (
    Candidate,
    SelectionConditions,
    assign_positions,
    eligible,
    select_candidates,
)
from qbank_mcq.core.stats import FLAG_NEGATIVE_DISC
from qbank_mcq.core.validate import ExamLimits

A_STEM = "正しいのはどれか。1つ選べ。"
X2_STEM = "正しいのはどれか。2つ選べ。"
NEG_STEM = "正しく<strong>ない</strong>のはどれか。1つ選べ。"


def cand(qid: int, **kw) -> Candidate:
    defaults = dict(
        qversion_id=qid,
        stem_html=A_STEM,
        correct="a",
        choice_set_id=qid,
        p=0.7,
        disc=0.3,
    )
    defaults.update(kw)
    return Candidate(question_id=qid, **defaults)


def ids(result) -> list[int]:
    return [c.question_id for c in result.selected]


# ---------------------------------------------------------------------------
# 絞り込み
# ---------------------------------------------------------------------------


def test_draft_and_retired_are_excluded() -> None:
    pool = [cand(1), cand(2, status="draft"), cand(3, status="retired")]
    kept, excluded = eligible(pool, SelectionConditions(total=5))
    assert [c.question_id for c in kept] == [1]
    assert excluded[2].startswith("status=")


def test_negative_disc_is_excluded_automatically() -> None:
    """正答設定ミス・二義性の疑い。まず点検すべきで出題には使わない(設計書 §12, §13.1)。"""
    pool = [cand(1), cand(2, flags=frozenset({FLAG_NEGATIVE_DISC}))]
    kept, excluded = eligible(pool, SelectionConditions(total=5))
    assert [c.question_id for c in kept] == [1]
    assert excluded[2] == "negative_disc"


def test_negative_disc_exclusion_can_be_turned_off() -> None:
    pool = [cand(1, flags=frozenset({FLAG_NEGATIVE_DISC}))]
    kept, _ = eligible(pool, SelectionConditions(total=5, exclude_negative_disc=False))
    assert len(kept) == 1


def test_min_disc_filters_but_spares_new_items() -> None:
    """統計のない問題に識別係数の下限は適用しない。"""
    pool = [cand(1, disc=0.05), cand(2, disc=0.4), cand(3, disc=None, p=None)]
    kept, _ = eligible(pool, SelectionConditions(total=5, min_disc=0.2))
    assert [c.question_id for c in kept] == [2, 3]


def test_recent_years_are_excluded() -> None:
    pool = [cand(1, last_exam_year=2025), cand(2, last_exam_year=2022), cand(3)]
    kept, _ = eligible(
        pool, SelectionConditions(total=5, exclude_recent_years=2, current_year=2026)
    )
    assert [c.question_id for c in kept] == [2, 3]


def test_p_range_is_off_by_default() -> None:
    """正答率レンジ絞り込みは任意・既定オフ(設計書 §13.1)。"""
    pool = [cand(1, p=0.1), cand(2, p=0.95)]
    kept, _ = eligible(pool, SelectionConditions(total=5))
    assert len(kept) == 2
    kept2, _ = eligible(pool, SelectionConditions(total=5, p_range=(0.3, 0.9)))
    assert kept2 == []


def test_questions_without_a_derivable_type_are_excluded() -> None:
    pool = [cand(1, stem_html="正しいのはどれか。")]
    kept, excluded = eligible(pool, SelectionConditions(total=5))
    assert kept == []
    assert "タイプ" in excluded[1]


# ---------------------------------------------------------------------------
# 選定
# ---------------------------------------------------------------------------


def test_least_used_questions_come_first() -> None:
    pool = [cand(1, times_used=5), cand(2, times_used=0), cand(3, times_used=2)]
    assert ids(select_candidates(pool, SelectionConditions(total=2))) == [2, 3]


def test_selection_is_deterministic() -> None:
    pool = [cand(i) for i in range(1, 11)]
    conditions = SelectionConditions(total=4)
    assert ids(select_candidates(pool, conditions)) == ids(select_candidates(pool, conditions))


def test_type_distribution_is_respected() -> None:
    pool = [cand(i, stem_html=A_STEM) for i in range(1, 6)]
    pool += [cand(i, stem_html=X2_STEM, correct="ab") for i in range(6, 11)]
    result = select_candidates(
        pool, SelectionConditions(total=5, type_distribution={"A": 3, "X2": 2})
    )
    counts = {t: len(g) for t, g in result.by_type.items()}
    assert counts == {"A": 3, "X2": 2}


def test_unmet_type_quota_is_reported_not_silently_dropped() -> None:
    pool = [cand(i, stem_html=A_STEM) for i in range(1, 4)]
    result = select_candidates(
        pool, SelectionConditions(total=5, type_distribution={"A": 2, "X2": 3})
    )
    assert len(result.selected) == 2
    assert any("X2" in m for m in result.unmet)


def test_set_group_limit_caps_similar_sets() -> None:
    """同一セットおよび近似セットからの出題上限(既定 2 問)。"""
    pool = [cand(1, choice_set_id=1), cand(2, choice_set_id=2), cand(3, choice_set_id=1)]
    result = select_candidates(pool, SelectionConditions(total=3), set_links={1: {2}, 2: {1}})
    assert len(result.selected) == 2


def test_set_group_limit_is_configurable() -> None:
    pool = [cand(i, choice_set_id=1) for i in range(1, 4)]
    result = select_candidates(
        pool, SelectionConditions(total=3, limits=ExamLimits(max_per_set_group=3))
    )
    assert len(result.selected) == 3


def test_derived_questions_are_never_selected_together() -> None:
    """派生関係にある問題の同時出題禁止(設計書 §13.1)。"""
    pool = [cand(1), cand(2), cand(3)]
    result = select_candidates(
        pool,
        SelectionConditions(total=3),
        derivation_families={1: {1, 2}, 2: {1, 2}, 3: {3}},
    )
    assert set(ids(result)) == {1, 3}


def test_negative_limit_is_respected() -> None:
    pool = [cand(i, stem_html=NEG_STEM) for i in range(1, 5)]
    pool += [cand(i) for i in range(5, 9)]
    result = select_candidates(
        pool, SelectionConditions(total=6, limits=ExamLimits(max_negative=2))
    )
    assert sum(1 for c in result.selected if c.negative) == 2
    assert len(result.selected) == 6


def test_new_item_ratio_is_honoured() -> None:
    """新作(統計なし)問題の混入率(設計書 §13.1)。"""
    pool = [cand(i, p=None, disc=None) for i in range(1, 6)]
    pool += [cand(i) for i in range(6, 16)]
    result = select_candidates(pool, SelectionConditions(total=10, new_item_ratio=0.2))
    assert sum(1 for c in result.selected if c.is_new) == 2
    assert len(result.selected) == 10


def test_new_item_shortfall_is_reported() -> None:
    pool = [cand(i) for i in range(1, 11)]  # 新作なし
    result = select_candidates(pool, SelectionConditions(total=10, new_item_ratio=0.2))
    assert any("新作" in m for m in result.unmet)


def test_tag_distribution_is_respected() -> None:
    pool = [cand(i, tags=frozenset({"発生"})) for i in range(1, 4)]
    pool += [cand(i, tags=frozenset({"硬組織"})) for i in range(4, 7)]
    result = select_candidates(
        pool, SelectionConditions(total=3, tag_distribution={"発生": 2, "硬組織": 1})
    )
    tags = [sorted(c.tags)[0] for c in result.selected]
    assert tags.count("発生") == 2 and tags.count("硬組織") == 1


def test_shortfall_is_reported_when_the_bank_is_too_small() -> None:
    result = select_candidates([cand(1), cand(2)], SelectionConditions(total=10))
    assert len(result.selected) == 2
    assert any("10 問の指定" in m for m in result.unmet)


# ---------------------------------------------------------------------------
# 表示と出題番号
# ---------------------------------------------------------------------------


def test_label_always_pairs_p_with_the_type() -> None:
    """正答率は必ずタイプと併記する(設計書 §12, §13.1)。"""
    assert cand(1, p=0.41, stem_html=X2_STEM, correct="ab").label().startswith("正答率41%(X2)")
    assert "新作(A)" in cand(2, p=None, disc=None).label()
    assert "否定形" in cand(3, stem_html=NEG_STEM).label()


def test_assign_positions_groups_by_type() -> None:
    pool = [cand(1, stem_html=X2_STEM, correct="ab"), cand(2), cand(3, stem_html="すべて選べ。")]
    assigned = assign_positions(pool)
    assert [c.item_type for _, c in assigned] == ["A", "X2", "XX"]
    assert [p for p, _ in assigned] == [1, 2, 3]
