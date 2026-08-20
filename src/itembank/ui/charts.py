"""matplotlib の可視化(設計書 §14-3、実装計画 M6-4)。

    周辺マーク率の横棒 / **誤答パターン上位5件** / 部分正答分布 /
    正答率・識別係数の推移

実装計画 §11 の落とし穴:

    matplotlib の日本語フォントを rcParams で明示しないと豆腐になる

Windows は Yu Gothic を持つが、開発機(Linux/macOS)にはない。**入っているものから
選ぶ**ようにして、無ければ黙って既定に戻す。ここで例外を投げると、フォントが無い
だけで問題詳細画面が開かなくなる。
"""

from __future__ import annotations

import logging

import matplotlib

# Qt に描く前にバックエンドを決める。import 順を変えると別のバックエンドが
# 先に選ばれ、GUI の外(テストなど)で画面を開こうとして落ちる。
matplotlib.use("QtAgg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtWidgets import QSizePolicy, QWidget  # noqa: E402

log = logging.getLogger(__name__)

#: 設計書 §15 は Windows 標準の Yu Gothic を指定する。開発機向けの候補も添える。
JAPANESE_FONTS = (
    "Yu Gothic",
    "Meiryo",
    "MS Gothic",
    "Hiragino Sans",
    "Noto Sans CJK JP",
    "IPAGothic",
    "TakaoGothic",
)

_FONT_CONFIGURED = False


def configure_japanese_font() -> str | None:
    """入っている日本語フォントを rcParams に据える。見つからなければ ``None``。"""
    global _FONT_CONFIGURED
    if _FONT_CONFIGURED:
        return matplotlib.rcParams["font.family"][0]

    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in JAPANESE_FONTS:
        if name in available:
            matplotlib.rcParams["font.family"] = [name]
            # マイナス記号は日本語フォントに無いことがある(識別係数は負を取る)。
            matplotlib.rcParams["axes.unicode_minus"] = False
            _FONT_CONFIGURED = True
            log.info("グラフの日本語フォント: %s", name)
            return name

    log.warning("日本語フォントが見つかりません。グラフの日本語は豆腐になります")
    _FONT_CONFIGURED = True
    return None


class ChartCanvas(FigureCanvasQTAgg):
    """1 枚の図を持つキャンバス。``draw_*`` で中身を差し替える。"""

    def __init__(self, parent: QWidget | None = None, *, height: float = 2.4) -> None:
        configure_japanese_font()
        self.figure = Figure(figsize=(4.6, height), layout="constrained")
        super().__init__(self.figure)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def _axes(self, title: str):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_title(title, fontsize=9)
        ax.tick_params(labelsize=8)
        return ax

    def show_message(self, text: str) -> None:
        """データが無いときに空の枠だけ出さない。"""
        ax = self._axes("")
        ax.axis("off")
        ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=9)
        self.draw_idle()

    # -- 設計書 §14-3 の 4 つ ----------------------------------------------
    def draw_mark_rates(self, sel: dict[str, float | None], correct: str) -> None:
        """周辺マーク率の横棒。正答の肢を塗り分ける(設計書 §14-3)。"""
        labels = list("abcde")
        values = [(sel.get(label) or 0.0) * 100 for label in labels]
        if not any(v for v in values):
            self.show_message("マーク率のデータがありません")
            return

        ax = self._axes("周辺マーク率(%)")
        colors = ["#2f6f4e" if label in correct else "#8d8d8d" for label in labels]
        ax.barh(labels, values, color=colors)
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        for y, v in enumerate(values):
            ax.text(min(v + 1, 96), y, f"{v:.1f}", va="center", fontsize=8)
        self.draw_idle()

    def draw_top_wrong(self, pairs: list[tuple[str, int]], *, limit: int = 5) -> None:
        """誤答パターン上位。設計書 §14-3 が名指しで求めている図。"""
        pairs = list(pairs)[:limit]
        if not pairs:
            self.show_message("誤答パターンがありません")
            return

        ax = self._axes(f"誤答パターン上位 {len(pairs)} 件(人)")
        patterns = [p for p, _ in pairs]
        counts = [c for _, c in pairs]
        ax.barh(patterns, counts, color="#a3563a")
        ax.invert_yaxis()
        for y, c in enumerate(counts):
            ax.text(c, y, f" {c}", va="center", fontsize=8)
        self.draw_idle()

    def draw_partial(self, partial: dict[int, int], n_correct_required: int) -> None:
        """部分正答分布。「正答を何個当てたか」の人数(設計書 §14-3)。"""
        if not partial:
            self.show_message("部分正答のデータがありません")
            return

        ax = self._axes("部分正答分布(人)")
        hits = sorted(partial)
        counts = [partial[h] for h in hits]
        colors = ["#2f6f4e" if h == n_correct_required else "#8d8d8d" for h in hits]
        ax.bar([str(h) for h in hits], counts, color=colors)
        ax.set_xlabel("当てた正答の個数", fontsize=8)
        self.draw_idle()

    def draw_trend(
        self,
        years: list[str],
        ps: list[float | None],
        discs: list[float | None],
        *,
        version_breaks: list[int] | None = None,
    ) -> None:
        """正答率・識別係数の推移。**版の切れ目を縦線で示す**(設計書 §2.2)。

        改訂すると新版は実績ゼロから始まるので、切れ目なしに繋いだ折れ線は
        「同じ問題の推移」として読まれてしまう。
        """
        if not years:
            self.show_message("出題実績がありません")
            return

        ax = self._axes("正答率と識別係数の推移")
        x = list(range(len(years)))
        ax.plot(
            x,
            [(p or 0) * 100 if p is not None else None for p in ps],
            marker="o",
            label="正答率(%)",
        )
        ax.set_ylim(0, 100)
        ax.set_xticks(x)
        ax.set_xticklabels(years, fontsize=8)

        right = ax.twinx()
        right.plot(x, discs, marker="s", color="#a3563a", label="識別係数")
        right.set_ylim(-1, 1)
        right.axhline(0, color="#cccccc", linewidth=0.8)
        right.tick_params(labelsize=8)

        for index in version_breaks or []:
            if 0 < index < len(years):
                ax.axvline(index - 0.5, color="#3b6ea5", linestyle="--", linewidth=1)

        handles = ax.get_lines() + right.get_lines()[:1]
        ax.legend(handles, [h.get_label() for h in handles], fontsize=8, loc="lower left")
        self.draw_idle()
