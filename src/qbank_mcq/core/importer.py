"""過去問一括取込の手順(設計書 §1.1 の局面A)。

CLI(``qbank import-exam``)と取込画面(設計書 §14-6)が**同じ経路を通る**ため、
手順そのものはここに置く。画面側に写すと、片方だけ直したときに登録内容が食い違う。

    docx ─┐
          ├─→ 相互検証 → バンクへ一括登録 → 試験として確定 → 統計を与える
   集計CSV ┘

呼び出し側がするのは、結果(``ImportReport``)を人に見せることだけ。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .bank import create_question_from_printed, find_duplicate_question, upsert_choice_set
from .db import Q_ACTIVE, Q_DRAFT, Exam
from .exam import apply_stats, create_exam, finalize_exam, flagged_after_import, set_exam_items
from .typing_rules import ValidationIssue
from .validate import FinalizeReport, validate_stats_import

log = logging.getLogger(__name__)


@dataclass
class ImportedQuestion:
    """docx の 1 設問を取り込んだ結果。"""

    number: int
    question_id: int | None = None
    qversion_id: int | None = None
    status: str = Q_ACTIVE
    #: 同じセット・同じ設問文の既存問題(設計書 §1.4 の二重登録防止)。
    duplicate_of: int | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def registered(self) -> bool:
        return self.qversion_id is not None


@dataclass
class ImportReport:
    """一括取込の結果一式。"""

    exam: Exam | None = None
    questions: list[ImportedQuestion] = field(default_factory=list)
    finalize: FinalizeReport | None = None
    #: 統計の検証チェーン(設計書 §9.2)。CSV を渡さなければ空。
    stats_issues: list[ValidationIssue] = field(default_factory=list)
    stats_written: int = 0
    flagged: list[tuple[int, int, list[str]]] = field(default_factory=list)
    #: 取り込めなかった理由。空なら成功。
    blocked_reason: str | None = None

    @property
    def registered(self) -> list[ImportedQuestion]:
        return [q for q in self.questions if q.registered]

    @property
    def duplicates(self) -> list[ImportedQuestion]:
        return [q for q in self.questions if q.duplicate_of is not None]

    @property
    def drafts(self) -> list[ImportedQuestion]:
        return [q for q in self.questions if q.status == Q_DRAFT]

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


def import_parsed_exam(
    session: Session,
    parsed: Any,
    stats_file: Any | None = None,
    *,
    name: str,
    exam_date: str | None = None,
    course: str | None = None,
    cohort: str | None = None,
    source_file: str = "",
    force: bool = False,
) -> ImportReport:
    """抽出済みの docx(と集計 CSV)をバンクに入れ、試験として確定する。

    ``force`` は統計の検証チェーンでブロックが出ても取り込む。**docx 側の相互検証は
    呼び出し側の責任**(``validate.cross_validate_import``)であり、ここは通ってきた
    ものとして扱う。

    正答が分からない設問は ``draft`` として登録する(設計書 §2.5)。捨てるより、
    後から正答を入れて有効化できるほうがよい。
    """
    correct_by_position = {r.position: r.correct for r in stats_file.rows} if stats_file else {}
    meta = stats_file.meta if stats_file else None

    report = ImportReport()
    report.exam = create_exam(
        session,
        name=name or (meta.exam_name if meta else "取込"),
        exam_date=exam_date or (meta.exam_date if meta else None),
        course=course,
        cohort=cohort,
    )

    assignments: list[tuple[int, int]] = []
    for question in parsed.questions:
        correct = correct_by_position.get(question.number)
        entry = ImportedQuestion(number=question.number, status=Q_ACTIVE if correct else Q_DRAFT)

        probe, _ = upsert_choice_set(session, list(question.choice_htmls))
        existing = find_duplicate_question(session, question.stem_html, probe.id)
        if existing is not None:
            entry.duplicate_of = existing.id

        result, _ = create_question_from_printed(
            session,
            stem_html=question.stem_html,
            printed_choices=list(question.choice_htmls),
            correct=correct or "a",
            status=entry.status,
            image_path=question.image_paths[0] if question.image_paths else None,
        )
        entry.issues = list(result.issues)
        if not result.blocked and result.version is not None:
            entry.question_id = result.question.id
            entry.qversion_id = result.version.id
            assignments.append((question.number, result.version.id))
        report.questions.append(entry)

    set_exam_items(session, report.exam, assignments)
    report.finalize = finalize_exam(session, report.exam)
    if report.finalize.blocked:
        report.blocked_reason = "finalize できなかったため統計は取り込みません"
        return report

    if stats_file is not None:
        _apply_stats(session, report, stats_file, source_file=source_file, force=force)
    return report


def _apply_stats(
    session: Session,
    report: ImportReport,
    stats_file: Any,
    *,
    source_file: str,
    force: bool,
) -> None:
    exam_items = {item.position: item.correct_asked for item in report.exam.items}
    report.stats_issues = validate_stats_import(
        stats_file.rows,
        exam_items,
        pattern_columns_found=stats_file.pattern_columns_found,
        missing_fixed_columns=stats_file.missing_fixed_columns,
        n_examinees=stats_file.meta.n_examinees,
        n_non_mcq=len(stats_file.non_mcq_rows),
    )
    if any(i.blocking for i in report.stats_issues) and not force:
        report.blocked_reason = "統計の検証に失敗したため取り込みませんでした"
        return

    result = apply_stats(
        session,
        report.exam,
        stats_file.rows,
        source_file=source_file,
        disc_type=stats_file.meta.disc_type,
        n_examinees=stats_file.meta.effective_n,
    )
    report.stats_written = result.written
    report.flagged = flagged_after_import(session, report.exam)
