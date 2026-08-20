"""選択肢セットの署名・類似・リンク生成、並び順の解決。すべて純関数。

設計書 §6.1 のモデル: **選択肢セットは順序を持たない 5 項目の集合**であり、
出題時の並び順は版 (``question_version.choice_order``) が持つ。順序シャッフルは
「同一セットの別の並び」として自然に表現され、別セットとして重複登録されない。

``choice_order`` の書式は設計書 §8 のスキーマ注記に従う::

    '31524' = a←項目3, b←項目1, c←項目5, d←項目2, e←項目4

(設計書 §6.1 の図には ``'cadbe'`` という別表記も現れるが、DB スキーマの注記である
§8 を正とする。)
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .text import strip_tags
from .typing_rules import LABELS

#: セットは常に 5 項目(設計書 §6.1、および §8 の ``item_no INTEGER -- 1〜5``)。
EXPECTED_ITEM_COUNT = 5

#: 署名を作るときの連結区切り。本文には現れない制御文字を使う。
_SEP = "\x1f"

RELATION_IDENTICAL = "identical"
RELATION_NEAR = "near"
RELATION_CANDIDATE = "candidate"

#: 設計書 §6.3 の対応表。共通項目数 → (関係, 自動リンクするか)
_RELATION_TABLE: dict[int, tuple[str, bool]] = {
    5: (RELATION_IDENTICAL, False),  # 統合を提案する。リンクではなく統合の対象
    4: (RELATION_NEAR, True),  # 1 肢差し替え
    3: (RELATION_NEAR, True),  # 2 肢差し替え
    2: (RELATION_CANDIDATE, False),  # 候補として提示するのみ
}


def choice_set_signature(items: Iterable[str]) -> str:
    """HTML 断片をソートして連結したハッシュ(設計書 §6.2)。

    順序を持たないモデルなので、並び順が違うだけのセットは同じ署名になる。
    """
    return hashlib.sha256(_SEP.join(sorted(items)).encode("utf-8")).hexdigest()


def validate_items(items: Sequence[str]) -> list[str]:
    """セット登録前の項目チェック。問題があればメッセージを返す。"""
    problems: list[str] = []
    if len(items) != EXPECTED_ITEM_COUNT:
        problems.append(f"選択肢セットは {EXPECTED_ITEM_COUNT} 項目です(現在 {len(items)} 項目)")
    if any(not i.strip() for i in items):
        problems.append("空の項目があります")
    dupes = {i for i in items if items.count(i) > 1}
    if dupes:
        problems.append(f"同一の項目が重複しています: {sorted(dupes)}")
    return problems


def set_similarity(a: Iterable[str], b: Iterable[str]) -> int:
    """2 つのセットの共通項目数(設計書 §6.3)。"""
    return len(set(a) & set(b))


def relation_for(shared: int) -> str | None:
    """共通項目数から関係名を返す。0〜1 は別セットなので ``None``。"""
    entry = _RELATION_TABLE.get(shared)
    return entry[0] if entry else None


def should_autolink(shared: int) -> bool:
    """自動リンクの対象か。設計書 §6.3 は共通 3〜4 項目を自動リンクとする。"""
    entry = _RELATION_TABLE.get(shared)
    return bool(entry and entry[1])


@dataclass(frozen=True)
class SetLink:
    """``choice_set_links`` の 1 行に対応する。``set_a < set_b`` に正規化される。"""

    set_a: int
    set_b: int
    shared: int
    relation: str


def build_links(sets: Mapping[int, Sequence[str]], *, min_shared: int = 3) -> list[SetLink]:
    """自動リンクを生成する(設計書 §6.3)。

    **推移的に閉じない。** A〜B、B〜C があっても A〜C は生成しない。総当たりで
    共通項目数を数え、閾値以上の組だけを返す。自動リンクはあくまで提案であり、
    手動で解除・追加できる。
    """
    links: list[SetLink] = []
    ids = sorted(sets)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            shared = set_similarity(sets[a], sets[b])
            if shared < min_shared:
                continue
            rel = relation_for(shared)
            if rel is None:  # pragma: no cover - min_shared>=2 なら到達しない
                continue
            links.append(SetLink(a, b, shared, rel))
    return links


def audit_tagless_duplicates(items_by_set: Mapping[int, Sequence[str]]) -> dict[str, list[int]]:
    """タグを除去すると一致する項目を洗い出す監査(設計書 §6.2)。

    マークアップの付け忘れで同一性判定が破綻するのを防ぐための保険。列は持たず、
    設定画面から実行する。**タグ除去後のテキストが同じで HTML が異なる**項目のみを返す。
    """
    buckets: dict[str, set[str]] = defaultdict(set)
    where: dict[str, set[int]] = defaultdict(set)
    for set_id, items in items_by_set.items():
        for html in items:
            plain = strip_tags(html)
            buckets[plain].add(html)
            where[plain].add(set_id)
    return {plain: sorted(where[plain]) for plain, variants in buckets.items() if len(variants) > 1}


# ---------------------------------------------------------------------------
# 並び順 (choice_order)
# ---------------------------------------------------------------------------


def parse_choice_order(choice_order: str) -> tuple[int, ...]:
    """``'31524'`` → ``(3, 1, 5, 2, 4)``。1〜5 の順列でなければ ``ValueError``。"""
    if len(choice_order) != EXPECTED_ITEM_COUNT or not choice_order.isdigit():
        raise ValueError(f"choice_order は 1〜5 の順列 5 桁です: {choice_order!r}")
    seq = tuple(int(c) for c in choice_order)
    if sorted(seq) != list(range(1, EXPECTED_ITEM_COUNT + 1)):
        raise ValueError(f"choice_order は 1〜5 の順列です: {choice_order!r}")
    return seq


def format_choice_order(seq: Sequence[int]) -> str:
    """``(3, 1, 5, 2, 4)`` → ``'31524'``。"""
    s = "".join(str(i) for i in seq)
    parse_choice_order(s)  # 妥当性を同じ規則で確かめる
    return s


def identity_order() -> str:
    """並び替えなし ``'12345'``。"""
    return "12345"


def label_to_item_no(label: str, choice_order: str) -> int:
    """印字記号 → 項目 ID。``label_to_item_no('a', '31524') == 3``。"""
    idx = LABELS.index(label.lower())
    return parse_choice_order(choice_order)[idx]


def item_no_to_label(item_no: int, choice_order: str) -> str:
    """項目 ID → 印字記号。``item_no_to_label(3, '31524') == 'a'``。"""
    return LABELS[parse_choice_order(choice_order).index(item_no)]


def ordered_items(items_by_no: Mapping[int, str], choice_order: str) -> list[tuple[str, int, str]]:
    """印字順に ``(印字記号, 項目ID, HTML)`` を返す。冊子出力と画面表示で使う。"""
    return [
        (LABELS[i], no, items_by_no[no]) for i, no in enumerate(parse_choice_order(choice_order))
    ]


def resolve_choice_order(items_by_no: Mapping[int, str], printed: Sequence[str]) -> str:
    """既存セットの項目 ID に対して、印字順 ``printed`` を表す ``choice_order`` を求める。

    docx 取込で「同じセットだが並びが違う」出題に出会ったときに使う。設計書 §6.1 が
    順序シャッフルを「同一セットの別の並び」として扱うための要となる変換。
    項目が 1 対 1 に対応しなければ ``ValueError``。
    """
    remaining: dict[str, list[int]] = {}
    for no, html in items_by_no.items():
        remaining.setdefault(html, []).append(no)
    for nos in remaining.values():
        nos.sort()

    seq: list[int] = []
    for html in printed:
        nos = remaining.get(html)
        if not nos:
            raise ValueError(f"セットに存在しない選択肢です: {html!r}")
        seq.append(nos.pop(0))
    return format_choice_order(seq)


def correct_to_item_nos(correct: str, choice_order: str) -> tuple[int, ...]:
    """正答(印字記号)を項目 ID に写す。

    並び順が変わっても項目単位で追跡できるようにするための変換(設計書 §6.4-5、§6.5)。
    """
    return tuple(sorted(label_to_item_no(c, choice_order) for c in correct))


def item_nos_to_correct(item_nos: Iterable[int], choice_order: str) -> str:
    """項目 ID の集合を印字記号の正答文字列にする。"""
    labels = {item_no_to_label(n, choice_order) for n in item_nos}
    return "".join(c for c in LABELS if c in labels)
