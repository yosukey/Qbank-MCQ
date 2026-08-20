"""レポートの行モデルと集計(設計書 §13.2、§4-(2))。

``io.xlsx_report`` はここで組み立てた行を書き出すだけにして、集計そのものは
``core`` に置く(実装計画 §5 の層分け)。

設計書 §4-(2) の要求が効いてくるのがここ:

    否定形/肯定形で層別した集計を分析レポートに含める。「この問題が難しいのは
    内容のせいか、否定形だからか」を切り分ける材料になる。

**難易度という導出指標は持たず、正答率は必ずタイプと併記する**(設計書 §12)。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .bank import tag_names
from .db import ChoiceSet, Exam, ExamItem, ItemPatternCount, ItemStatRow, Question, QuestionVersion
from .stats import PATTERNS, decode_flags
from .typing_rules import derive_item_type_detail, is_negative


@dataclass(frozen=True)
class CrosswalkRow:
    """教員用照合表の 1 行(設計書 §13.2)。"""

    position: int
    question_id: int
    version_no: int
    item_type: str | None
    negative: bool
    is_new: bool
    correct: str
    tags: str
    last_exam_year: int | None
    last_p: float | None
    choice_set_id: int


@dataclass(frozen=True)
class ReportRow:
    """統計レポートの 1 行。"""

    position: int
    question_id: int
    version_no: int
    item_type: str | None
    negative: bool
    correct: str
    tags: str
    n: int | None
    n_correct: int | None
    p: float | None
    disc: float | None
    sel: dict[str, float | None]
    blank_rate: float | None
    overselect_rate: float | None
    top_wrong_pattern: str | None
    top_wrong_count: int | None
    flags: list[str]
    prev_p: float | None = None
    prev_disc: float | None = None
    #: 誤答パターン上位。``(pattern, count)`` の降順(設計書 §14-3)。
    top_wrong: list[tuple[str, int]] = field(default_factory=list)

    @property
    def p_label(self) -> str:
        """正答率は必ずタイプと併記する(設計書 §12, §13.1)。"""
        if self.p is None:
            return f"—({self.item_type or '?'})"
        return f"{self.p:.0%}({self.item_type or '?'})"

    @property
    def delta_p(self) -> float | None:
        if self.p is None or self.prev_p is None:
            return None
        return self.p - self.prev_p


@dataclass(frozen=True)
class Stratum:
    """層別集計の 1 区分。"""

    name: str
    n_items: int
    mean_p: float | None
    median_p: float | None
    mean_disc: float | None

    @classmethod
    def of(cls, name: str, rows: Sequence[ReportRow]) -> Stratum:
        ps = [r.p for r in rows if r.p is not None]
        ds = [r.disc for r in rows if r.disc is not None]
        return cls(
            name=name,
            n_items=len(rows),
            mean_p=statistics.fmean(ps) if ps else None,
            median_p=statistics.median(ps) if ps else None,
            mean_disc=statistics.fmean(ds) if ds else None,
        )


def stratify_by_negative(rows: Sequence[ReportRow]) -> list[Stratum]:
    """否定形/肯定形で層別する(設計書 §4-(2))。"""
    return [
        Stratum.of("否定形", [r for r in rows if r.negative]),
        Stratum.of("肯定形", [r for r in rows if not r.negative]),
        Stratum.of("全体", list(rows)),
    ]


def stratify_by_type(rows: Sequence[ReportRow]) -> list[Stratum]:
    """タイプ別に層別する。正答率をタイプ抜きで比べないための区分。"""
    order = ["A", "X2", "X3", "X4", "XX"]
    present = [t for t in order if any(r.item_type == t for r in rows)]
    present += sorted({r.item_type or "?" for r in rows} - set(order))
    return [Stratum.of(t, [r for r in rows if (r.item_type or "?") == t]) for t in present]


# ---------------------------------------------------------------------------
# DB からの組み立て
# ---------------------------------------------------------------------------


def _previous_stats(
    session: Session, question_id: int, exam: Exam
) -> tuple[float | None, float | None]:
    """同じ問題の前回の正答率・識別係数(この試験より前で最新のもの)。"""
    rows = session.execute(
        select(ItemStatRow.p, ItemStatRow.disc, Exam.exam_date)
        .join(QuestionVersion, QuestionVersion.id == ItemStatRow.qversion_id)
        .join(Exam, Exam.id == ItemStatRow.exam_id)
        .where(QuestionVersion.question_id == question_id, ItemStatRow.exam_id != exam.id)
        .order_by(Exam.exam_date)
    ).all()
    earlier = [r for r in rows if not exam.exam_date or (r[2] or "") < exam.exam_date]
    if not earlier:
        return None, None
    p, disc, _ = earlier[-1]
    return p, disc


def crosswalk_rows(session: Session, exam: Exam) -> list[CrosswalkRow]:
    """教員用照合表の材料。前回出題年・前回正答率まで含める(設計書 §13.2)。"""
    out: list[CrosswalkRow] = []
    for item in exam.items:
        version = session.get(QuestionVersion, item.qversion_id)
        question = session.get(Question, version.question_id)
        prev_p, _ = _previous_stats(session, question.id, exam)

        years = session.execute(
            select(Exam.exam_date)
            .join(ExamItem, ExamItem.exam_id == Exam.id)
            .join(QuestionVersion, QuestionVersion.id == ExamItem.qversion_id)
            .where(QuestionVersion.question_id == question.id, Exam.id != exam.id)
            .order_by(Exam.exam_date)
        ).all()
        parsed_years = [int(str(d)[:4]) for (d,) in years if d]

        out.append(
            CrosswalkRow(
                position=item.position,
                question_id=question.id,
                version_no=version.version_no,
                item_type=derive_item_type_detail(version.stem_html).item_type,
                negative=is_negative(version.stem_html),
                is_new=prev_p is None,
                correct=item.correct_asked,
                tags="、".join(tag_names(session, question.id)),
                last_exam_year=max(parsed_years) if parsed_years else None,
                last_p=prev_p,
                choice_set_id=version.choice_set_id,
            )
        )
    return out


def report_rows(session: Session, exam: Exam, *, top_wrong_limit: int = 5) -> list[ReportRow]:
    """統計レポートの材料。誤答パターン上位 5 件を含める(設計書 §14-3)。"""
    out: list[ReportRow] = []
    for item in exam.items:
        version = session.get(QuestionVersion, item.qversion_id)
        question = session.get(Question, version.question_id)
        stat = session.get(ItemStatRow, (exam.id, version.id))
        prev_p, prev_disc = _previous_stats(session, question.id, exam)

        counts = {
            pattern: count
            for pattern, count in session.execute(
                select(ItemPatternCount.pattern, ItemPatternCount.count).where(
                    ItemPatternCount.exam_id == exam.id,
                    ItemPatternCount.qversion_id == version.id,
                )
            ).all()
        }
        correct = item.correct_asked
        wrong = sorted(
            ((p, c) for p, c in counts.items() if p and p != correct and c),
            key=lambda pc: (-pc[1], PATTERNS.index(pc[0])),
        )[:top_wrong_limit]

        out.append(
            ReportRow(
                position=item.position,
                question_id=question.id,
                version_no=version.version_no,
                item_type=derive_item_type_detail(version.stem_html).item_type,
                negative=is_negative(version.stem_html),
                correct=correct,
                tags="、".join(tag_names(session, question.id)),
                n=stat.n if stat else None,
                n_correct=stat.n_correct if stat else None,
                p=stat.p if stat else None,
                disc=stat.disc if stat else None,
                sel={label: getattr(stat, f"sel_{label}") if stat else None for label in "abcde"},
                blank_rate=stat.blank_rate if stat else None,
                overselect_rate=stat.overselect_rate if stat else None,
                top_wrong_pattern=stat.top_wrong_pattern if stat else None,
                top_wrong_count=stat.top_wrong_count if stat else None,
                flags=decode_flags(stat.flags) if stat else [],
                prev_p=prev_p,
                prev_disc=prev_disc,
                top_wrong=wrong,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 問題詳細の材料(設計書 §14-3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Appearance:
    """ある問題が 1 回出題されたときの記録と、そのときの統計。

    版をまたいで並べる。**改訂すると新版は実績ゼロから始まる**(設計書 §2.2)ので、
    推移を見るときは版の切れ目が分かる必要がある。
    """

    exam_id: int
    exam_name: str | None
    exam_date: str | None
    position: int
    qversion_id: int
    version_no: int
    correct_asked: str
    n: int | None
    p: float | None
    disc: float | None
    sel: dict[str, float | None]
    blank_rate: float | None
    overselect_rate: float | None
    flags: list[str]
    #: マークパターン度数(無回答は ``''``)。統計未取込なら空。
    counts: dict[str, int]

    @property
    def year(self) -> int | None:
        return int(str(self.exam_date)[:4]) if self.exam_date else None

    @property
    def has_stats(self) -> bool:
        return self.p is not None

    def top_wrong(self, limit: int = 5) -> list[tuple[str, int]]:
        """誤答パターン上位(設計書 §14-3)。無回答は含めない。"""
        return sorted(
            ((p, c) for p, c in self.counts.items() if p and p != self.correct_asked and c),
            key=lambda pc: (-pc[1], PATTERNS.index(pc[0])),
        )[:limit]

    def partial(self) -> dict[int, int]:
        """部分正答分布(正答を何個当てたか → 人数)。設計書 §14-3。

        ``item_type`` は渡さない。部分正答分布はタイプに依らず度数だけで決まり、
        ここで導出し直すとタイプの取り違えを持ち込むだけになるため。
        """
        if not self.counts:
            return {}
        from .stats import derive_item_stats

        return derive_item_stats(self.counts, self.correct_asked, None).partial


@dataclass(frozen=True)
class QuestionHistory:
    """問題詳細に出す一式(版履歴・派生系譜・出題実績)。"""

    question_id: int
    status: str
    versions: list[QuestionVersion]
    appearances: list[Appearance]
    #: 派生元の版(無ければ None)。設計書 §2.3。
    parent: QuestionVersion | None
    #: この問題から派生した問題。
    children: list[Question]

    @property
    def latest(self) -> QuestionVersion | None:
        return max(self.versions, key=lambda v: v.version_no) if self.versions else None

    def with_stats(self) -> list[Appearance]:
        return [a for a in self.appearances if a.has_stats]


def question_history(session: Session, question_id: int) -> QuestionHistory:
    """問題詳細画面の材料をまとめて集める(設計書 §14-3)。"""
    from .bank import derivation_parent, derived_children

    question = session.get(Question, question_id)
    if question is None:
        raise ValueError(f"問題 {question_id} がありません")

    versions = sorted(question.versions, key=lambda v: v.version_no)
    version_no = {v.id: v.version_no for v in versions}

    rows = session.execute(
        select(ExamItem.exam_id, ExamItem.position, ExamItem.qversion_id, ExamItem.correct_asked)
        .join(QuestionVersion, QuestionVersion.id == ExamItem.qversion_id)
        .where(QuestionVersion.question_id == question_id)
    ).all()

    appearances: list[Appearance] = []
    for exam_id, position, qversion_id, correct in rows:
        exam = session.get(Exam, exam_id)
        stat = session.get(ItemStatRow, (exam_id, qversion_id))
        counts = {
            pattern: count
            for pattern, count in session.execute(
                select(ItemPatternCount.pattern, ItemPatternCount.count).where(
                    ItemPatternCount.exam_id == exam_id,
                    ItemPatternCount.qversion_id == qversion_id,
                )
            ).all()
        }
        appearances.append(
            Appearance(
                exam_id=exam_id,
                exam_name=exam.name if exam else None,
                exam_date=exam.exam_date if exam else None,
                position=position,
                qversion_id=qversion_id,
                version_no=version_no.get(qversion_id, 0),
                correct_asked=correct,
                n=stat.n if stat else None,
                p=stat.p if stat else None,
                disc=stat.disc if stat else None,
                sel={label: getattr(stat, f"sel_{label}") if stat else None for label in "abcde"},
                blank_rate=stat.blank_rate if stat else None,
                overselect_rate=stat.overselect_rate if stat else None,
                flags=decode_flags(stat.flags) if stat else [],
                counts=counts,
            )
        )
    appearances.sort(key=lambda a: (a.exam_date or "", a.exam_id))

    return QuestionHistory(
        question_id=question_id,
        status=question.status,
        versions=versions,
        appearances=appearances,
        parent=derivation_parent(session, question),
        children=derived_children(session, question),
    )


# ---------------------------------------------------------------------------
# 選択肢セット画面の材料(設計書 §14-4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetLinkView:
    """近似セットの関連 1 本。"""

    other_id: int
    shared: int | None
    relation: str | None
    note: str | None


@dataclass(frozen=True)
class ChoiceSetSummary:
    """セット一覧の 1 行。"""

    set_id: int
    name: str | None
    items: list[tuple[int, str]]
    n_questions: int
    links: list[SetLinkView]

    def preview(self, limit: int = 4) -> str:
        from .text import strip_tags

        heads = [strip_tags(html) for _, html in self.items[:limit]]
        tail = "…" if len(self.items) > limit else ""
        return "、".join(heads) + tail


def choice_set_summaries(session: Session) -> list[ChoiceSetSummary]:
    """セット一覧と近似リンク(設計書 §14-4)。"""
    from .db import ChoiceSetLink

    counts = dict(
        session.execute(
            select(QuestionVersion.choice_set_id, func.count()).group_by(
                QuestionVersion.choice_set_id
            )
        ).all()
    )
    links: dict[int, list[SetLinkView]] = {}
    for link in session.scalars(select(ChoiceSetLink)).all():
        links.setdefault(link.set_a, []).append(
            SetLinkView(link.set_b, link.shared, link.relation, link.note)
        )
        links.setdefault(link.set_b, []).append(
            SetLinkView(link.set_a, link.shared, link.relation, link.note)
        )

    out: list[ChoiceSetSummary] = []
    for cset in session.scalars(select(ChoiceSet).order_by(ChoiceSet.id)).all():
        out.append(
            ChoiceSetSummary(
                set_id=cset.id,
                name=cset.name,
                items=[(item.item_no, item.text_html) for item in cset.items],
                n_questions=int(counts.get(cset.id, 0)),
                links=sorted(links.get(cset.id, []), key=lambda link: -(link.shared or 0)),
            )
        )
    return out


@dataclass(frozen=True)
class SetMatrixRow:
    """セット内の 1 設問 × 項目のマーク率(設計書 §14-4 のマトリクス)。"""

    question_id: int
    qversion_id: int
    version_no: int
    stem_html: str
    exam_id: int | None
    exam_date: str | None
    #: 項目番号 → 周辺マーク率。統計が無ければ空。
    rates: dict[int, float | None]
    #: 正答だった項目番号。
    correct_item_nos: tuple[int, ...]


def set_item_matrix(session: Session, choice_set_id: int) -> list[SetMatrixRow]:
    """セットを使った全設問について、項目ごとのマーク率を並べる(設計書 §14-4)。

    「この項目はどの問い方のときに効いたか」を横に並べて読むための表。
    順序が変わっても項目単位で追える(設計書 §6.4-5)ので、``choice_order`` を
    通して印字記号を項目番号に戻す。
    """
    from .choiceset import correct_to_item_nos, item_no_to_label

    out: list[SetMatrixRow] = []
    versions = session.scalars(
        select(QuestionVersion)
        .where(QuestionVersion.choice_set_id == choice_set_id)
        .order_by(QuestionVersion.question_id, QuestionVersion.version_no)
    ).all()

    for version in versions:
        appearance = session.execute(
            select(ExamItem.exam_id, Exam.exam_date)
            .join(Exam, Exam.id == ExamItem.exam_id)
            .where(ExamItem.qversion_id == version.id)
            .order_by(Exam.exam_date.desc())
        ).first()
        exam_id = appearance[0] if appearance else None
        stat = session.get(ItemStatRow, (exam_id, version.id)) if exam_id else None

        rates: dict[int, float | None] = {}
        for item_no in range(1, 6):
            label = item_no_to_label(item_no, version.choice_order)
            rates[item_no] = getattr(stat, f"sel_{label}") if stat else None

        out.append(
            SetMatrixRow(
                question_id=version.question_id,
                qversion_id=version.id,
                version_no=version.version_no,
                stem_html=version.stem_html,
                exam_id=exam_id,
                exam_date=appearance[1] if appearance else None,
                rates=rates,
                correct_item_nos=correct_to_item_nos(version.correct, version.choice_order),
            )
        )
    return out


def choice_item_appearances(session: Session):
    """選択肢アイテム単位の実績の材料(設計書 §6.5)。

    ``core.stats.aggregate_item_performance`` にそのまま渡せる形で返す。
    """
    from .choiceset import item_no_to_label
    from .stats import ItemAppearance

    out: list[ItemAppearance] = []
    rows = session.execute(
        select(ExamItem.exam_id, ExamItem.qversion_id, ExamItem.correct_asked)
    ).all()

    for exam_id, qversion_id, correct in rows:
        stat = session.get(ItemStatRow, (exam_id, qversion_id))
        if stat is None:
            continue
        version = session.get(QuestionVersion, qversion_id)
        cset = session.get(ChoiceSet, version.choice_set_id)
        if cset is None:
            continue
        marks = {label: getattr(stat, f"sel_{label}") or 0.0 for label in "abcde"}

        by_no = cset.items_by_no()
        for item_no, html in by_no.items():
            label = item_no_to_label(item_no, version.choice_order)
            was_correct = label in correct
            co_items = {
                other_html: marks[item_no_to_label(other_no, version.choice_order)]
                for other_no, other_html in by_no.items()
                if other_no != item_no
                and item_no_to_label(other_no, version.choice_order) not in correct
            }
            out.append(
                ItemAppearance(
                    text_html=html,
                    exam_id=exam_id,
                    qversion_id=qversion_id,
                    was_correct=was_correct,
                    mark_rate=marks[label],
                    question_p=stat.p if was_correct else None,
                    co_items=co_items,
                )
            )
    return out
