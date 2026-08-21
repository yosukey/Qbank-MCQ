"""検証チェーンのテスト(設計書 §9.2、§13.3、実装計画 §4 M3/M6)。

実装計画 §6「異常系」は、**壊した CSV を数種類用意し、期待どおりブロックされることを
確認する**ことを求めている。9 項目それぞれに 1 つずつ反例を当てる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from designdata import DESIGN_Q1_VALUES, counts_from_row
from qbank_mcq.core.stats import BLANK, OTHER, PATTERNS
from qbank_mcq.core.validate import (
    ExamItemView,
    ExamLimits,
    ParsedQuestionView,
    cross_validate_import,
    finalize_checks,
    validate_stats_import,
)

#: io.csv_stats が正規化したあとの度数列キー。無回答は表記ゆれを BLANK に寄せてある。
ALL_COLUMNS = [*PATTERNS, BLANK]


@dataclass
class FakeRow:
    """``io.csv_stats.StatsRow`` と同じ形の最小実装。"""

    position: int
    correct: str
    counts_raw: dict[str, float]
    p_reported: float | None = None
    n_correct_reported: int | None = None
    disc: float | None = None
    unreadable: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(self.counts_raw.values())

    @property
    def has_non_integer(self) -> bool:
        return any(v != int(v) for v in self.counts_raw.values())

    @property
    def has_negative(self) -> bool:
        return any(v < 0 for v in self.counts_raw.values())


def make_row(position: int = 1, correct: str = "ad", **kw) -> FakeRow:
    counts = {k: float(v) for k, v in counts_from_row(DESIGN_Q1_VALUES).items()}
    row = FakeRow(
        position=position,
        correct=correct,
        counts_raw=counts,
        p_reported=112 / 139,
        n_correct_reported=112,
        disc=0.529,
    )
    for key, value in kw.items():
        setattr(row, key, value)
    return row


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def blocking_codes(issues) -> set[str]:
    return {i.code for i in issues if i.blocking}


BASE_ARGS = dict(pattern_columns_found=ALL_COLUMNS, n_examinees=139)


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------


def test_a_clean_import_passes_every_check() -> None:
    """設計書 §10.2 の実データはチェーン 9 項目をすべて通る。"""
    issues = validate_stats_import([make_row()], {1: "ad"}, **BASE_ARGS)
    assert issues == []


# ---------------------------------------------------------------------------
# 9 項目の反例
# ---------------------------------------------------------------------------


def test_1_totals_must_agree_across_questions() -> None:
    a = make_row(1, "ad")
    b = make_row(2, "ad")
    b.counts_raw = {**b.counts_raw, BLANK: 3.0}  # この設問だけ合計が 3 多い
    issues = validate_stats_import([a, b], {1: "ad", 2: "ad"}, **BASE_ARGS)
    assert "total_mismatch" in blocking_codes(issues)
    assert "2" in next(i.message for i in issues if i.code == "total_mismatch")


def test_2_total_must_equal_the_meta_examinee_count() -> None:
    issues = validate_stats_import(
        [make_row()], {1: "ad"}, pattern_columns_found=ALL_COLUMNS, n_examinees=150
    )
    assert "n_mismatch" in blocking_codes(issues)


def test_3_correct_pattern_column_must_equal_the_correct_count() -> None:
    issues = validate_stats_import([make_row(n_correct_reported=100)], {1: "ad"}, **BASE_ARGS)
    assert "n_correct_mismatch" in blocking_codes(issues)


def test_4_reported_p_must_match_the_recomputed_one() -> None:
    issues = validate_stats_import([make_row(p_reported=0.5)], {1: "ad"}, **BASE_ARGS)
    assert "p_mismatch" in blocking_codes(issues)


def test_4_rounding_within_tolerance_is_accepted() -> None:
    """CSV の正答率は丸めて届く。0.8058 は通る(設計書 §10.2)。"""
    issues = validate_stats_import([make_row(p_reported=0.8058)], {1: "ad"}, **BASE_ARGS)
    assert issues == []


def test_5_csv_correct_must_match_the_exam_record() -> None:
    """正答の食い違い、または出題順のずれを捕まえる(設計書 §9.2-5)。"""
    issues = validate_stats_import([make_row()], {1: "bc"}, **BASE_ARGS)
    assert "correct_mismatch" in blocking_codes(issues)


def test_5_order_is_insensitive_to_label_sorting() -> None:
    issues = validate_stats_import([make_row(correct="da")], {1: "ad"}, **BASE_ARGS)
    assert "correct_mismatch" not in codes(issues)


def test_6_off_grid_disc_is_only_a_warning() -> None:
    """設計書 §9.2 の表で唯一「警告」の項目。"""
    issues = validate_stats_import([make_row(disc=0.51)], {1: "ad"}, **BASE_ARGS)
    assert "disc_off_grid" in codes(issues)
    assert "disc_off_grid" not in blocking_codes(issues)


def test_7_counts_must_be_non_negative_integers() -> None:
    row = make_row()
    row.counts_raw = {**row.counts_raw, "b": -1.0}
    assert "count_negative" in blocking_codes(validate_stats_import([row], {1: "ad"}, **BASE_ARGS))


def test_7_ratios_instead_of_counts_are_caught() -> None:
    """「人数か割合か」の取り違えは静かに全統計を壊す(実装計画 §11)。"""
    row = make_row()
    row.counts_raw = {k: v / 139 for k, v in row.counts_raw.items()}
    assert "count_not_integer" in blocking_codes(
        validate_stats_import([row], {1: "ad"}, **BASE_ARGS)
    )


def test_7_unreadable_cells_are_reported() -> None:
    row = make_row(unreadable={"c": "―"})
    assert "count_unreadable" in blocking_codes(
        validate_stats_import([row], {1: "ad"}, **BASE_ARGS)
    )


def test_8_pattern_columns_must_be_exactly_31_plus_blank() -> None:
    missing = [c for c in ALL_COLUMNS if c != BLANK]
    issues = validate_stats_import(
        [make_row()], {1: "ad"}, pattern_columns_found=missing, n_examinees=139
    )
    assert "pattern_columns_missing" in blocking_codes(issues)


def test_8_unknown_pattern_columns_are_blocked() -> None:
    issues = validate_stats_import(
        [make_row()], {1: "ad"}, pattern_columns_found=[*ALL_COLUMNS, "abcdef"], n_examinees=139
    )
    assert "pattern_columns_extra" in blocking_codes(issues)


def test_8_the_other_column_is_optional() -> None:
    """``その他`` は方言による追加列。あってもなくても通す。"""
    issues = validate_stats_import(
        [make_row()], {1: "ad"}, pattern_columns_found=[*ALL_COLUMNS, OTHER], n_examinees=139
    )
    assert "pattern_columns_extra" not in codes(issues)
    assert blocking_codes(issues) == set()


def test_8_two_columns_for_the_same_bucket_are_blocked() -> None:
    """``空白`` と ``無解答`` が両方あると、どちらを採るかで受験者数が変わる。"""
    issues = validate_stats_import(
        [make_row()], {1: "ad"}, pattern_columns_found=[*ALL_COLUMNS, BLANK], n_examinees=139
    )
    assert "pattern_columns_duplicate" in blocking_codes(issues)


def test_9_row_count_must_equal_the_number_of_questions() -> None:
    issues = validate_stats_import([make_row()], {1: "ad", 2: "bc"}, **BASE_ARGS)
    assert "row_count" in blocking_codes(issues)


def test_unknown_position_is_blocked() -> None:
    """CSV に試験へ存在しない出題番号があれば止める。"""
    issues = validate_stats_import([make_row(position=7)], {1: "ad"}, **BASE_ARGS)
    assert "position_unknown" in blocking_codes(issues)


def test_missing_fixed_columns_are_reported() -> None:
    issues = validate_stats_import(
        [make_row()],
        {1: "ad"},
        pattern_columns_found=ALL_COLUMNS,
        missing_fixed_columns=["正答肢"],
        n_examinees=139,
    )
    assert "csv_missing_columns" in blocking_codes(issues)


# ---------------------------------------------------------------------------
# docx ⇔ CSV の相互検証(実装計画 §4 M3)
# ---------------------------------------------------------------------------

FIVE = ("あ", "い", "う", "え", "お")


def test_cross_validation_accepts_a_matching_pair() -> None:
    questions = [ParsedQuestionView(1, "正しいのはどれか。2つ選べ。", FIVE)]
    assert cross_validate_import(questions, [make_row(correct="ad")]) == []


def test_cross_validation_catches_type_vs_correct_count() -> None:
    """docx 由来のタイプ ⇔ CSV 由来の正答個数。"""
    questions = [ParsedQuestionView(1, "正しいのはどれか。1つ選べ。", FIVE)]
    issues = cross_validate_import(questions, [make_row(correct="ad")])
    assert "type_correct_mismatch" in blocking_codes(issues)


def test_cross_validation_catches_question_count_mismatch() -> None:
    questions = [
        ParsedQuestionView(1, "正しいのはどれか。1つ選べ。", FIVE),
        ParsedQuestionView(2, "正しいのはどれか。1つ選べ。", FIVE),
    ]
    issues = cross_validate_import(questions, [make_row(correct="a")])
    assert "question_count_mismatch" in blocking_codes(issues)
    assert "no_stats_row" in blocking_codes(issues)


def test_cross_validation_reports_rows_without_a_question() -> None:
    issues = cross_validate_import([], [make_row(correct="ad")])
    assert "no_question" in blocking_codes(issues)


def test_cross_validation_checks_the_emphasis_rule() -> None:
    """過去問の一括取込は既存資産の品質点検としてまとめて機能する(設計書 §4)。"""
    questions = [ParsedQuestionView(1, "含まれないのはどれか。1つ選べ。", FIVE)]
    issues = cross_validate_import(questions, [make_row(correct="a")])
    assert "emphasis_missing" in codes(issues)
    assert "emphasis_missing" not in blocking_codes(issues)


def test_cross_validation_accepts_xx_with_four_answers() -> None:
    questions = [ParsedQuestionView(1, "正しいものをすべて選べ。", FIVE)]
    assert cross_validate_import(questions, [make_row(correct="abce")]) == []


# ---------------------------------------------------------------------------
# finalize 前チェック(設計書 §13.3)
# ---------------------------------------------------------------------------


def view(position: int, **kw) -> ExamItemView:
    defaults = dict(
        question_id=position,
        qversion_id=position,
        status="active",
        stem_html="正しいのはどれか。1つ選べ。",
        correct="a",
        choice_set_id=position,
    )
    defaults.update(kw)
    return ExamItemView(position=position, **defaults)


def test_finalize_passes_on_a_clean_exam() -> None:
    report = finalize_checks([view(1), view(2), view(3)])
    assert not report.blocked
    assert report.issues == []


def test_finalize_blocks_a_draft_question() -> None:
    """設計書 §2.5 / §13.3: draft の問題は出題セットに入れられない。"""
    report = finalize_checks([view(1), view(2, status="draft")])
    assert report.blocked
    assert "draft_included" in blocking_codes(report.issues)


def test_finalize_blocks_a_missing_correct() -> None:
    report = finalize_checks([view(1, correct="")])
    assert "no_correct" in blocking_codes(report.issues)


def test_finalize_blocks_a_correct_count_mismatch() -> None:
    report = finalize_checks([view(1, correct="ab")])
    assert "correct_count" in blocking_codes(report.issues)


def test_finalize_blocks_an_unparsable_instruction() -> None:
    report = finalize_checks([view(1, stem_html="正しいのはどれか。")])
    assert "type_underivable" in blocking_codes(report.issues)


def test_finalize_blocks_a_position_gap() -> None:
    report = finalize_checks([view(1), view(3)], expected_positions=3)
    assert "position_gap" in blocking_codes(report.issues)


def test_finalize_blocks_the_same_question_twice() -> None:
    report = finalize_checks([view(1, question_id=7), view(2, question_id=7)])
    assert "duplicate_question" in blocking_codes(report.issues)


def test_finalize_blocks_derived_questions_in_the_same_exam() -> None:
    """派生関係にある問題は実質同じ問題(設計書 §2.3, §13.3)。"""
    report = finalize_checks(
        [view(1, question_id=10), view(2, question_id=11)],
        derivation_families={10: {10, 11}, 11: {10, 11}},
    )
    assert "derived_together" in blocking_codes(report.issues)


def test_finalize_allows_unrelated_questions() -> None:
    report = finalize_checks(
        [view(1, question_id=10), view(2, question_id=11)],
        derivation_families={10: {10}, 11: {11}},
    )
    assert not report.blocked


def test_finalize_warns_when_a_set_group_is_overused() -> None:
    """前回と 1 肢だけ違うセットを続けて出す事故を防ぐ(設計書 §6.4-1)。"""
    report = finalize_checks(
        [view(1, choice_set_id=1), view(2, choice_set_id=2), view(3, choice_set_id=1)],
        set_links={1: {2}, 2: {1}},
        limits=ExamLimits(max_per_set_group=2),
    )
    assert not report.blocked  # 警告どまり
    assert "set_group_over_limit" in codes(report.issues)


def test_finalize_respects_a_raised_set_limit() -> None:
    report = finalize_checks(
        [view(1, choice_set_id=1), view(2, choice_set_id=1), view(3, choice_set_id=1)],
        limits=ExamLimits(max_per_set_group=3),
    )
    assert "set_group_over_limit" not in codes(report.issues)


def test_finalize_warns_on_too_many_negatives() -> None:
    """否定形設問は多すぎると読み飛ばしによる失点が増える(設計書 §4-(1))。"""
    negative = "正しく<strong>ない</strong>のはどれか。1つ選べ。"
    items = [view(i, stem_html=negative) for i in range(1, 5)]
    report = finalize_checks(items, limits=ExamLimits(max_negative=2))
    assert not report.blocked
    assert "negative_over_limit" in codes(report.issues)


def test_finalize_warns_on_the_negative_ratio() -> None:
    negative = "正しく<strong>ない</strong>のはどれか。1つ選べ。"
    items = [view(1, stem_html=negative), view(2), view(3), view(4)]
    report = finalize_checks(items, limits=ExamLimits(max_negative_ratio=0.1))
    assert "negative_over_ratio" in codes(report.issues)


def test_finalize_warns_on_a_type_distribution_miss() -> None:
    report = finalize_checks(
        [view(1), view(2)], limits=ExamLimits(type_distribution={"A": 2, "X2": 1})
    )
    assert "type_distribution" in codes(report.issues)
    assert not report.blocked


def test_finalize_blocks_an_empty_exam() -> None:
    report = finalize_checks([])
    assert "empty_exam" in blocking_codes(report.issues)


def test_retired_question_is_only_a_warning() -> None:
    report = finalize_checks([view(1, status="retired")])
    assert "retired_included" in codes(report.issues)
    assert not report.blocked


@pytest.mark.parametrize("correct,ok", [("a", True), ("abcde", True), ("", False)])
def test_finalize_accepts_any_xx_count(correct: str, ok: bool) -> None:
    report = finalize_checks([view(1, stem_html="すべて選べ。", correct=correct)])
    assert (not report.blocked) is ok
