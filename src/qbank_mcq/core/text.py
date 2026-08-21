"""正規化・均等割・タグ処理。すべて純関数(実装計画 §5)。

設計書 §3.1 より、設問文・選択肢は **HTML 断片のみ**を保存する。プレーン版の列は
持たない。許可タグは ``<strong>`` ``<i>`` ``<sup>`` ``<sub>`` の 4 種のみ、属性なし。

正規化で最も壊れやすいのが空白の扱いである(設計書 §7、実装計画 §11)。

- 一律に空白を削ると ``Krause 小体`` ``滑膜 A 型細胞`` ``胎生 3-4 週`` が壊れる
- そこで均等割の除去は「日本語 1 文字 + 空白 1 つ + 日本語 1 文字」で
  **文字列全体が構成される場合に限る**

もう一点、NFKC を **HTML 文字列そのものに掛けてはならない**。全角 ``＜`` (U+FF1C) が
半角 ``<`` に変換され、``＜以上 50 設問＞`` のような本文がタグとして解釈されてしまう。
本モジュールは HTML を解析し、**テキストノードにのみ** NFKC を適用したうえで再エスケープする。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: 許可タグ。並びはそのままネストの正規順序でもある(``runs_to_html`` が使う)。
ALLOWED_TAGS: tuple[str, ...] = ("strong", "i", "sup", "sub")

#: 日本語 1 文字(ひらがな・カタカナ・漢字・々・互換漢字)。設計書 §7。
J = r"[぀-ヿ一-鿿々豈-﫿]"

#: 均等割の検出。半角・全角の両方の空白を許容するため NFKC の前後どちらでも動く。
KINTOU = re.compile(rf"^({J})[　\s]({J})$")

#: 均等割の復元。空白を含まない日本語 2 文字ちょうどの選択肢が対象。
KINTOU_RENDER = re.compile(rf"^({J})({J})$")

#: 均等割で挿入する全角空白。
IDEOGRAPHIC_SPACE = "　"


def escape_text(s: str) -> str:
    """テキストを HTML 断片に埋め込める形にする(設計書 §3.1)。"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class SanitizeResult:
    """``sanitize_html`` の結果。

    ``removals`` は除去箇所の説明。設計書 §3.1 は「除去箇所をログに残す」ことを求める。
    """

    html: str
    removals: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no cover - 利便性のみ
        return bool(self.html)


class _Sanitizer(HTMLParser):
    """許可タグ 4 種のホワイトリスト。属性・その他のタグは除去し、中身は残す。"""

    def __init__(self, *, nfkc: bool) -> None:
        # convert_charrefs=True なので実体参照は handle_data で素の文字として届く。
        # 出力時に escape_text で必ず張り直すため、&amp; の二重エスケープは起きない。
        super().__init__(convert_charrefs=True)
        self._nfkc = nfkc
        self._out: list[str] = []
        self._open: list[str] = []
        self.removals: list[str] = []

    # -- タグ ---------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t not in ALLOWED_TAGS:
            self.removals.append(f"許可外タグを除去: <{t}>")
            return
        if attrs:
            names = " ".join(a for a, _ in attrs)
            self.removals.append(f"属性を除去: <{t} {names}>")
        self._out.append(f"<{t}>")
        self._open.append(t)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t not in ALLOWED_TAGS:
            return
        if t not in self._open:
            self.removals.append(f"開始タグのない </{t}> を除去")
            return
        # 交差したネスト (<i><sup>x</i></sup>) は入れ子を正して閉じる。
        while self._open:
            top = self._open.pop()
            self._out.append(f"</{top}>")
            if top == t:
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.removals.append(f"空要素タグを除去: <{tag.lower()}/>")

    # -- テキスト以外 -------------------------------------------------------
    def handle_comment(self, data: str) -> None:
        self.removals.append("コメントを除去")

    def handle_decl(self, decl: str) -> None:
        self.removals.append("宣言を除去")

    def handle_pi(self, data: str) -> None:
        self.removals.append("処理命令を除去")

    def unknown_decl(self, data: str) -> None:  # pragma: no cover - 実運用では出ない
        self.removals.append("不明な宣言を除去")

    # -- テキスト -----------------------------------------------------------
    def handle_data(self, data: str) -> None:
        if self._nfkc:
            data = unicodedata.normalize("NFKC", data)
        self._out.append(escape_text(data))

    # -- 終端 ---------------------------------------------------------------
    def result(self) -> SanitizeResult:
        self.close()
        while self._open:
            t = self._open.pop()
            self.removals.append(f"閉じられていない <{t}> を補完")
            self._out.append(f"</{t}>")
        return SanitizeResult(_collapse_tags("".join(self._out)), self.removals)


def _collapse_tags(s: str) -> str:
    """空タグ ``<i></i>`` と、隙間なく隣接する同一タグ ``</i><i>`` を畳む。

    空白をまたぐ結合はしない。``<i>Strep</i> <i>tococcus</i>`` の空白は
    元の docx では非イタリックであり、往復で書式を変えないため。
    """
    prev = None
    while prev != s:
        prev = s
        for t in ALLOWED_TAGS:
            s = s.replace(f"<{t}></{t}>", "").replace(f"</{t}><{t}>", "")
    return s


def sanitize_html(html: str, *, nfkc: bool = False) -> SanitizeResult:
    """許可タグ 4 種だけを残した整形式の HTML 断片を返す。

    ``nfkc=True`` のとき、テキストノードにのみ NFKC 正規化を適用する
    (タグ構造は壊さない)。
    """
    p = _Sanitizer(nfkc=nfkc)
    p.feed(html)
    return p.result()


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_tags(html: str) -> str:
    """タグを除いた素のテキスト。実体参照は復号する。

    設計書 §3.2 の要点: 否定形設問は ``酸に溶け<strong>ない</strong>のはどれか。``
    のようにタグが語中に入る。**検索・指示文言のパースは必ずこれを通してから行う。**
    列としては保存せず、その都度計算する。
    """
    p = _Stripper()
    p.feed(html)
    p.close()
    return "".join(p.parts)


def has_tag(html: str, tag: str) -> bool:
    """許可タグ ``tag`` が HTML 断片に含まれるか。"""
    t = tag.lower()
    if t not in ALLOWED_TAGS:
        raise ValueError(f"許可されていないタグです: {tag}")
    return re.search(rf"<{t}\s*>", html, re.IGNORECASE) is not None


# ---------------------------------------------------------------------------
# 均等割(設計書 §7)
# ---------------------------------------------------------------------------


def is_kintou(html: str) -> bool:
    """均等割表記(日本語 1 文字 + 空白 + 日本語 1 文字)そのものか。"""
    return KINTOU.match(html) is not None


def normalize_choice(html: str) -> str:
    """選択肢の保存形。NFKC + サニタイズ + 均等割の全角空白除去。

    ``横 紋`` → ``横紋`` のように詰める一方、``Krause 小体`` ``滑膜 A 型細胞``
    ``胎生 3-4 週`` は文字列全体が「日本語+空白+日本語」ではないため**変更しない**。
    """
    s = sanitize_html(html, nfkc=True).html.strip()
    m = KINTOU.match(s)
    if m:
        return m.group(1) + m.group(2)
    return s


def normalize_stem(html: str) -> str:
    """設問文の保存形。均等割は選択肢の規則なので適用しない(設計書 §7)。"""
    return sanitize_html(html, nfkc=True).html.strip()


def render_choice(text_html: str, render_override: str | None = None) -> str:
    """docx 出力用の印字形。日本語 2 文字なら均等割の全角空白を復元する。

    ``render_override`` が入っていればそれをそのまま使う(設計書 §7 の例外用)。
    """
    if render_override is not None:
        return render_override
    m = KINTOU_RENDER.match(text_html)
    if m:
        return m.group(1) + IDEOGRAPHIC_SPACE + m.group(2)
    return text_html


# ---------------------------------------------------------------------------
# 書式付き run と HTML の相互変換(設計書 §5.1-2, §5.3)
# ---------------------------------------------------------------------------

#: ``(テキスト, 書式集合)`` の並び。書式集合の要素は ALLOWED_TAGS のいずれか。
Run = tuple[str, frozenset[str]]


def merge_runs(runs: Iterable[Run]) -> list[Run]:
    """書式が同一の隣接 run を結合する。

    設計書 §5.1-2: 1 つの語が複数 run に分割されていることが多いため、タグ化の前に
    必ずこれを通す。怠ると ``<i>Strep</i><i>tococcus</i>`` のような分割が生じる。
    """
    merged: list[Run] = []
    for text, fmt in runs:
        if not text:
            continue
        if merged and merged[-1][1] == fmt:
            merged[-1] = (merged[-1][0] + text, fmt)
        else:
            merged.append((text, fmt))
    return merged


def _canonical(fmt: Iterable[str]) -> list[str]:
    """書式集合を常に同じネスト順に並べる(出力を決定的にするため)。"""
    s = set(fmt)
    unknown = s - set(ALLOWED_TAGS)
    if unknown:
        raise ValueError(f"許可されていない書式です: {sorted(unknown)}")
    return [t for t in ALLOWED_TAGS if t in s]


def runs_to_html(runs: Sequence[Run]) -> str:
    """書式付き run 列を HTML 断片にする。結合は呼び出し側で済ませておく。"""
    out: list[str] = []
    open_tags: list[str] = []
    for text, fmt in merge_runs(runs):
        want = _canonical(fmt)
        # 共通の前置きを残し、それより上を閉じる。
        common = 0
        while common < len(open_tags) and common < len(want) and open_tags[common] == want[common]:
            common += 1
        while len(open_tags) > common:
            out.append(f"</{open_tags.pop()}>")
        for t in want[common:]:
            out.append(f"<{t}>")
            open_tags.append(t)
        out.append(escape_text(text))
    while open_tags:
        out.append(f"</{open_tags.pop()}>")
    return _collapse_tags("".join(out))


def html_to_runs(html: str) -> list[Run]:
    """HTML 断片を書式付き run 列に戻す(docx 出力で使う)。

    ``runs_to_html`` の逆。往復テスト(設計書 §5.3)の片側を担う。
    """
    runs: list[Run] = []
    stack: list[str] = []

    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)

        def handle_starttag(self, tag: str, attrs: object) -> None:
            t = tag.lower()
            if t in ALLOWED_TAGS:
                stack.append(t)

        def handle_endtag(self, tag: str) -> None:
            t = tag.lower()
            if t in stack:
                while stack:
                    if stack.pop() == t:
                        break

        def handle_data(self, data: str) -> None:
            if data:
                runs.append((data, frozenset(stack)))

    p = _P()
    p.feed(html)
    p.close()
    return merge_runs(runs)
