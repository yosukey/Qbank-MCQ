"""設計書 §10.2 の形式(採点システムの実物)の取り込み。

``testdata/item_stats_2026_02.csv`` は採点システムが実際に出力したファイルで、
設計書 §10.2 はこれを正として書かれている。実データなので
**数値をハードコードして突き合わせる**(実装計画 §0「実データをテスト資産にする」)。

§10.2 が明示している扱い:

- メタ行が無い → 受験者数は度数合計から導く
- ``正答率(%)`` は 0〜100 → 0〜1 に直す
- ``正答数`` 列が無い → 正答パターン列から数える
- ``空白`` ではなく ``無解答``、さらに ``その他`` 列がある
- 30 問中 24 問が記述式(度数欄はすべて ``-``)
- ``配点`` ``措置`` ``点双列相関`` は読むが保存しない
"""

from __future__ import annotations

from pathlib import Path

import pytest

from itembank.__main__ import main
from itembank.core.bank import create_question_from_printed
from itembank.core.db import ItemStatRow
from itembank.core.exam import apply_stats, create_exam, finalize_exam, set_exam_items
from itembank.core.stats import BLANK, OTHER, PATTERNS, derive_item_stats
from itembank.core.validate import validate_stats_import
from itembank.io.csv_stats import (
    DIALECT_SSDB,
    KIND_NON_MCQ,
    parse_stats_csv,
)

REAL = Path(__file__).parent.parent / "testdata" / "item_stats_2026_02.csv"

pytestmark = pytest.mark.skipif(not REAL.exists(), reason="実物の集計 CSV がありません")

#: 実物から読み取れる事実。
N_EXAMINEES = 138
N_MCQ = 6
N_NON_MCQ = 24
#: ``(出題番号, 正答, 正答数, 識別係数, 配点)``
MCQ_FACTS = [
    (1, "b", 69, 0.294, 5.0),
    (2, "ce", 131, 0.059, 5.0),
    (3, "bd", 44, 0.559, 5.0),
    (4, "c", 114, 0.176, 5.0),
    (5, "b", 120, 0.294, 5.0),
    (6, "e", 109, 0.588, 5.0),
]


@pytest.fixture
def parsed():
    return parse_stats_csv(REAL)


# ---------------------------------------------------------------------------
# 方言の判定と全体像
# ---------------------------------------------------------------------------


def test_dialect_is_detected(parsed) -> None:
    assert parsed.dialect == DIALECT_SSDB
    assert parsed.percent_scale is True


def test_free_response_rows_are_separated(parsed) -> None:
    """記述式は選択式の統計としては扱えないので ``rows`` から外す。"""
    assert parsed.n_rows == N_MCQ
    assert len(parsed.non_mcq_rows) == N_NON_MCQ
    assert [r.position for r in parsed.rows] == [1, 2, 3, 4, 5, 6]
    assert [r.position for r in parsed.non_mcq_rows] == list(range(7, 31))
    assert all(r.kind == KIND_NON_MCQ for r in parsed.non_mcq_rows)
    assert all("記述式" in r.correct_raw for r in parsed.non_mcq_rows)


def test_examinee_count_is_derived_without_a_meta_row(parsed) -> None:
    """メタ行が無いので度数合計から導く。全 MCQ 行で 138 に揃っている。"""
    assert parsed.meta.n_examinees is None
    assert parsed.meta.derived_n == N_EXAMINEES
    assert parsed.meta.effective_n == N_EXAMINEES
    assert all(r.total == N_EXAMINEES for r in parsed.rows)


def test_blank_and_other_columns_are_normalized(parsed) -> None:
    """``無解答`` は BLANK に寄せ、``その他`` は別区分として N に算入する。"""
    assert parsed.pattern_columns_found == [*PATTERNS, BLANK, OTHER]
    assert parsed.missing_fixed_columns == []


def test_percent_is_converted_to_a_ratio(parsed) -> None:
    row = parsed.rows[0]
    assert row.p_reported == pytest.approx(0.50)  # CSV には 50.0 と書いてある


@pytest.mark.parametrize("position,correct,n_correct,disc,points", MCQ_FACTS)
def test_each_mcq_row_matches_the_file(
    parsed, position: int, correct: str, n_correct: int, disc: float, points: float
) -> None:
    row = next(r for r in parsed.rows if r.position == position)
    assert row.correct == correct
    assert row.counts()[correct] == n_correct
    assert row.disc == pytest.approx(disc)
    assert row.points == pytest.approx(points)
    assert row.adjustment == "none"
    assert row.is_adjusted is False


def test_unused_columns_are_kept_as_extras(parsed) -> None:
    """点双列相関は保存しないが、目視できるよう原文は持っておく。"""
    assert parsed.rows[0].extras["点双列相関"] == "0.265"


def test_no_column_is_misread_as_a_count(parsed) -> None:
    """配点・措置・点双列相関を度数に数えてしまうと受験者数が狂う。"""
    for row in parsed.rows:
        assert row.total == N_EXAMINEES
        assert not row.unreadable


# ---------------------------------------------------------------------------
# 導出値が CSV 記載値と一致するか
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position,correct,n_correct,disc,points", MCQ_FACTS)
def test_recomputed_p_matches_the_reported_one(
    parsed, position: int, correct: str, n_correct: int, disc: float, points: float
) -> None:
    """正答率は CSV の丸め値ではなく 正答数/N から再計算する(実装計画 §11)。"""
    row = next(r for r in parsed.rows if r.position == position)
    item_type = "A" if len(correct) == 1 else "X2"
    stats = derive_item_stats(row.counts(), row.correct, item_type, disc=row.disc)

    assert stats.n == N_EXAMINEES
    assert stats.n_correct == n_correct
    assert stats.p == pytest.approx(n_correct / N_EXAMINEES)
    # 記載の正答率(小数第 1 位までの %)とも一致する。
    assert stats.p == pytest.approx(row.p_reported, abs=1e-3)


def test_other_column_is_counted_in_n_not_dropped(parsed) -> None:
    """``その他`` を N から外すと受験者数と正答率がずれる。"""
    row = parsed.rows[0]
    counts = row.counts()
    counts[OTHER] = 5  # 実物では 0 なので、算入されることを足して確かめる
    stats = derive_item_stats(counts, row.correct, "A")
    assert stats.n == N_EXAMINEES + 5
    assert stats.other_rate == pytest.approx(5 / (N_EXAMINEES + 5))
    # その他は印字記号を含まないので周辺マーク率には入らない。
    assert sum(stats.sel.values()) < len(stats.sel)


def test_other_is_never_counted_as_an_instruction_violation() -> None:
    """``?`` の長さ 1 を「1 つ選んだ」と解釈してはいけない。

    素朴に ``len(pattern) != 指示個数`` で数えると、A(1 個)では違反にならず
    X2(2 個)では違反になる、という一貫しない結果になる。どちらでも 0 になること。
    """
    assert derive_item_stats({"a": 90, OTHER: 10}, "a", "A").overselect_rate == pytest.approx(0.0)
    assert derive_item_stats({"ab": 90, OTHER: 10}, "ab", "X2").overselect_rate == pytest.approx(
        0.0
    )
    # 本当の違反はこれまでどおり数える。
    assert derive_item_stats({"a": 90, "ab": 10}, "a", "A").overselect_rate == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 検証チェーン
# ---------------------------------------------------------------------------


def validate(parsed, exam_items):
    return validate_stats_import(
        parsed.rows,
        exam_items,
        pattern_columns_found=parsed.pattern_columns_found,
        missing_fixed_columns=parsed.missing_fixed_columns,
        n_examinees=parsed.meta.n_examinees,
        n_non_mcq=len(parsed.non_mcq_rows),
    )


def test_the_real_file_passes_the_chain(parsed) -> None:
    """実物は 1 件もブロックされない。"""
    issues = validate(parsed, {p: c for p, c, *_ in MCQ_FACTS})
    assert [i.code for i in issues if i.blocking] == []


def test_missing_meta_and_skipped_rows_are_reported_not_hidden(parsed) -> None:
    """黙って進めず、何を導出し何を飛ばしたかを必ず言う。"""
    codes = {i.code for i in validate(parsed, {p: c for p, c, *_ in MCQ_FACTS})}
    assert "n_not_declared" in codes
    assert "non_mcq_skipped" in codes


def test_a_wrong_correct_is_still_caught(parsed) -> None:
    """方言が変わっても §9.2-5 は効く。"""
    exam_items = {p: c for p, c, *_ in MCQ_FACTS}
    exam_items[3] = "ab"  # 実際は bd
    issues = validate(parsed, exam_items)
    assert "correct_mismatch" in {i.code for i in issues if i.blocking}


def test_adjustment_is_flagged_as_a_warning(parsed) -> None:
    """措置が入った問題は素の成績ではない。止めないが気づかせる。"""
    parsed.rows[0].adjustment = "全員正解"
    assert parsed.rows[0].is_adjusted is True
    issues = validate(parsed, {p: c for p, c, *_ in MCQ_FACTS})
    adjusted = [i for i in issues if i.code == "adjusted_item"]
    assert len(adjusted) == 1
    assert not adjusted[0].blocking


# ---------------------------------------------------------------------------
# 取込の通し(局面B)
# ---------------------------------------------------------------------------


CHOICES = ["選択肢1", "選択肢2", "選択肢3", "選択肢4", "選択肢5"]


@pytest.fixture
def exam_with_six_mcq(session):
    """実物と同じ 6 問・同じ正答の試験をバンク側に用意する。"""
    assignments = []
    for position, correct, *_ in MCQ_FACTS:
        instruction = "1つ選べ。" if len(correct) == 1 else "2つ選べ。"
        result, _ = create_question_from_printed(
            session,
            stem_html=f"問{position}はどれか。{instruction}",
            printed_choices=[f"問{position}の{c}" for c in CHOICES],
            correct=correct,
        )
        assert not result.blocked
        assignments.append((position, result.version.id))

    exam = create_exam(session, name="2026年度第2回", exam_date="2026-02-01")
    set_exam_items(session, exam, assignments)
    finalize_exam(session, exam)
    return exam


def test_stats_land_in_the_database(session, exam_with_six_mcq, parsed) -> None:
    exam = exam_with_six_mcq
    result = apply_stats(
        session,
        exam,
        parsed.rows,
        source_file=str(REAL),
        n_examinees=parsed.meta.effective_n,
    )
    assert result.written == N_MCQ
    assert exam.n_examinees == N_EXAMINEES
    assert exam.status == "imported"

    for position, _correct, n_correct, disc, _points in MCQ_FACTS:
        item = next(i for i in exam.items if i.position == position)
        stat = session.get(ItemStatRow, (exam.id, item.qversion_id))
        assert stat.n == N_EXAMINEES
        assert stat.n_correct == n_correct
        assert stat.p == pytest.approx(n_correct / N_EXAMINEES)
        assert stat.disc == pytest.approx(disc)


def test_low_discrimination_item_is_visible(session, exam_with_six_mcq, parsed) -> None:
    """問2 は正答率 94.9% で識別係数 0.059。点検対象として見えること。"""
    exam = exam_with_six_mcq
    apply_stats(session, exam, parsed.rows, n_examinees=parsed.meta.effective_n)
    item = next(i for i in exam.items if i.position == 2)
    stat = session.get(ItemStatRow, (exam.id, item.qversion_id))
    assert stat.p > 0.94
    assert stat.disc < 0.1
    # 錯乱肢がほとんど選ばれていないので dead_distractor が立つ。
    assert "dead_distractor" in (stat.flags or "")


def test_cli_imports_the_real_file(tmp_path: Path, session, capsys) -> None:
    """CLI からも同じ経路で通ること。"""
    db = tmp_path / "bank.sqlite"
    assert main(["--db", str(db), "db", "init"]) == 0
    capsys.readouterr()

    # バンク側の試験を用意する手間を避け、パーサ + 検証までを CLI で確認する。
    code = main(["--db", str(db), "import-stats", "--exam", "1", "--csv", str(REAL)])
    assert code == 2  # 試験 1 がまだ無い
    assert "試験 1 がありません" in capsys.readouterr().err
