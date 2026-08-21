"""出題候補の選定(設計書 §13.1、実装計画 §4 M5)。純関数のみ。

設計書 §13.1 が挙げる条件をすべて扱う:

- タイプ別配分 / 分野(タグ)別配分
- 識別係数の下限と ``negative_disc`` の自動除外
- 直近 n 年の出題除外 / 通算出題回数の少ないものを優先
- 新作(統計なし)問題の混入率
- 同一セットおよび近似セット(共通 4 項目以上)からの出題上限(既定 2 問)
- 派生関係にある問題の同時出題禁止
- 否定形設問の上限
- 正答率レンジ絞り込み(任意・既定オフ)

選定は**決定的**(乱数を使わない)。同じ条件・同じバンクなら常に同じ結果になるので、
候補を差し替えたときの影響が読める。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .stats import FLAG_NEGATIVE_DISC
from .typing_rules import derive_item_type_detail, is_negative
from .validate import ExamLimits

#: 「新作」= 統計がまだ無い問題(設計書 §12 の ``no_stats``)。


@dataclass(frozen=True)
class Candidate:
    """選定にかける 1 問(最新版)。DB から組み立てて渡す。"""

    question_id: int
    qversion_id: int
    stem_html: str
    correct: str
    choice_set_id: int
    status: str = "active"
    tags: frozenset[str] = frozenset()
    #: 直近に出題された年。未出題なら None。
    last_exam_year: int | None = None
    #: 通算出題回数。
    times_used: int = 0
    #: 直近の正答率・識別係数。統計が無ければ None。
    p: float | None = None
    disc: float | None = None
    flags: frozenset[str] = frozenset()

    @property
    def item_type(self) -> str | None:
        return derive_item_type_detail(self.stem_html).item_type

    @property
    def negative(self) -> bool:
        return is_negative(self.stem_html)

    @property
    def is_new(self) -> bool:
        """未出題または統計未取込(設計書 §12 の ``no_stats`` 相当)。"""
        return self.p is None

    def label(self) -> str:
        """候補一覧の表示。正答率は必ずタイプと併記する(設計書 §12, §13.1)。"""
        parts: list[str] = []
        if self.p is None:
            parts.append(f"新作({self.item_type or '?'})")
        else:
            parts.append(f"正答率{self.p:.0%}({self.item_type or '?'})")
        if self.disc is not None:
            parts.append(f"D={self.disc:.3f}")
        if self.negative:
            parts.append("否定形")
        return " ".join(parts)


@dataclass(frozen=True)
class SelectionConditions:
    """選定条件(設計書 §13.1)。"""

    total: int
    #: タイプ別配分。``{'A': 30, 'X2': 15}``。合計が ``total`` を超えないこと。
    type_distribution: Mapping[str, int] | None = None
    #: 分野(タグ)別配分。
    tag_distribution: Mapping[str, int] | None = None
    #: 識別係数の下限。統計のない問題には適用しない。
    min_disc: float | None = None
    #: ``negative_disc`` が付いた問題を自動除外する。
    exclude_negative_disc: bool = True
    #: 直近 n 年の出題を除外する。``current_year`` と併せて使う。
    exclude_recent_years: int | None = None
    current_year: int | None = None
    #: 新作の混入率(0.0〜1.0)。
    new_item_ratio: float | None = None
    #: 正答率レンジ。**既定はオフ**(設計書 §13.1)。
    p_range: tuple[float, float] | None = None
    #: 露出・否定形の上限。finalize と同じ既定値を使う。
    limits: ExamLimits = ExamLimits()


@dataclass
class SelectionResult:
    selected: list[Candidate] = field(default_factory=list)
    #: 満たせなかった条件の説明。
    unmet: list[str] = field(default_factory=list)
    #: 候補から外した理由。``question_id -> 理由``
    excluded: dict[int, str] = field(default_factory=dict)

    @property
    def by_type(self) -> dict[str, list[Candidate]]:
        """候補一覧はタイプ別にグループ化して見せる(設計書 §13.1)。"""
        groups: dict[str, list[Candidate]] = {}
        for c in self.selected:
            groups.setdefault(c.item_type or "?", []).append(c)
        return groups


def eligible(
    candidates: Sequence[Candidate], conditions: SelectionConditions
) -> tuple[list[Candidate], dict[int, str]]:
    """絞り込みだけを行い、``(残った候補, 除外理由)`` を返す。"""
    kept: list[Candidate] = []
    excluded: dict[int, str] = {}

    for c in candidates:
        if c.status != "active":
            excluded[c.question_id] = f"status={c.status}"
            continue
        if c.item_type is None:
            excluded[c.question_id] = "指示文言からタイプを導出できない"
            continue
        if conditions.exclude_negative_disc and FLAG_NEGATIVE_DISC in c.flags:
            # 正答設定ミス・二義性の疑い。まず点検すべきで、出題には使わない。
            excluded[c.question_id] = "negative_disc"
            continue
        if conditions.min_disc is not None and c.disc is not None and c.disc < conditions.min_disc:
            excluded[c.question_id] = f"識別係数 {c.disc:.3f} < {conditions.min_disc}"
            continue
        if conditions.p_range is not None and c.p is not None:
            low, high = conditions.p_range
            if not low <= c.p <= high:
                excluded[c.question_id] = f"正答率 {c.p:.0%} がレンジ外"
                continue
        if (
            conditions.exclude_recent_years is not None
            and conditions.current_year is not None
            and c.last_exam_year is not None
            and c.last_exam_year > conditions.current_year - conditions.exclude_recent_years
        ):
            excluded[c.question_id] = f"直近 {conditions.exclude_recent_years} 年に出題済み"
            continue
        kept.append(c)
    return kept, excluded


def _preference_key(c: Candidate) -> tuple:
    """通算出題回数の少ないものを優先し、次に古い出題を優先する。

    最後に ``question_id`` を入れて順序を決定的にする。
    """
    return (c.times_used, c.last_exam_year if c.last_exam_year is not None else -1, c.question_id)


def select_candidates(
    candidates: Sequence[Candidate],
    conditions: SelectionConditions,
    *,
    derivation_families: Mapping[int, set[int]] | None = None,
    set_links: Mapping[int, set[int]] | None = None,
) -> SelectionResult:
    """条件を満たす出題候補を選ぶ。

    貪欲法。優先順に見て、露出・派生・配分の制約に触れないものから採る。
    埋まらなかった枠は ``unmet`` に理由を残す(黙って少ない本数を返さない)。
    """
    result = SelectionResult()
    pool, result.excluded = eligible(candidates, conditions)
    pool.sort(key=_preference_key)

    families = derivation_families or {}
    links = set_links or {}
    limits = conditions.limits

    type_quota = dict(conditions.type_distribution or {})
    tag_quota = dict(conditions.tag_distribution or {})
    use_type_quota = bool(type_quota)
    use_tag_quota = bool(tag_quota)

    new_target = (
        round(conditions.total * conditions.new_item_ratio)
        if conditions.new_item_ratio is not None
        else None
    )

    chosen: list[Candidate] = []
    chosen_questions: set[int] = set()
    blocked_questions: set[int] = set()
    set_group_counts: dict[frozenset[int], int] = {}
    negative_count = 0
    new_count = 0

    def group_key(choice_set_id: int) -> frozenset[int]:
        """露出管理の単位。自セットと自動リンク先をひとまとまりにする。

        リンクは推移的に閉じないので(設計書 §6.3)、A-B と A-C があっても
        B と C は別の群になる。finalize 側の判定と同じ数え方。
        """
        return frozenset({choice_set_id, *links.get(choice_set_id, set())})

    def negative_allowed(c: Candidate) -> bool:
        if not c.negative:
            return True
        if limits.max_negative is not None and negative_count >= limits.max_negative:
            return False
        if limits.max_negative_ratio is not None:
            allowed = int(conditions.total * limits.max_negative_ratio)
            if negative_count >= allowed:
                return False
        return True

    def take(c: Candidate) -> None:
        nonlocal negative_count, new_count
        chosen.append(c)
        chosen_questions.add(c.question_id)
        blocked_questions.update(families.get(c.question_id, {c.question_id}))
        key = group_key(c.choice_set_id)
        set_group_counts[key] = set_group_counts.get(key, 0) + 1
        if c.negative:
            negative_count += 1
        if c.is_new:
            new_count += 1
        if use_type_quota and c.item_type in type_quota:
            type_quota[c.item_type] -= 1
        if use_tag_quota:
            for tag in sorted(c.tags):
                if tag_quota.get(tag, 0) > 0:
                    tag_quota[tag] -= 1
                    break

    def can_take(c: Candidate, *, require_new: bool | None = None) -> bool:
        if c.question_id in chosen_questions or c.question_id in blocked_questions:
            return False
        if len(chosen) >= conditions.total:
            return False
        if require_new is True and not c.is_new:
            return False
        if require_new is False and c.is_new:
            return False
        if new_target is not None and c.is_new and new_count >= new_target:
            return False
        if not negative_allowed(c):
            return False
        if limits.max_per_set_group:
            if set_group_counts.get(group_key(c.choice_set_id), 0) >= limits.max_per_set_group:
                return False
        if use_type_quota and type_quota.get(c.item_type, 0) <= 0:
            return False
        if use_tag_quota and not any(tag_quota.get(t, 0) > 0 for t in c.tags):
            return False
        return True

    # 1 周目: 新作の枠を先に埋める(後回しにすると埋まらないことがある)。
    if new_target:
        for c in pool:
            if len(chosen) >= conditions.total:
                break
            if can_take(c, require_new=True):
                take(c)

    # 2 周目: 残りを優先順に埋める。
    for c in pool:
        if len(chosen) >= conditions.total:
            break
        if can_take(c):
            take(c)

    result.selected = chosen

    if len(chosen) < conditions.total:
        result.unmet.append(
            f"{conditions.total} 問の指定に対し {len(chosen)} 問しか選べませんでした"
        )
    for item_type, remaining in sorted(type_quota.items()):
        if remaining > 0:
            result.unmet.append(f"タイプ {item_type} が {remaining} 問不足しています")
    for tag, remaining in sorted(tag_quota.items()):
        if remaining > 0:
            result.unmet.append(f"タグ {tag} が {remaining} 問不足しています")
    if new_target is not None and new_count < new_target:
        result.unmet.append(f"新作が {new_target - new_count} 問不足しています")
    return result


def assign_positions(selected: Sequence[Candidate]) -> list[tuple[int, Candidate]]:
    """選んだ候補に出題番号を振る。タイプ別にまとめ、A→X2→X3→X4→XX の順に並べる。"""
    order = {"A": 0, "X2": 1, "X3": 2, "X4": 3, "XX": 4}
    ordered = sorted(selected, key=lambda c: (order.get(c.item_type or "", 9), _preference_key(c)))
    return [(i + 1, c) for i, c in enumerate(ordered)]
