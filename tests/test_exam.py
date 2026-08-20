"""試験の組み立て → finalize → 統計取込 の統合テスト(設計書 §1.2, §9, §13)。

インメモリ SQLite で一連の操作を実行し、レコード状態を検証する(実装計画 §6「統合」)。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from designdata import DESIGN_Q1_VALUES, counts_from_row
from itembank.core.bank import create_question_from_printed, derive_question, revise_question
from itembank.core.db import E_DRAFT, E_FINALIZED, E_IMPORTED, ItemPatternCount, ItemStatRow
from itembank.core.exam import (
    ExamLockedError,
    apply_stats,
    booklet_sources,
    build_candidates,
    check_finalize,
    create_exam,
    finalize_exam,
    flagged_after_import,
    prior_discs,
    set_exam_items,
)
from itembank.core.stats import (
    FLAG_DOMINANT_WRONG,
    FLAG_NO_STATS,
    FLAG_PERSISTENT_LOW_DISC,
    decode_flags,
)

SET_A = ["エナメル質", "象牙質", "セメント質", "歯髄", "歯根膜"]
SET_B = ["横紋", "死帯", "頰骨", "導管", "歯堤"]


@dataclass
class Row:
    """``io.csv_stats.StatsRow`` の最小代役。"""

    position: int
    correct: str
    _counts: dict[str, int]
    disc: float | None = None

    def counts(self) -> dict[str, int]:
        return dict(self._counts)


def add_question(session: Session, stem: str, correct: str, items=None, **kw):
    result, _ = create_question_from_printed(
        session,
        stem_html=stem,
        printed_choices=list(items or SET_A),
        correct=correct,
        **kw,
    )
    assert not result.blocked, [i.message for i in result.issues]
    return result


@pytest.fixture
def exam_with_two_items(session: Session):
    q1 = add_question(session, "最も硬いのはどれか。1つ選べ。", "a")
    q2 = add_question(session, "血管があるのはどれか。2つ選べ。", "de", items=SET_B)
    exam = create_exam(session, name="定期試験2025", exam_date="2025-08-25")
    set_exam_items(session, exam, [(1, q1.version.id), (2, q2.version.id)])
    return exam, q1, q2


# ---------------------------------------------------------------------------
# 組み立てと finalize
# ---------------------------------------------------------------------------


def test_exam_items_copy_the_correct_from_the_version(
    session: Session, exam_with_two_items
) -> None:
    exam, _, _ = exam_with_two_items
    assert [(i.position, i.correct_asked) for i in exam.items] == [(1, "a"), (2, "de")]
    assert exam.status == E_DRAFT


def test_finalize_moves_the_status(session: Session, exam_with_two_items) -> None:
    exam, _, _ = exam_with_two_items
    report = finalize_exam(session, exam)
    assert not report.blocked
    assert exam.status == E_FINALIZED


def test_a_finalized_exam_is_locked(session: Session, exam_with_two_items) -> None:
    """確定後はセット・使用版・正答を変更ロックする(設計書 §13.3)。"""
    exam, q1, _ = exam_with_two_items
    finalize_exam(session, exam)
    with pytest.raises(ExamLockedError):
        set_exam_items(session, exam, [(1, q1.version.id)])
    with pytest.raises(ExamLockedError):
        finalize_exam(session, exam)


def test_finalize_blocks_a_draft_question(session: Session) -> None:
    q = add_question(session, "最も硬いのはどれか。1つ選べ。", "a", status="draft")
    exam = create_exam(session, name="x")
    set_exam_items(session, exam, [(1, q.version.id)])
    report = finalize_exam(session, exam)
    assert report.blocked
    assert exam.status == E_DRAFT  # 確定していない


def test_finalize_blocks_derived_questions_together(session: Session) -> None:
    base = add_question(session, "最も硬いのはどれか。1つ選べ。", "a")
    derived = derive_question(
        session,
        base.version,
        stem_html="最も軟らかいのはどれか。1つ選べ。",
        choice_set=base.version.choice_set,
        choice_order=base.version.choice_order,
        correct="d",
    )
    exam = create_exam(session, name="x")
    set_exam_items(session, exam, [(1, base.version.id), (2, derived.version.id)])
    report = check_finalize(session, exam)
    assert "derived_together" in {i.code for i in report.issues if i.blocking}


def test_booklet_sources_resolve_the_printed_order(session: Session) -> None:
    """``choice_order`` を解いて印字順に並べ替える(設計書 §6.1)。"""
    q = add_question(session, "最も硬いのはどれか。1つ選べ。", "a")
    # 逆順で同じセットを使う問題を足すと、同じセットのまま別の並びになる。
    q2, created = create_question_from_printed(
        session,
        stem_html="最も軟らかいのはどれか。1つ選べ。",
        printed_choices=list(reversed(SET_A)),
        correct="a",
    )
    assert not created  # セットは再利用される
    assert q2.version.choice_set_id == q.version.choice_set_id
    assert q2.version.choice_order != q.version.choice_order

    exam = create_exam(session, name="x")
    set_exam_items(session, exam, [(1, q.version.id), (2, q2.version.id)])
    sources = booklet_sources(session, exam)
    assert sources[0].choices == SET_A
    assert sources[1].choices == list(reversed(SET_A))


# ---------------------------------------------------------------------------
# 統計の付与(設計書 §9)
# ---------------------------------------------------------------------------


def test_stats_need_a_finalized_exam(session: Session, exam_with_two_items) -> None:
    """設計書 §9.1: ``status='finalized'`` の試験にのみ統計を与えられる。"""
    exam, _, _ = exam_with_two_items
    with pytest.raises(ValueError, match="確定済み"):
        apply_stats(session, exam, [])


def test_apply_stats_writes_counts_and_derived_values(
    session: Session, exam_with_two_items
) -> None:
    exam, _, _ = exam_with_two_items
    finalize_exam(session, exam)
    counts = counts_from_row(DESIGN_Q1_VALUES)

    rows = [
        Row(1, "a", {"a": 100, "b": 39}, disc=0.4),
        Row(2, "de", {**counts, "ad": 0, "de": 112}, disc=0.529),
    ]
    result = apply_stats(session, exam, rows, source_file="x.csv", n_examinees=139)

    assert result.written == 2
    assert exam.status == E_IMPORTED
    assert exam.n_examinees == 139

    stat = session.get(ItemStatRow, (exam.id, exam.items[0].qversion_id))
    assert stat.n == 139
    assert stat.n_correct == 100
    assert stat.p == pytest.approx(100 / 139)  # 丸め値ではなく再計算
    assert stat.source_file == "x.csv"

    stored = session.query(ItemPatternCount).filter_by(exam_id=exam.id).count()
    assert stored > 0


def test_reimporting_replaces_rather_than_duplicates(session: Session, exam_with_two_items) -> None:
    exam, _, _ = exam_with_two_items
    finalize_exam(session, exam)
    rows = [Row(1, "a", {"a": 100, "b": 39}), Row(2, "de", {"de": 90, "ab": 49})]
    apply_stats(session, exam, rows)
    first = session.query(ItemPatternCount).filter_by(exam_id=exam.id).count()
    apply_stats(session, exam, rows)
    assert session.query(ItemPatternCount).filter_by(exam_id=exam.id).count() == first


def test_flags_are_written_and_listed(session: Session, exam_with_two_items) -> None:
    """取込完了時にフラグの付いた問題を一覧表示する(設計書 §9.3)。"""
    exam, _, _ = exam_with_two_items
    finalize_exam(session, exam)
    apply_stats(
        session,
        exam,
        [
            Row(1, "a", {"a": 30, "b": 109}),  # 多数派が誤答に流れている
            Row(2, "de", {"de": 100, "ab": 39}),
        ],
    )
    stat = session.get(ItemStatRow, (exam.id, exam.items[0].qversion_id))
    assert FLAG_DOMINANT_WRONG in decode_flags(stat.flags)

    flagged = flagged_after_import(session, exam)
    assert flagged[0][0] == 1
    assert FLAG_DOMINANT_WRONG in flagged[0][2]


def test_persistent_low_disc_spans_exams(session: Session) -> None:
    """単年値では判断しない(設計書 §12)。2 年ぶんそろって初めて立つ。"""
    q = add_question(session, "最も硬いのはどれか。1つ選べ。", "a")

    def run(name: str, date: str, disc: float):
        exam = create_exam(session, name=name, exam_date=date)
        set_exam_items(session, exam, [(1, q.version.id)])
        finalize_exam(session, exam)
        apply_stats(session, exam, [Row(1, "a", {"a": 80, "b": 59}, disc=disc)])
        return exam

    first = run("2024", "2024-08-25", 0.05)
    stat = session.get(ItemStatRow, (first.id, q.version.id))
    assert FLAG_PERSISTENT_LOW_DISC not in decode_flags(stat.flags)

    second = run("2025", "2025-08-25", 0.03)
    assert prior_discs(session, q.version, second) == [0.05]
    stat2 = session.get(ItemStatRow, (second.id, q.version.id))
    assert FLAG_PERSISTENT_LOW_DISC in decode_flags(stat2.flags)


def test_stats_stay_with_the_old_version_after_a_revision(session: Session) -> None:
    """改訂すると旧統計は旧版に残り、新版は実績ゼロから(設計書 §2.2)。"""
    q = add_question(session, "最も硬いのはどれか。1つ選べ。", "a")
    exam = create_exam(session, name="2024", exam_date="2024-08-25")
    set_exam_items(session, exam, [(1, q.version.id)])
    finalize_exam(session, exam)
    apply_stats(session, exam, [Row(1, "a", {"a": 100, "b": 39}, disc=0.4)])

    revised = revise_question(
        session,
        q.question,
        stem_html="最も硬いのはどれか。1つ選べ。",
        choice_set=q.version.choice_set,
        choice_order=q.version.choice_order,
        correct="b",
    )
    assert revised.created_new_version
    # 旧版の統計はそのまま。新版には統計が無い。
    assert session.get(ItemStatRow, (exam.id, q.version.id)) is not None
    assert session.get(ItemStatRow, (exam.id, revised.version.id)) is None


# ---------------------------------------------------------------------------
# 候補の組み立て(設計書 §13.1)
# ---------------------------------------------------------------------------


def test_candidates_carry_history_and_stats(session: Session, exam_with_two_items) -> None:
    exam, q1, _ = exam_with_two_items
    finalize_exam(session, exam)
    apply_stats(
        session,
        exam,
        [Row(1, "a", {"a": 100, "b": 39}, disc=0.4), Row(2, "de", {"de": 90, "ab": 49}, disc=0.3)],
    )
    candidates = {c.question_id: c for c in build_candidates(session)}
    first = candidates[q1.question.id]
    assert first.times_used == 1
    assert first.last_exam_year == 2025
    assert first.p == pytest.approx(100 / 139)
    assert first.disc == pytest.approx(0.4)


def test_never_used_questions_are_flagged_as_new(session: Session) -> None:
    add_question(session, "最も硬いのはどれか。1つ選べ。", "a")
    candidate = build_candidates(session)[0]
    assert candidate.is_new
    assert FLAG_NO_STATS in candidate.flags
    assert candidate.times_used == 0


def test_candidates_use_the_latest_version(session: Session) -> None:
    q = add_question(session, "最も硬いのはどれか。1つ選べ。", "a")
    revised = revise_question(
        session,
        q.question,
        stem_html="最も硬いのはどれか。1つ選べ。",
        choice_set=q.version.choice_set,
        choice_order=q.version.choice_order,
        correct="b",
    )
    candidate = build_candidates(session)[0]
    assert candidate.qversion_id == revised.version.id
    assert candidate.correct == "b"
