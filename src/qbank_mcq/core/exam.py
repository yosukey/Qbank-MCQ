"""試験セットの組み立て・finalize・統計の付与。

設計書 §1 の運用サイクルのうち、局面B(平常運用)の骨格。

    バンク ──選定──► 出題セット(finalize) ──┬──► 問題冊子(.docx)
                                             └──► 正答キー(.csv) ──► ss-database
                                                                          │
                        ③統計を「このセット」に与える ◄─── 集計CSV ◄────────┘

**問題の再取込は行わない。** 出題セットは finalize 時点で「どの版を何番として出したか」
が記録済みであり、集計 CSV は**そのセットに統計を与えるだけ**でよい(設計書 §1.2)。
局面Bで問題を作成する経路はこのモジュールに存在しない。

``core.validate`` は純関数なので、DB からの組み立てはここが受け持つ。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .bank import derivation_family, linked_set_ids, tag_names
from .choiceset import ordered_items
from .db import (
    E_DRAFT,
    E_FINALIZED,
    E_IMPORTED,
    ChoiceSet,
    Exam,
    ExamItem,
    ItemPatternCount,
    ItemStatRow,
    Question,
    QuestionVersion,
    utcnow_iso,
)
from .selection import Candidate
from .stats import (
    DEFAULT_THRESHOLDS,
    FLAG_EMPHASIS_RULE,
    FLAG_NO_STATS,
    FlagThresholds,
    ItemStats,
    compute_flags,
    decode_flags,
    derive_item_stats,
    encode_flags,
)
from .typing_rules import (
    ValidationIssue,
    check_emphasis_rule,
    derive_item_type_detail,
    normalize_correct,
)
from .validate import DEFAULT_LIMITS, ExamItemView, ExamLimits, FinalizeReport, finalize_checks

log = logging.getLogger(__name__)


class ExamLockedError(RuntimeError):
    """finalize 済みの試験の構成を変えようとした。

    設計書 §13.3: 確定後はセット・使用版・正答を変更ロックし、恒久記録とする。
    """


def create_exam(
    session: Session,
    *,
    name: str,
    exam_date: str | None = None,
    course: str | None = None,
    cohort: str | None = None,
) -> Exam:
    exam = Exam(name=name, exam_date=exam_date, course=course, cohort=cohort, status=E_DRAFT)
    session.add(exam)
    session.flush()
    return exam


def exam_year(exam: Exam) -> int | None:
    if not exam.exam_date:
        return None
    try:
        return int(str(exam.exam_date)[:4])
    except ValueError:  # pragma: no cover - 書式違い
        return None


def set_exam_items(
    session: Session, exam: Exam, assignments: Sequence[tuple[int, int]]
) -> list[ExamItem]:
    """``(position, qversion_id)`` の並びで出題を差し替える。

    ``correct_asked`` は版の ``correct`` から写す。**正答を書けるのは本アプリのみ**
    (設計書 §17)。
    """
    if exam.status != E_DRAFT:
        raise ExamLockedError(
            f"確定済みの試験({exam.status})は変更できません。設計書 §13.3 の変更ロック"
        )

    session.query(ExamItem).filter(ExamItem.exam_id == exam.id).delete()
    created: list[ExamItem] = []
    for position, qversion_id in assignments:
        version = session.get(QuestionVersion, qversion_id)
        if version is None:
            raise ValueError(f"版 {qversion_id} が見つかりません")
        item = ExamItem(
            exam_id=exam.id,
            position=position,
            qversion_id=version.id,
            correct_asked=normalize_correct(version.correct),
        )
        session.add(item)
        created.append(item)
    session.flush()
    return created


def exam_item_views(session: Session, exam: Exam) -> list[ExamItemView]:
    """finalize チェックに渡す素のビューを組み立てる。"""
    views: list[ExamItemView] = []
    for item in exam.items:
        version = session.get(QuestionVersion, item.qversion_id)
        question = session.get(Question, version.question_id)
        views.append(
            ExamItemView(
                position=item.position,
                question_id=question.id,
                qversion_id=version.id,
                status=question.status,
                stem_html=version.stem_html,
                correct=item.correct_asked,
                choice_set_id=version.choice_set_id,
            )
        )
    return views


def check_finalize(
    session: Session,
    exam: Exam,
    *,
    expected_positions: int | None = None,
    limits: ExamLimits = DEFAULT_LIMITS,
) -> FinalizeReport:
    """finalize 前チェックを走らせる(設計書 §13.3)。DB からの組み立てはここ。"""
    views = exam_item_views(session, exam)
    families = {v.question_id: derivation_family(session, v.question_id) for v in views}
    links = {v.choice_set_id: linked_set_ids(session, v.choice_set_id) for v in views}
    return finalize_checks(
        views,
        expected_positions=expected_positions,
        derivation_families=families,
        set_links=links,
        limits=limits,
    )


def finalize_exam(
    session: Session,
    exam: Exam,
    *,
    expected_positions: int | None = None,
    limits: ExamLimits = DEFAULT_LIMITS,
    ignore_warnings: bool = True,
) -> FinalizeReport:
    """チェックを通れば ``status='finalized'`` にする。

    警告(上限超過・配分ずれ)は既定では確定を妨げない。ブロック項目が 1 つでも
    あれば確定しない。
    """
    if exam.status != E_DRAFT:
        raise ExamLockedError(f"この試験は既に {exam.status} です")

    report = check_finalize(session, exam, expected_positions=expected_positions, limits=limits)
    if report.blocked or (not ignore_warnings and report.warnings):
        return report

    exam.status = E_FINALIZED
    session.flush()
    log.info("試験 %s を確定しました(%d 問)", exam.name, len(exam.items))
    return report


# ---------------------------------------------------------------------------
# 出力物の材料(設計書 §13.2)
# ---------------------------------------------------------------------------


def answer_key_pairs(session: Session, exam: Exam) -> list[tuple[int, str]]:
    """``(出題番号, 正答肢)``。正答キー CSV の材料(設計書 §10.1)。"""
    return [(item.position, item.correct_asked) for item in exam.items]


@dataclass(frozen=True)
class BookletSource:
    """冊子出力に渡す 1 設問(``io`` の ``BookletItem`` に写す)。"""

    position: int
    stem_html: str
    choices: list[str]
    render_overrides: list[str | None]
    image_path: str | None


def booklet_sources(session: Session, exam: Exam) -> list[BookletSource]:
    """出題順・印字順に解決した冊子の材料。"""
    out: list[BookletSource] = []
    for item in exam.items:
        version = session.get(QuestionVersion, item.qversion_id)
        cset = session.get(ChoiceSet, version.choice_set_id)
        by_no = {i.item_no: i for i in cset.items}
        printed = ordered_items(cset.items_by_no(), version.choice_order)
        out.append(
            BookletSource(
                position=item.position,
                stem_html=version.stem_html,
                choices=[html for _, _, html in printed],
                render_overrides=[by_no[no].render_override for _, no, _ in printed],
                image_path=version.image_path,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 統計の付与(設計書 §9)
# ---------------------------------------------------------------------------


class StatsRowLike:  # pragma: no cover - 型注記用
    position: int
    correct: str
    disc: float | None

    def counts(self) -> dict[str, int]: ...


@dataclass
class StatsImportResult:
    written: int = 0
    flagged: list[tuple[int, list[str]]] = None  # (position, flags)

    def __post_init__(self) -> None:
        if self.flagged is None:
            self.flagged = []


def apply_stats(
    session: Session,
    exam: Exam,
    rows: Sequence[StatsRowLike],
    *,
    source_file: str = "",
    disc_type: str | None = None,
    n_examinees: int | None = None,
    thresholds: FlagThresholds = DEFAULT_THRESHOLDS,
) -> StatsImportResult:
    """検証済みの集計行を ``item_pattern_counts`` と ``item_stats`` に書く。

    **呼ぶ前に ``core.validate.validate_stats_import`` を通すこと。** ここは
    検証しない(ブロック項目のあるデータを書かせないのは呼び出し側の責任)。
    取込完了で ``status='imported'`` に遷移する(設計書 §9.2)。
    """
    if exam.status not in (E_FINALIZED, E_IMPORTED):
        raise ValueError(
            f"統計を与えられるのは確定済みの試験だけです(現在 {exam.status})。設計書 §9.1"
        )

    by_position = {item.position: item for item in exam.items}
    result = StatsImportResult()
    imported_at = utcnow_iso()

    session.query(ItemPatternCount).filter(ItemPatternCount.exam_id == exam.id).delete()
    session.query(ItemStatRow).filter(ItemStatRow.exam_id == exam.id).delete()

    for row in rows:
        item = by_position.get(row.position)
        if item is None:
            log.warning("出題番号 %s は試験にありません。飛ばします", row.position)
            continue
        version = session.get(QuestionVersion, item.qversion_id)
        counts = row.counts()

        for pattern, count in counts.items():
            session.add(
                ItemPatternCount(
                    exam_id=exam.id,
                    qversion_id=version.id,
                    pattern=pattern,
                    count=count,
                )
            )

        item_type = derive_item_type_detail(version.stem_html).item_type
        stats = derive_item_stats(
            counts,
            item.correct_asked,
            item_type,
            disc=row.disc,
            disc_type=disc_type,
        )
        flags = compute_flags(
            stats,
            thresholds=thresholds,
            emphasis_violation=bool(emphasis_issues(session, version)),
            prior_discs=prior_discs(session, version, exam),
        )
        session.add(_stat_row(exam.id, version.id, stats, flags, imported_at, source_file))
        result.written += 1
        if flags:
            result.flagged.append((item.position, flags))

    if n_examinees is not None:
        exam.n_examinees = n_examinees
    exam.status = E_IMPORTED
    session.flush()
    log.info("試験 %s に統計を取り込みました(%d 問)", exam.name, result.written)
    return result


def _stat_row(
    exam_id: int,
    qversion_id: int,
    stats: ItemStats,
    flags: Sequence[str],
    imported_at: str,
    source_file: str,
) -> ItemStatRow:
    return ItemStatRow(
        exam_id=exam_id,
        qversion_id=qversion_id,
        n=stats.n,
        n_correct=stats.n_correct,
        p=stats.p,
        disc=stats.disc,
        disc_type=stats.disc_type,
        sel_a=stats.sel["a"],
        sel_b=stats.sel["b"],
        sel_c=stats.sel["c"],
        sel_d=stats.sel["d"],
        sel_e=stats.sel["e"],
        blank_rate=stats.blank_rate,
        overselect_rate=stats.overselect_rate,
        top_wrong_pattern=stats.top_wrong_pattern,
        top_wrong_count=stats.top_wrong_count,
        flags=encode_flags(flags),
        imported_at=imported_at,
        source_file=source_file,
    )


def emphasis_issues(session: Session, version: QuestionVersion) -> list[ValidationIssue]:
    """強調規則の違反(設計書 §4)。``emphasis_rule`` フラグの材料。"""
    cset = session.get(ChoiceSet, version.choice_set_id)
    return check_emphasis_rule(version.stem_html, cset.item_htmls() if cset else [])


def prior_discs(session: Session, version: QuestionVersion, exam: Exam) -> list[float]:
    """同じ**問題**の過去の識別係数(今回を除く)。

    ``persistent_low_disc`` は単年値では判断しないため、版をまたいで集める
    (設計書 §12)。
    """
    rows = session.execute(
        select(ItemStatRow.disc, Exam.exam_date)
        .join(QuestionVersion, QuestionVersion.id == ItemStatRow.qversion_id)
        .join(Exam, Exam.id == ItemStatRow.exam_id)
        .where(
            QuestionVersion.question_id == version.question_id,
            ItemStatRow.exam_id != exam.id,
            ItemStatRow.disc.is_not(None),
        )
        .order_by(Exam.exam_date)
    ).all()
    return [float(disc) for disc, _ in rows]


def flagged_after_import(session: Session, exam: Exam) -> list[tuple[int, int, list[str]]]:
    """取込後に並べるフラグ一覧 ``(出題番号, question_id, フラグ)``(設計書 §9.3)。

    ここから直接「この問題を改訂する」に進める導線を UI が置く(設計書 §2.6)。
    """
    out: list[tuple[int, int, list[str]]] = []
    for item in exam.items:
        stat = session.get(ItemStatRow, (exam.id, item.qversion_id))
        if stat is None or not stat.flags:
            continue
        version = session.get(QuestionVersion, item.qversion_id)
        out.append((item.position, version.question_id, decode_flags(stat.flags)))
    return out


# ---------------------------------------------------------------------------
# 候補の組み立て(設計書 §13.1)
# ---------------------------------------------------------------------------


def build_candidates(session: Session, *, exclude_exam_ids: Iterable[int] = ()) -> list[Candidate]:
    """バンクの各問題の**最新版**を選定候補にする。

    露出履歴・直近の統計・フラグを添えて返す。統計が無い問題は「新作」扱いになり、
    ``no_stats`` フラグが付く(設計書 §12)。
    """
    excluded = set(exclude_exam_ids)
    out: list[Candidate] = []

    for question in session.scalars(select(Question)).all():
        version = question.latest_version
        if version is None:
            continue

        history = session.execute(
            select(
                ExamItem.exam_id, Exam.exam_date, ItemStatRow.p, ItemStatRow.disc, ItemStatRow.flags
            )
            .join(QuestionVersion, QuestionVersion.id == ExamItem.qversion_id)
            .join(Exam, Exam.id == ExamItem.exam_id)
            .outerjoin(
                ItemStatRow,
                (ItemStatRow.exam_id == ExamItem.exam_id)
                & (ItemStatRow.qversion_id == ExamItem.qversion_id),
            )
            .where(QuestionVersion.question_id == question.id)
            .order_by(Exam.exam_date)
        ).all()
        history = [h for h in history if h[0] not in excluded]

        years = [int(str(date)[:4]) for _, date, _, _, _ in history if date]
        latest_with_stats = next(
            ((p, disc, flags) for _, _, p, disc, flags in reversed(history) if p is not None),
            None,
        )
        flags = set(decode_flags(latest_with_stats[2]) if latest_with_stats else [])
        if latest_with_stats is None:
            flags.add(FLAG_NO_STATS)
        if emphasis_issues(session, version):
            flags.add(FLAG_EMPHASIS_RULE)

        out.append(
            Candidate(
                question_id=question.id,
                qversion_id=version.id,
                stem_html=version.stem_html,
                correct=version.correct,
                choice_set_id=version.choice_set_id,
                status=question.status,
                tags=frozenset(tag_names(session, question.id)),
                last_exam_year=max(years) if years else None,
                times_used=len(history),
                p=latest_with_stats[0] if latest_with_stats else None,
                disc=latest_with_stats[1] if latest_with_stats else None,
                flags=frozenset(flags),
            )
        )
    out.sort(key=lambda c: c.question_id)
    return out


def selection_context(
    session: Session, candidates: Sequence[Candidate]
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """``select_candidates`` に渡す派生系譜と近似リンクの対応表。"""
    families = {c.question_id: derivation_family(session, c.question_id) for c in candidates}
    links = {c.choice_set_id: linked_set_ids(session, c.choice_set_id) for c in candidates}
    return families, links


def exam_summary(session: Session, exam: Exam) -> Mapping[str, object]:
    """一覧表示・レポート見出し用の要約。"""
    n_items = session.scalar(
        select(func.count()).select_from(ExamItem).where(ExamItem.exam_id == exam.id)
    )
    return {
        "id": exam.id,
        "name": exam.name,
        "exam_date": exam.exam_date,
        "status": exam.status,
        "n_items": n_items or 0,
        "n_examinees": exam.n_examinees,
    }
