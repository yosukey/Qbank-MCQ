"""度数からの導出指標とフラグ判定。すべて純関数(実装計画 §5)。

設計書 §12 の方針: 保存するのは度数 (``item_pattern_counts``) であり、
正答率・周辺マーク率・最頻誤答パターンなどは**すべて導出する**。
``item_type`` 列・``n_select`` 列・否定形フラグ列は持たない(設計書 §8)。

識別係数だけは受験者ごとの合計点を要するため導出できず、CSV からインポートする。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from .typing_rules import LABELS, REQUIRED_COUNT, TYPE_XX, normalize_correct

#: 無回答は ``pattern=''`` で表す(設計書 §8 の ``item_pattern_counts`` 注記)。
BLANK = ""

#: 集計 CSV での無回答列の見出し(設計書 §10.2)。
BLANK_COLUMN = "空白"


def all_patterns() -> tuple[str, ...]:
    """31 通りのマークパターンを設計書 §10.2 の列順で返す。

    長さ 1 → 5 の順、同じ長さの中は a<b<c<d<e の辞書順。合計 5+10+10+5+1 = 31。
    """
    out: list[str] = []
    for size in range(1, len(LABELS) + 1):
        out.extend("".join(c) for c in combinations(LABELS, size))
    return tuple(out)


PATTERNS: tuple[str, ...] = all_patterns()
PATTERN_SET: frozenset[str] = frozenset(PATTERNS)


def pattern_columns() -> tuple[str, ...]:
    """CSV の度数列見出し 32 個(31 パターン + 空白)。"""
    return (*PATTERNS, BLANK_COLUMN)


@dataclass(frozen=True)
class FlagThresholds:
    """フラグ判定の閾値。設定画面から変更できる(設計書 §14-10)。"""

    #: 周辺マーク率がこれ未満の錯乱肢を「死んだ選択肢」とみなす(設計書 §12)。
    dead_distractor_rate: float = 0.05
    #: 指示個数違反率がこれを超えたら ``overselect``。設計書に数値指定がないため既定値。
    overselect_rate: float = 0.10
    #: ``persistent_low_disc`` の識別係数下限(設計書 §12)。
    low_disc: float = 0.10
    #: 何回続けて下回ったら ``persistent_low_disc`` とするか(「複数回にわたり」)。
    persistent_min_exams: int = 2


DEFAULT_THRESHOLDS = FlagThresholds()

FLAG_NEGATIVE_DISC = "negative_disc"
FLAG_PERSISTENT_LOW_DISC = "persistent_low_disc"
FLAG_DOMINANT_WRONG = "dominant_wrong"
FLAG_DEAD_DISTRACTOR = "dead_distractor"
FLAG_OVERSELECT = "overselect"
FLAG_EMPHASIS_RULE = "emphasis_rule"
FLAG_NO_STATS = "no_stats"


@dataclass
class ItemStats:
    """1 設問 1 試験ぶんの導出結果。``item_stats`` テーブルに対応する。"""

    n: int
    n_correct: int
    p: float
    correct: str
    item_type: str | None
    sel: dict[str, float]
    blank_rate: float
    overselect_rate: float | None
    top_wrong_pattern: str | None
    top_wrong_count: int
    partial: dict[int, int]
    disc: float | None = None
    disc_type: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def distractors(self) -> tuple[str, ...]:
        """錯乱肢(正答でない印字記号)。"""
        return tuple(c for c in LABELS if c not in self.correct)

    def dead_distractors(self, thresholds: FlagThresholds = DEFAULT_THRESHOLDS) -> tuple[str, ...]:
        """周辺マーク率が閾値未満の錯乱肢。"""
        return tuple(c for c in self.distractors if self.sel[c] < thresholds.dead_distractor_rate)


def _validate_counts(counts: Mapping[str, int]) -> None:
    unknown = sorted(set(counts) - PATTERN_SET - {BLANK})
    if unknown:
        raise ValueError(f"未知のパターンです: {unknown}")
    bad = sorted(k for k, v in counts.items() if not isinstance(v, int) or v < 0)
    if bad:
        raise ValueError(f"度数は非負整数です。不正な列: {bad}")


def derive_item_stats(
    counts: Mapping[str, int],
    correct: str,
    item_type: str | None,
    *,
    disc: float | None = None,
    disc_type: str | None = None,
    overselect_includes_blank: bool = False,
) -> ItemStats:
    """32 列の度数から設計書 §12 の導出指標を計算する。

    ``overselect_includes_blank``
        指示個数違反率に無回答を含めるか。**既定は含めない。** 無回答は
        ``blank_rate`` として別に持っており、``overselect`` フラグの意味は
        「指示・設問文が誤解されている疑い」なので、答えなかった者を
        混ぜると解釈がぼやけるため。含める運用にも切り替えられるようにしてある。
    """
    _validate_counts(counts)
    correct = normalize_correct(correct)
    if not correct:
        raise ValueError("正答が空です")

    n = sum(counts.values())
    if n == 0:
        raise ValueError("度数の合計が 0 です")

    n_correct = counts.get(correct, 0)
    # 設計書 §12 / 実装計画 §11: 正答率は CSV の丸め値ではなく必ず再計算する。
    p = n_correct / n
    blank_rate = counts.get(BLANK, 0) / n

    sel = {
        label: sum(c for pat, c in counts.items() if pat and label in pat) / n for label in LABELS
    }

    correct_set = set(correct)
    partial: dict[int, int] = {}
    for pat, c in counts.items():
        if c == 0:
            continue
        hits = len(correct_set & set(pat))
        partial[hits] = partial.get(hits, 0) + c

    # 最頻誤答パターン: 正答以外で度数最大。無回答は誤答パターンではなく
    # blank_rate として別に扱うため除く。同数のときは §10.2 の列順で先のものを採る。
    top_wrong_pattern: str | None = None
    top_wrong_count = 0
    for pat in PATTERNS:
        if pat == correct:
            continue
        c = counts.get(pat, 0)
        if c > top_wrong_count:
            top_wrong_pattern, top_wrong_count = pat, c

    overselect_rate: float | None = None
    if item_type is not None and item_type != TYPE_XX:
        # 設計書 §12: XX では算出しない(任意個数を取るため違反が定義できない)。
        need = REQUIRED_COUNT.get(item_type)
        if need is not None:
            violating = sum(
                c
                for pat, c in counts.items()
                if (pat != BLANK or overselect_includes_blank) and len(pat) != need
            )
            overselect_rate = violating / n

    return ItemStats(
        n=n,
        n_correct=n_correct,
        p=p,
        correct=correct,
        item_type=item_type,
        sel=sel,
        blank_rate=blank_rate,
        overselect_rate=overselect_rate,
        top_wrong_pattern=top_wrong_pattern,
        top_wrong_count=top_wrong_count,
        partial=partial,
        disc=disc,
        disc_type=disc_type,
    )


def disc_resolution(n: int, upper_lower_fraction: float = 0.25) -> float | None:
    """識別係数の分解能 ``1 / floor(N × 0.25)``(設計書 §9.2-6、§12)。

    N=139 なら floor(34.75)=34 で 1/34 ≒ 0.0294。上位/下位群の人数が刻み幅を決める。
    """
    group = math.floor(n * upper_lower_fraction)
    if group <= 0:
        return None
    return 1.0 / group


def is_disc_on_grid(disc: float, n: int, *, tolerance: float = 1e-3) -> bool:
    """識別係数が分解能の整数倍になっているか(設計書 §9.2-6 は警告扱い)。

    CSV の識別係数は小数第 3 位までに丸められて届く(設計書 §10.2 の例は ``0.529``、
    真値は 18/34 = 0.52941…)。既定の許容差 1e-3 はその丸めぶんを吸収する。
    """
    step = disc_resolution(n)
    if step is None:
        return False
    nearest = round(disc / step) * step
    return abs(disc - nearest) <= tolerance


def compute_flags(
    stats: ItemStats,
    *,
    thresholds: FlagThresholds = DEFAULT_THRESHOLDS,
    emphasis_violation: bool = False,
    prior_discs: Sequence[float] = (),
) -> list[str]:
    """設計書 §12 の自動フラグを判定する。

    ``prior_discs`` は同じ問題の過去の識別係数(今回を含めない)。
    ``persistent_low_disc`` は**単年値では判断しない**ため、これを使う。
    """
    flags: list[str] = []

    if stats.disc is not None and stats.disc < 0:
        flags.append(FLAG_NEGATIVE_DISC)

    if stats.disc is not None:
        history = [*prior_discs, stats.disc]
        low = [d for d in history if d < thresholds.low_disc]
        if len(history) >= thresholds.persistent_min_exams and len(low) == len(history):
            flags.append(FLAG_PERSISTENT_LOW_DISC)

    if stats.top_wrong_count > stats.n_correct:
        flags.append(FLAG_DOMINANT_WRONG)

    if stats.dead_distractors(thresholds):
        flags.append(FLAG_DEAD_DISTRACTOR)

    if stats.overselect_rate is not None and stats.overselect_rate > thresholds.overselect_rate:
        flags.append(FLAG_OVERSELECT)

    if emphasis_violation:
        flags.append(FLAG_EMPHASIS_RULE)

    return flags


def encode_flags(flags: Iterable[str]) -> str:
    """``item_stats.flags`` へ保存する形。空なら空文字。"""
    return ",".join(sorted(set(flags)))


def decode_flags(value: str | None) -> list[str]:
    """``item_stats.flags`` を読み戻す。"""
    return [f for f in (value or "").split(",") if f]


# ---------------------------------------------------------------------------
# 試験全体 (exam_stats)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExamScoreStats:
    n: int
    mean: float
    sd: float
    median: float


def derive_exam_stats(scores: Sequence[float]) -> ExamScoreStats:
    """受験者ごとの合計点から ``exam_stats`` の値を作る。

    合計点が手元にない運用(集計 CSV は設問別の度数のみ)ではこの関数は使わない。
    """
    if not scores:
        raise ValueError("スコアが空です")
    return ExamScoreStats(
        n=len(scores),
        mean=statistics.fmean(scores),
        sd=statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        median=statistics.median(scores),
    )


# ---------------------------------------------------------------------------
# 選択肢アイテム単位の実績(設計書 §6.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemAppearance:
    """ある選択肢項目が 1 回出題されたときの実績。

    ``co_items`` は同時に出題された他の 4 項目(項目 ID → その周辺マーク率)。
    """

    text_html: str
    exam_id: int
    qversion_id: int
    was_correct: bool
    #: この項目の周辺マーク率
    mark_rate: float
    #: この項目が正答だったときの、その設問の正答率
    question_p: float | None
    co_items: dict[str, float] = field(default_factory=dict)


@dataclass
class ItemPerformance:
    """用語レベルの実績(設計書 §6.5 の表示例に対応)。"""

    text_html: str
    appearances: int
    as_correct: int
    as_distractor: int
    median_p_when_correct: float | None
    median_mark_rate_when_distractor: float | None
    top_confused_with: str | None
    top_confused_count: int
    co_occurrences: int


def aggregate_item_performance(appearances: Iterable[ItemAppearance]) -> list[ItemPerformance]:
    """出題実績を用語ごとに集約する。

    「最も混同される相手」は、**この項目が正答だった出題**において、錯乱肢のうち
    周辺マーク率が最大だった相手を数え、最も多く「誤選択の主軸」になった項目とする。
    """
    by_text: dict[str, list[ItemAppearance]] = {}
    for a in appearances:
        by_text.setdefault(a.text_html, []).append(a)

    out: list[ItemPerformance] = []
    for text, apps in by_text.items():
        correct_apps = [a for a in apps if a.was_correct]
        distractor_apps = [a for a in apps if not a.was_correct]

        ps = [a.question_p for a in correct_apps if a.question_p is not None]
        rates = [a.mark_rate for a in distractor_apps]

        confusion: dict[str, int] = {}
        co_seen: dict[str, int] = {}
        for a in correct_apps:
            for other in a.co_items:
                co_seen[other] = co_seen.get(other, 0) + 1
            if not a.co_items:
                continue
            # 同率のときは用語順で決めて結果を決定的にする。
            leader = max(sorted(a.co_items), key=lambda k: a.co_items[k])
            confusion[leader] = confusion.get(leader, 0) + 1

        top_partner, top_count = None, 0
        if confusion:
            top_partner = max(sorted(confusion), key=lambda k: confusion[k])
            top_count = confusion[top_partner]

        out.append(
            ItemPerformance(
                text_html=text,
                appearances=len(apps),
                as_correct=len(correct_apps),
                as_distractor=len(distractor_apps),
                median_p_when_correct=statistics.median(ps) if ps else None,
                median_mark_rate_when_distractor=statistics.median(rates) if rates else None,
                top_confused_with=top_partner,
                top_confused_count=top_count,
                co_occurrences=co_seen.get(top_partner, 0) if top_partner else 0,
            )
        )
    out.sort(key=lambda p: (-p.appearances, p.text_html))
    return out
