"""``core.stats`` のテスト(設計書 §12)。

**設計書 §10.2 に載っている実データ 1 行(問1)を基準にする。** 正答率・受験者数が
シート記載値と一致することを直接確かめられる、数少ない検証可能な実測値なので、
実装計画 §2.3 のデータ層スパイクに相当する検証をここで自動化している。
"""

from __future__ import annotations

import pytest

from designdata import (
    DESIGN_Q1_CORRECT,
    DESIGN_Q1_DISC,
    DESIGN_Q1_N,
    DESIGN_Q1_N_CORRECT,
    DESIGN_Q1_P,
)
from itembank.core.stats import (
    BLANK,
    FLAG_DEAD_DISTRACTOR,
    FLAG_DOMINANT_WRONG,
    FLAG_EMPHASIS_RULE,
    FLAG_NEGATIVE_DISC,
    FLAG_OVERSELECT,
    FLAG_PERSISTENT_LOW_DISC,
    PATTERNS,
    FlagThresholds,
    all_patterns,
    compute_flags,
    decode_flags,
    derive_exam_stats,
    derive_item_stats,
    disc_resolution,
    encode_flags,
    is_disc_on_grid,
    pattern_columns,
)
from itembank.core.typing_rules import TYPE_A, TYPE_X2, TYPE_XX

# ---------------------------------------------------------------------------
# パターン一覧(設計書 §10.2)
# ---------------------------------------------------------------------------


def test_there_are_exactly_31_patterns() -> None:
    assert len(PATTERNS) == 31
    assert len(set(PATTERNS)) == 31


def test_pattern_order_matches_the_csv_header() -> None:
    """設計書 §10.2 のヘッダそのままの順序であること。"""
    head = "a,b,c,d,e,ab,ac,ad,ae,bc,bd,be,cd,ce,de,abc,abd,abe,acd,ace,ade"
    tail = "bcd,bce,bde,cde,abcd,abce,abde,acde,bcde,abcde"
    assert ",".join(all_patterns()) == f"{head},{tail}"


def test_pattern_columns_appends_blank() -> None:
    assert pattern_columns()[-1] == "空白"
    assert len(pattern_columns()) == 32


# ---------------------------------------------------------------------------
# 設計書 §10.2 の実データで導出を突き合わせる
# ---------------------------------------------------------------------------


def test_design_row_reproduces_published_values(design_q1_counts: dict[str, int]) -> None:
    s = derive_item_stats(design_q1_counts, DESIGN_Q1_CORRECT, TYPE_X2, disc=DESIGN_Q1_DISC)
    assert s.n == DESIGN_Q1_N  # 32 列の度数合計 = 受験者数
    assert s.n_correct == DESIGN_Q1_N_CORRECT
    # 正答率は CSV の丸め値ではなく 正答数/N から再計算する(実装計画 §11)。
    assert s.p == pytest.approx(112 / 139)
    assert round(s.p, 4) == DESIGN_Q1_P


def test_design_row_marginal_rates(design_q1_counts: dict[str, int]) -> None:
    """周辺マーク率 = その選択肢を含む全パターンの度数合計 / N(設計書 §12)。"""
    s = derive_item_stats(design_q1_counts, DESIGN_Q1_CORRECT, TYPE_X2)
    assert s.sel["a"] == pytest.approx(129 / 139)  # a, ab, ac, ad, ae
    assert s.sel["b"] == pytest.approx(13 / 139)
    assert s.sel["c"] == pytest.approx(8 / 139)
    assert s.sel["d"] == pytest.approx(118 / 139)
    assert s.sel["e"] == pytest.approx(9 / 139)
    assert sum(s.sel.values()) * 139 == pytest.approx(129 + 13 + 8 + 118 + 9)


def test_design_row_top_wrong_pattern(design_q1_counts: dict[str, int]) -> None:
    s = derive_item_stats(design_q1_counts, DESIGN_Q1_CORRECT, TYPE_X2)
    assert s.top_wrong_pattern == "ab"
    assert s.top_wrong_count == 7


def test_design_row_partial_distribution(design_q1_counts: dict[str, int]) -> None:
    """正答集合との一致数別に度数を集約(設計書 §12)。"""
    s = derive_item_stats(design_q1_counts, DESIGN_Q1_CORRECT, TYPE_X2)
    assert s.partial == {0: 4, 1: 23, 2: 112}
    assert sum(s.partial.values()) == DESIGN_Q1_N


def test_design_row_overselect_rate(design_q1_counts: dict[str, int]) -> None:
    """X2 なのに 1 つしか選んでいない ``b`` の 1 名だけが違反。"""
    s = derive_item_stats(design_q1_counts, DESIGN_Q1_CORRECT, TYPE_X2)
    assert s.overselect_rate == pytest.approx(1 / 139)


def test_design_row_has_no_flags(design_q1_counts: dict[str, int]) -> None:
    s = derive_item_stats(design_q1_counts, DESIGN_Q1_CORRECT, TYPE_X2, disc=DESIGN_Q1_DISC)
    assert compute_flags(s) == []


def test_design_row_disc_sits_on_the_resolution_grid() -> None:
    """N=139 なら刻みは 1/34。0.529 は 18/34=0.5294 の丸め(設計書 §9.2-6, §12)。"""
    assert disc_resolution(139) == pytest.approx(1 / 34)
    assert is_disc_on_grid(DESIGN_Q1_DISC, DESIGN_Q1_N)
    assert not is_disc_on_grid(0.51, DESIGN_Q1_N)


# ---------------------------------------------------------------------------
# 導出の細部
# ---------------------------------------------------------------------------


def test_blank_counts_toward_n_and_blank_rate() -> None:
    s = derive_item_stats({"a": 90, "b": 5, BLANK: 5}, "a", TYPE_A)
    assert s.n == 100
    assert s.blank_rate == pytest.approx(0.05)
    assert s.p == pytest.approx(0.9)


def test_blank_is_not_a_wrong_pattern() -> None:
    """無回答は blank_rate として別に扱う。最頻誤答パターンには入れない。"""
    s = derive_item_stats({"a": 40, "b": 10, BLANK: 50}, "a", TYPE_A)
    assert s.top_wrong_pattern == "b"
    assert s.top_wrong_count == 10


def test_blank_excluded_from_overselect_by_default() -> None:
    s = derive_item_stats({"a": 90, BLANK: 10}, "a", TYPE_A)
    assert s.overselect_rate == pytest.approx(0.0)
    s2 = derive_item_stats({"a": 90, BLANK: 10}, "a", TYPE_A, overselect_includes_blank=True)
    assert s2.overselect_rate == pytest.approx(0.1)


def test_xx_has_no_overselect_rate() -> None:
    """設計書 §12: 指示個数違反率は XX では算出しない。"""
    s = derive_item_stats({"ab": 50, "abc": 50}, "ab", TYPE_XX)
    assert s.overselect_rate is None


def test_unknown_type_has_no_overselect_rate() -> None:
    s = derive_item_stats({"ab": 50, "abc": 50}, "ab", None)
    assert s.overselect_rate is None


def test_top_wrong_ties_break_by_column_order() -> None:
    s = derive_item_stats({"a": 10, "b": 5, "c": 5}, "a", TYPE_A)
    assert s.top_wrong_pattern == "b"  # §10.2 の列順で先のもの


def test_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="非負整数"):
        derive_item_stats({"a": 10, "b": -1}, "a", TYPE_A)


def test_rejects_unknown_pattern() -> None:
    with pytest.raises(ValueError, match="未知のパターン"):
        derive_item_stats({"af": 10}, "a", TYPE_A)


def test_rejects_zero_total() -> None:
    with pytest.raises(ValueError, match="合計が 0"):
        derive_item_stats({"a": 0}, "a", TYPE_A)


# ---------------------------------------------------------------------------
# フラグ(設計書 §12)
# ---------------------------------------------------------------------------


def test_negative_disc_flag() -> None:
    """識別係数 < 0。正答設定ミス・二義性の疑いで最優先点検(実例: 問45)。"""
    s = derive_item_stats({"a": 50, "b": 50}, "a", TYPE_A, disc=-0.12)
    assert FLAG_NEGATIVE_DISC in compute_flags(s)


def test_dominant_wrong_flag() -> None:
    """最頻誤答パターンの度数 > 正答数(実例: 問42、問19)。"""
    s = derive_item_stats({"a": 30, "b": 70}, "a", TYPE_A)
    flags = compute_flags(s)
    assert FLAG_DOMINANT_WRONG in flags


def test_dominant_wrong_not_raised_when_correct_leads() -> None:
    s = derive_item_stats({"a": 70, "b": 30}, "a", TYPE_A)
    assert FLAG_DOMINANT_WRONG not in compute_flags(s)


def test_dead_distractor_flag() -> None:
    """周辺マーク率 < 5% の錯乱肢があれば立つ。"""
    s = derive_item_stats({"a": 96, "b": 2, "c": 2}, "a", TYPE_A)
    assert FLAG_DEAD_DISTRACTOR in compute_flags(s)
    assert set(s.dead_distractors()) == {"b", "c", "d", "e"}


def test_dead_distractor_threshold_is_configurable() -> None:
    s = derive_item_stats({"a": 90, "b": 4, "c": 3, "d": 2, "e": 1}, "a", TYPE_A)
    loose = FlagThresholds(dead_distractor_rate=0.0)
    assert FLAG_DEAD_DISTRACTOR not in compute_flags(s, thresholds=loose)
    assert FLAG_DEAD_DISTRACTOR in compute_flags(s)


def test_overselect_flag() -> None:
    s = derive_item_stats({"ab": 70, "a": 30}, "ab", TYPE_X2)
    assert FLAG_OVERSELECT in compute_flags(s)


def test_persistent_low_disc_needs_more_than_one_year() -> None:
    """設計書 §12: 単年値では判断しない。"""
    s = derive_item_stats({"a": 60, "b": 40}, "a", TYPE_A, disc=0.05)
    assert FLAG_PERSISTENT_LOW_DISC not in compute_flags(s)
    assert FLAG_PERSISTENT_LOW_DISC in compute_flags(s, prior_discs=[0.03])


def test_persistent_low_disc_resets_when_a_year_was_fine() -> None:
    s = derive_item_stats({"a": 60, "b": 40}, "a", TYPE_A, disc=0.05)
    assert FLAG_PERSISTENT_LOW_DISC not in compute_flags(s, prior_discs=[0.4])


def test_emphasis_rule_flag_is_passed_in() -> None:
    s = derive_item_stats({"a": 60, "b": 40}, "a", TYPE_A)
    assert FLAG_EMPHASIS_RULE in compute_flags(s, emphasis_violation=True)


def test_flag_encoding_roundtrip() -> None:
    flags = [FLAG_DOMINANT_WRONG, FLAG_NEGATIVE_DISC]
    assert decode_flags(encode_flags(flags)) == sorted(flags)
    assert decode_flags("") == []
    assert decode_flags(None) == []


# ---------------------------------------------------------------------------
# 試験全体
# ---------------------------------------------------------------------------


def test_exam_score_stats() -> None:
    st = derive_exam_stats([10, 20, 30, 40])
    assert st.n == 4
    assert st.mean == pytest.approx(25.0)
    assert st.median == pytest.approx(25.0)
    assert st.sd > 0


def test_exam_score_stats_rejects_empty() -> None:
    with pytest.raises(ValueError):
        derive_exam_stats([])
