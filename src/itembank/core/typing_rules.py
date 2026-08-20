"""問題タイプの導出と正答個数の検証、強調規則のチェック。すべて純関数。

設計書 §11:

===== ========== ==========
タイプ 指示文言   正答の個数
===== ========== ==========
A      1つ選べ    1
X2     2つ選べ    2
X3     3つ選べ    3
X4     4つ選べ    4
XX     すべて選べ 1〜5(任意)
===== ========== ==========

「5つ選べ」は成立しない。XX が任意個数を取るため**正答肢からはタイプが確定せず**、
設問文の指示文言から導出する。``item_type`` 列は持たない(設計書 §8)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .text import has_tag, strip_tags

#: 印字記号。選択肢は常に 5 つ。
LABELS: str = "abcde"

TYPE_A = "A"
TYPE_X2 = "X2"
TYPE_X3 = "X3"
TYPE_X4 = "X4"
TYPE_XX = "XX"

ITEM_TYPES: tuple[str, ...] = (TYPE_A, TYPE_X2, TYPE_X3, TYPE_X4, TYPE_XX)

#: 新規入力時のドロップダウンはこの 5 種に限定する(設計書 §11, §14-2)。
INSTRUCTION_CHOICES: dict[str, str] = {
    TYPE_A: "1つ選べ。",
    TYPE_X2: "2つ選べ。",
    TYPE_X3: "3つ選べ。",
    TYPE_X4: "4つ選べ。",
    TYPE_XX: "すべて選べ。",
}

#: A/X2/X3/X4 の要求個数。XX は None(1〜5 で任意)。
REQUIRED_COUNT: dict[str, int | None] = {
    TYPE_A: 1,
    TYPE_X2: 2,
    TYPE_X3: 3,
    TYPE_X4: 4,
    TYPE_XX: None,
}

#: 設計書 §4 が例示する否定語。設定画面から差し替えられる(設計書 §14-10)。
#: ``でない`` ``含まれない`` は ``ない`` に含まれるが、意図を残すため列挙しておく。
DEFAULT_NEGATIVE_WORDS: tuple[str, ...] = ("ない", "でない", "含まれない", "誤っている")

_KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}

# NFKC 後の文字列に当てる。全角数字は NFKC で半角になっている。
_RE_XX = re.compile(r"(?:すべて|全て)(?:を)?選べ")
_RE_NUM = re.compile(r"([1-9一二三四五])\s*つ(?:を)?選べ")


@dataclass(frozen=True)
class TypeDerivation:
    """``derive_item_type_detail`` の結果。"""

    item_type: str | None
    instruction: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.item_type is not None


def derive_item_type_detail(stem_html: str) -> TypeDerivation:
    """設問文からタイプを導出する。**必ずタグ除去後に判定する**(設計書 §3.2)。"""
    plain = unicodedata.normalize("NFKC", strip_tags(stem_html))

    m = _RE_XX.search(plain)
    if m:
        return TypeDerivation(TYPE_XX, m.group(0))

    m = _RE_NUM.search(plain)
    if m:
        token = m.group(1)
        n = _KANJI_NUM.get(token) or int(token)
        if n == 5:
            # 設計書 §11: 5 肢から 5 つ選ばせる設問は成立しない。
            return TypeDerivation(
                None, m.group(0), "「5つ選べ」は成立しません。「すべて選べ」を使ってください"
            )
        if n > 5:
            return TypeDerivation(None, m.group(0), f"選択肢は 5 つまでです: {m.group(0)}")
        return TypeDerivation(ITEM_TYPES[n - 1], m.group(0))

    return TypeDerivation(None, None, "指示文言(「1つ選べ」〜「すべて選べ」)が見つかりません")


def derive_item_type(stem_html: str) -> str | None:
    """設問文からタイプを導出する。導出できなければ ``None``。"""
    return derive_item_type_detail(stem_html).item_type


def normalize_correct(correct: str) -> str:
    """正答を印字記号の昇順・重複なしの文字列にそろえる(``'da'`` → ``'ad'``)。"""
    plain = unicodedata.normalize("NFKC", correct).strip().lower()
    seen = {c for c in plain if c in LABELS}
    return "".join(c for c in LABELS if c in seen)


@dataclass
class ValidationIssue:
    """検証で見つかった 1 件。``blocking`` が真なら保存・確定をブロックする。"""

    code: str
    message: str
    blocking: bool = True
    context: dict[str, object] = field(default_factory=dict)


def validate_correct(correct: str, item_type: str | None) -> list[ValidationIssue]:
    """正答とタイプの整合を検証する(設計書 §11)。

    A/X2/X3/X4 で個数が不一致なら**ブロック**。XX は 1〜5 で許容。
    """
    issues: list[ValidationIssue] = []
    raw = unicodedata.normalize("NFKC", correct or "").strip().lower()

    bad = sorted({c for c in raw if c not in LABELS})
    if bad:
        issues.append(
            ValidationIssue(
                "correct_bad_label",
                f"正答に使えない記号が含まれます: {''.join(bad)}(a〜e のみ)",
            )
        )
    if len(raw) != len(set(raw)):
        issues.append(ValidationIssue("correct_duplicate", f"正答に重複があります: {correct}"))

    norm = normalize_correct(correct)
    if not norm:
        issues.append(ValidationIssue("correct_empty", "正答が設定されていません"))
        return issues

    if item_type is None:
        issues.append(
            ValidationIssue("type_unknown", "タイプが導出できないため正答個数を検証できません")
        )
        return issues
    if item_type not in ITEM_TYPES:
        issues.append(ValidationIssue("type_unknown", f"未知のタイプです: {item_type}"))
        return issues

    need = REQUIRED_COUNT[item_type]
    if need is None:
        if not 1 <= len(norm) <= 5:
            issues.append(
                ValidationIssue("correct_count", f"XX の正答は 1〜5 個です(現在 {len(norm)} 個)")
            )
    elif len(norm) != need:
        issues.append(
            ValidationIssue(
                "correct_count",
                f"{item_type} の正答は {need} 個です(現在 {len(norm)} 個: {norm})",
                context={"item_type": item_type, "expected": need, "actual": len(norm)},
            )
        )
    return issues


def is_negative(stem_html: str) -> bool:
    """否定形設問か。

    設計書 §4: 強調は**否定形である場合にのみ**用いる厳格な規則なので、
    ``<strong>`` の有無がそのまま否定形の指標になる。専用列は設けない。
    """
    return has_tag(stem_html, "strong")


def contains_negative_word(
    stem_html: str, negative_words: tuple[str, ...] = DEFAULT_NEGATIVE_WORDS
) -> bool:
    """否定語を含むか。タグ除去後に判定する(設計書 §3.2)。"""
    plain = strip_tags(stem_html)
    return any(w in plain for w in negative_words)


def check_emphasis_rule(
    stem_html: str,
    choice_htmls: list[str] | tuple[str, ...] = (),
    negative_words: tuple[str, ...] = DEFAULT_NEGATIVE_WORDS,
) -> list[ValidationIssue]:
    """強調規則の違反を検出する(設計書 §4-(3))。

    否定語リストが網羅的とは限らないため、**すべて警告でありブロックしない**。
    """
    issues: list[ValidationIssue] = []
    emphasized = is_negative(stem_html)
    negative = contains_negative_word(stem_html, negative_words)

    if negative and not emphasized:
        issues.append(
            ValidationIssue(
                "emphasis_missing",
                "否定語を含みますが強調(<strong>)がありません。付け忘れの疑いがあります",
                blocking=False,
            )
        )
    if emphasized and not negative:
        issues.append(
            ValidationIssue(
                "emphasis_unexpected",
                "強調がありますが否定語を含みません。誤った強調、または否定語リストの不足です",
                blocking=False,
            )
        )
    for i, html in enumerate(choice_htmls):
        if has_tag(html, "strong"):
            issues.append(
                ValidationIssue(
                    "emphasis_in_choice",
                    f"選択肢 {i + 1} に強調があります。強調は設問文のみに用います",
                    blocking=False,
                    context={"index": i},
                )
            )
    return issues
