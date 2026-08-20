"""検証チェーン。純関数のみ(実装計画 §5)。

3 か所の検証をまとめて持つ:

1. **統計取込**(設計書 §9.2 の 9 項目)— 不一致は原則ブロック
2. **docx ⇔ 集計CSV の相互検証**(実装計画 §4 M3)
3. **finalize 前チェック**(設計書 §13.3)

DB に触らないよう、いずれも素のデータ構造を受け取る。組み立ては ``core.exam`` が行う。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .stats import BLANK, BLANK_COLUMN, OTHER, OTHER_COLUMN, PATTERNS, is_disc_on_grid
from .typing_rules import (
    REQUIRED_COUNT,
    TYPE_XX,
    ValidationIssue,
    check_emphasis_rule,
    derive_item_type_detail,
    is_negative,
    normalize_correct,
)

#: 正答率の突き合わせ許容差。CSV の正答率は小数第 4 位までで届く(設計書 §10.2)。
P_TOLERANCE = 1e-3


def _issue(code: str, message: str, blocking: bool = True, **context: object) -> ValidationIssue:
    return ValidationIssue(code, message, blocking, dict(context))


# ---------------------------------------------------------------------------
# 1. 統計取込の検証チェーン(設計書 §9.2)
# ---------------------------------------------------------------------------


class StatsRowLike(Protocol):
    """``io.csv_stats.StatsRow`` が満たす形。io に依存しないための最小の約束。"""

    position: int
    correct: str
    p_reported: float | None
    n_correct_reported: int | None
    disc: float | None
    counts_raw: dict[str, float]
    unreadable: dict[str, str]

    @property
    def total(self) -> float: ...

    @property
    def has_non_integer(self) -> bool: ...

    @property
    def has_negative(self) -> bool: ...


def _adjusted(row: object) -> bool:
    """措置が入っているか。``is_adjusted`` を持たない行(テストの代役など)は素とみなす。"""
    return bool(getattr(row, "is_adjusted", False))


def validate_stats_import(
    rows: Sequence[StatsRowLike],
    exam_items: Mapping[int, str],
    *,
    pattern_columns_found: Sequence[str] = (),
    missing_fixed_columns: Sequence[str] = (),
    n_examinees: int | None = None,
    n_non_mcq: int = 0,
) -> list[ValidationIssue]:
    """設計書 §9.2 の 9 項目を順に見る。

    ``exam_items`` は ``position -> correct_asked``。finalize 済みの試験には既に
    「どの版を何番として出したか」が記録されているので、CSV は統計を与えるだけでよい
    (設計書 §1.2)。

    ``rows`` には**選択式の行だけ**を渡す。記述式などはバンクの対象外なので、
    件数を ``n_non_mcq`` で受け取って報告するに留める。
    """
    issues: list[ValidationIssue] = []

    if missing_fixed_columns:
        issues.append(
            _issue(
                "csv_missing_columns", f"必須列が欠けています: {', '.join(missing_fixed_columns)}"
            )
        )

    if n_non_mcq:
        issues.append(
            _issue(
                "non_mcq_skipped",
                f"選択式でない設問 {n_non_mcq} 問(記述式など)は統計の対象外として飛ばします",
                False,
                count=n_non_mcq,
            )
        )

    # --- 8. パターン列名が 31 通りと過不足なく一致 -------------------------
    issues.extend(_check_pattern_columns(pattern_columns_found))

    # --- 9. CSV の行数 = exam_items の設問数 -------------------------------
    if len(rows) != len(exam_items):
        issues.append(
            _issue(
                "row_count",
                f"CSV の行数 {len(rows)} が出題数 {len(exam_items)} と一致しません",
                n_rows=len(rows),
                n_items=len(exam_items),
            )
        )

    if not rows:
        return issues

    # --- 7. 度数が非負整数 --------------------------------------------------
    for row in rows:
        if row.unreadable:
            issues.append(
                _issue(
                    "count_unreadable",
                    f"問{row.position}: 数値として読めない列があります: "
                    f"{', '.join(sorted(row.unreadable))}",
                    position=row.position,
                )
            )
        if row.has_negative:
            issues.append(
                _issue(
                    "count_negative", f"問{row.position}: 負の度数があります", position=row.position
                )
            )
        if row.has_non_integer:
            # 実装計画 §11:「人数か割合か」の取り違えは静かに全統計を壊す。
            issues.append(
                _issue(
                    "count_not_integer",
                    f"問{row.position}: 度数が整数ではありません。"
                    "人数ではなく割合を渡していませんか",
                    position=row.position,
                )
            )

    # --- 1. 各行の 32 列の度数合計が全設問で同一 ----------------------------
    totals = {row.position: row.total for row in rows}
    distinct = sorted(set(totals.values()))
    if len(distinct) > 1:
        # 多数派の合計を正しい N とみなし、そこから外れた出題番号を名指しする。
        modal = Counter(totals.values()).most_common(1)[0][0]
        odd = [p for p, t in sorted(totals.items()) if t != modal]
        issues.append(
            _issue(
                "total_mismatch",
                f"度数合計が設問ごとに異なります(値: {distinct})。ずれている出題番号: {odd}",
                totals=distinct,
            )
        )

    n = distinct[0] if len(distinct) == 1 else None

    # --- 2. 度数合計 = メタ行の受験者数 ------------------------------------
    if n_examinees is None:
        # 実物の集計 CSV にはメタ行が無い。突き合わせ相手がいないことを黙って
        # 済ませず、受験者数は度数合計から導いたのだと明示する。
        issues.append(
            _issue(
                "n_not_declared",
                (
                    f"受験者数の記載がありません。度数合計から {int(n)} 人として扱います"
                    if n is not None
                    else "受験者数の記載がなく、度数合計も設問ごとに揃っていません"
                ),
                False,
                total=int(n) if n is not None else None,
            )
        )
    elif n is not None and int(n) != int(n_examinees):
        issues.append(
            _issue(
                "n_mismatch",
                f"度数合計 {int(n)} がメタ行の受験者数 {n_examinees} と一致しません",
                total=int(n),
                n_examinees=n_examinees,
            )
        )

    for row in rows:
        expected_correct = exam_items.get(row.position)

        # 措置(全員正解にした等)が入っていると素の成績ではなくなる。
        # 何が起きたかは CSV から分からないので、止めずに気づかせる。
        if _adjusted(row):
            issues.append(
                _issue(
                    "adjusted_item",
                    f"問{row.position}: 措置 '{row.adjustment}' が入っています。"
                    "統計の解釈に注意してください",
                    False,
                    position=row.position,
                )
            )

        # --- 5. CSV の正答肢 = exam_items.correct_asked --------------------
        if expected_correct is None:
            issues.append(
                _issue(
                    "position_unknown",
                    f"問{row.position}: この出題番号は試験に存在しません",
                    position=row.position,
                )
            )
        elif normalize_correct(row.correct) != normalize_correct(expected_correct):
            issues.append(
                _issue(
                    "correct_mismatch",
                    f"問{row.position}: CSV の正答肢 {row.correct!r} が出題記録 "
                    f"{expected_correct!r} と食い違います(正答の誤り、または出題順のずれ)",
                    position=row.position,
                )
            )

        row_total = row.total
        counted = row.counts_raw.get(normalize_correct(row.correct), 0)

        # --- 3. 正答肢に対応するパターン列の値 = 正答数 --------------------
        if row.n_correct_reported is not None and counted != row.n_correct_reported:
            issues.append(
                _issue(
                    "n_correct_mismatch",
                    f"問{row.position}: 正答パターン列の度数 {counted:g} が "
                    f"正答数 {row.n_correct_reported} と一致しません",
                    position=row.position,
                )
            )

        # --- 4. 正答数 / N = 正答率(丸め誤差内) ---------------------------
        if row.p_reported is not None and row_total:
            recomputed = counted / row_total
            if abs(recomputed - row.p_reported) > P_TOLERANCE:
                issues.append(
                    _issue(
                        "p_mismatch",
                        f"問{row.position}: 正答率 {row.p_reported} が "
                        f"正答数/N = {recomputed:.4f} と一致しません",
                        position=row.position,
                    )
                )

        # --- 6. 識別係数が 1/floor(N×0.25) の整数倍(警告) -----------------
        if row.disc is not None and row_total:
            if not is_disc_on_grid(row.disc, int(row_total)):
                issues.append(
                    _issue(
                        "disc_off_grid",
                        f"問{row.position}: 識別係数 {row.disc} が分解能 "
                        f"1/floor(N×0.25) の整数倍になっていません",
                        False,
                        position=row.position,
                    )
                )
    return issues


def _display(key: str) -> str:
    """正規化した度数列キーを、人に見せる名前に戻す。"""
    if key == BLANK:
        return BLANK_COLUMN
    if key == OTHER:
        return OTHER_COLUMN
    return key


def _check_pattern_columns(found: Sequence[str]) -> list[ValidationIssue]:
    """度数列が 31 パターン + 無回答 と過不足なく一致するか(設計書 §9.2-8)。

    ``found`` は ``io.csv_stats`` が正規化したキー。無回答は ``空白`` / ``無解答``
    のどちらで書かれていても ``BLANK`` に寄せられている。``その他`` は方言による
    追加列なので、あってもなくてもよい。
    """
    if not found:
        return []
    required = {*PATTERNS, BLANK}
    optional = {OTHER}
    got = set(found)
    missing = sorted(required - got, key=lambda c: (c != BLANK, c))
    extra = sorted(got - required - optional)

    issues: list[ValidationIssue] = []
    if missing:
        issues.append(
            _issue(
                "pattern_columns_missing",
                f"度数列が足りません: {', '.join(_display(c) for c in missing)}",
            )
        )
    if extra:
        issues.append(
            _issue(
                "pattern_columns_extra",
                f"知らない度数列があります: {', '.join(_display(c) for c in extra)}",
            )
        )
    if len(found) != len(got):
        # 「空白」と「無解答」が両方あるなど、同じ区分に寄る列が 2 つある。
        # どちらを採るかで受験者数が変わるので取り込ませない。
        duplicated = sorted({_display(c) for c in found if list(found).count(c) > 1})
        issues.append(
            _issue(
                "pattern_columns_duplicate",
                f"同じ区分に対応する度数列が重複しています: {', '.join(duplicated)}",
            )
        )
    if len(found) != len(got):
        issues.append(_issue("pattern_columns_duplicate", "度数列名が重複しています"))
    return issues


# ---------------------------------------------------------------------------
# 2. docx ⇔ 集計CSV の相互検証(実装計画 §4 M3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedQuestionView:
    """docx から取り出した 1 設問の、検証に必要な部分だけ。"""

    number: int
    stem_html: str
    choice_htmls: tuple[str, ...]


def cross_validate_import(
    questions: Sequence[ParsedQuestionView],
    rows: Sequence[StatsRowLike],
) -> list[ValidationIssue]:
    """過去問一括取込(局面A)で docx と集計 CSV を突き合わせる。

    - 設問数の一致
    - docx 由来のタイプ ⇔ CSV 由来の正答個数
    - 強調規則チェック
    """
    issues: list[ValidationIssue] = []

    if len(questions) != len(rows):
        issues.append(
            _issue(
                "question_count_mismatch",
                f"docx の設問数 {len(questions)} と CSV の行数 {len(rows)} が一致しません",
            )
        )

    by_position = {row.position: row for row in rows}
    for q in questions:
        row = by_position.get(q.number)
        if row is None:
            issues.append(
                _issue(
                    "no_stats_row",
                    f"問{q.number}: 集計 CSV に対応する行がありません",
                    number=q.number,
                )
            )
            continue

        derivation = derive_item_type_detail(q.stem_html)
        if not derivation.ok:
            issues.append(
                _issue("type_underivable", f"問{q.number}: {derivation.reason}", number=q.number)
            )
        else:
            need = REQUIRED_COUNT[derivation.item_type]
            actual = len(normalize_correct(row.correct))
            if need is not None and actual != need:
                issues.append(
                    _issue(
                        "type_correct_mismatch",
                        f"問{q.number}: 設問文は {derivation.item_type}"
                        f"(正答 {need} 個)ですが、CSV の正答肢は {actual} 個です",
                        number=q.number,
                    )
                )
            elif need is None and not 1 <= actual <= 5:
                issues.append(
                    _issue(
                        "type_correct_mismatch",
                        f"問{q.number}: XX の正答は 1〜5 個ですが {actual} 個です",
                        number=q.number,
                    )
                )

        for issue in check_emphasis_rule(q.stem_html, list(q.choice_htmls)):
            issue.message = f"問{q.number}: {issue.message}"
            issue.context["number"] = q.number
            issues.append(issue)

    for position in sorted(set(by_position) - {q.number for q in questions}):
        issues.append(
            _issue(
                "no_question",
                f"問{position}: 集計 CSV にあるが docx に設問がありません",
                number=position,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# 3. finalize 前チェック(設計書 §13.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExamItemView:
    """finalize チェックに必要な 1 出題ぶんの情報。"""

    position: int
    question_id: int
    qversion_id: int
    status: str
    stem_html: str
    correct: str
    choice_set_id: int

    @property
    def item_type(self) -> str | None:
        return derive_item_type_detail(self.stem_html).item_type

    @property
    def negative(self) -> bool:
        return is_negative(self.stem_html)


@dataclass(frozen=True)
class ExamLimits:
    """finalize と選定で共有する上限(設計書 §13.1, §13.3)。"""

    #: 同一セットおよび近似セット(共通 4 項目以上)からの出題上限。
    max_per_set_group: int = 2
    #: 否定形設問の上限(問題数)。
    max_negative: int | None = None
    #: 否定形設問の上限(比率)。
    max_negative_ratio: float | None = None
    #: タイプ別配分。``{'A': 30, 'X2': 15, ...}``
    type_distribution: dict[str, int] | None = None


DEFAULT_LIMITS = ExamLimits()


@dataclass
class FinalizeReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(i.blocking for i in self.issues)

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if not i.blocking]

    @property
    def blockers(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.blocking]


def finalize_checks(
    items: Sequence[ExamItemView],
    *,
    expected_positions: int | None = None,
    derivation_families: Mapping[int, set[int]] | None = None,
    set_links: Mapping[int, set[int]] | None = None,
    limits: ExamLimits = DEFAULT_LIMITS,
) -> FinalizeReport:
    """設計書 §13.3 の必須チェック。確定後はセット・使用版・正答が変更ロックされる。"""
    report = FinalizeReport()
    add = report.issues.append

    if not items:
        add(_issue("empty_exam", "出題が 1 問もありません"))
        return report

    # 全 position に問題が割り当てられているか
    positions = sorted(i.position for i in items)
    total = expected_positions if expected_positions is not None else len(items)
    missing = [p for p in range(1, total + 1) if p not in set(positions)]
    if missing:
        add(
            _issue(
                "position_gap", f"問題が割り当てられていない出題番号: {missing}", positions=missing
            )
        )
    duplicated = sorted({p for p in positions if positions.count(p) > 1})
    if duplicated:
        add(_issue("position_duplicate", f"出題番号が重複しています: {duplicated}"))

    for item in items:
        # draft の問題が含まれていないか(設計書 §2.5, §13.3)
        if item.status == "draft":
            add(
                _issue(
                    "draft_included",
                    f"問{item.position}: 下書き(draft)の問題は出題できません",
                    position=item.position,
                )
            )
        if item.status == "retired":
            add(
                _issue(
                    "retired_included",
                    f"問{item.position}: 退役した問題が含まれています",
                    False,
                    position=item.position,
                )
            )

        # 全設問に正答があるか / 指示文言がパース可能で正答個数と整合するか
        correct = normalize_correct(item.correct)
        if not correct:
            add(
                _issue("no_correct", f"問{item.position}: 正答がありません", position=item.position)
            )
            continue

        derivation = derive_item_type_detail(item.stem_html)
        if not derivation.ok:
            add(
                _issue(
                    "type_underivable",
                    f"問{item.position}: {derivation.reason}",
                    position=item.position,
                )
            )
            continue
        need = REQUIRED_COUNT[derivation.item_type]
        if need is not None and len(correct) != need:
            add(
                _issue(
                    "correct_count",
                    f"問{item.position}: {derivation.item_type} の正答は {need} 個ですが "
                    f"{len(correct)} 個です",
                    position=item.position,
                )
            )
        elif derivation.item_type == TYPE_XX and not 1 <= len(correct) <= 5:
            add(
                _issue(
                    "correct_count",
                    f"問{item.position}: XX の正答は 1〜5 個です",
                    position=item.position,
                )
            )

    # 同一問題の重複出題がないか
    seen: dict[int, int] = {}
    for item in items:
        if item.question_id in seen:
            add(
                _issue(
                    "duplicate_question",
                    f"問{seen[item.question_id]} と問{item.position} が同じ問題です",
                    position=item.position,
                )
            )
        else:
            seen[item.question_id] = item.position

    # 派生関係にある問題を同時出題していないか(設計書 §2.3, §13.3)
    if derivation_families:
        for a in items:
            family = derivation_families.get(a.question_id, {a.question_id})
            for b in items:
                if b.position <= a.position or b.question_id == a.question_id:
                    continue
                if b.question_id in family:
                    add(
                        _issue(
                            "derived_together",
                            f"問{a.position} と問{b.position} は派生関係にあります。"
                            "実質同じ問題になるため同時に出題できません",
                            position=b.position,
                        )
                    )

    # 同一・近似セットからの出題が上限内か(警告)
    if limits.max_per_set_group:
        report.issues.extend(_check_set_groups(items, set_links or {}, limits.max_per_set_group))

    # 否定形設問が上限内か(警告)
    negatives = [i for i in items if i.negative]
    if limits.max_negative is not None and len(negatives) > limits.max_negative:
        add(
            _issue(
                "negative_over_limit",
                f"否定形設問が {len(negatives)} 問で上限 {limits.max_negative} 問を超えています",
                False,
            )
        )
    if limits.max_negative_ratio is not None:
        ratio = len(negatives) / len(items)
        if ratio > limits.max_negative_ratio:
            add(
                _issue(
                    "negative_over_ratio",
                    f"否定形設問が {ratio:.0%} で上限 {limits.max_negative_ratio:.0%} を超えています",
                    False,
                )
            )

    # タイプ別配分が指定どおりか(警告)
    if limits.type_distribution:
        actual: dict[str, int] = {}
        for item in items:
            key = item.item_type or "?"
            actual[key] = actual.get(key, 0) + 1
        for item_type, want in limits.type_distribution.items():
            got = actual.get(item_type, 0)
            if got != want:
                add(
                    _issue(
                        "type_distribution",
                        f"{item_type} は {want} 問の指定ですが {got} 問です",
                        False,
                        item_type=item_type,
                    )
                )
    return report


def _check_set_groups(
    items: Sequence[ExamItemView], set_links: Mapping[int, set[int]], limit: int
) -> list[ValidationIssue]:
    """同一セットおよび近似セットからの出題が上限内かを見る(設計書 §6.4-1, §13.1)。

    前回と 1 肢だけ違うセットを続けて出す事故を防ぐための警告。
    """
    issues: list[ValidationIssue] = []
    checked: set[int] = set()
    for item in items:
        base = item.choice_set_id
        if base in checked:
            continue
        checked.add(base)
        group = {base, *set_links.get(base, set())}
        members = [i for i in items if i.choice_set_id in group]
        if len(members) > limit:
            issues.append(
                _issue(
                    "set_group_over_limit",
                    f"選択肢セット {sorted(group)} から {len(members)} 問出題しています"
                    f"(上限 {limit} 問): 問" + "・問".join(str(m.position) for m in members),
                    False,
                    choice_set_ids=sorted(group),
                )
            )
    return issues
