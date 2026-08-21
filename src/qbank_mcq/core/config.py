"""ユーザー設定の読み書き(設計書 §14-10 の設定画面が編集する値)。

設定画面が変えられるのは次の 5 つ:

===================== ================================================
タグ管理              ``core.bank.ensure_tag`` が作る ``tags`` テーブル
フラグ閾値            ``core.stats.FlagThresholds``
近似リンク閾値        ``core.bank.refresh_links_for`` の ``min_shared``
基準フォント          ``io.docx_write.WriterConfig``
否定語リスト          ``core.typing_rules.check_emphasis_rule``
===================== ================================================

タグだけは DB に持つ(問題と結び付くため)。残りは DB に入れる理由がないので
``%APPDATA%\\Qbank-MCQ\\settings.json`` に置く。exe 配布後にユーザーが壊れた
JSON を残しても起動できなくならないよう、**読めない値は既定値に落として警告する**。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from . import paths
from .stats import FlagThresholds
from .typing_rules import DEFAULT_NEGATIVE_WORDS

log = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"

#: 自動リンクの共通項目数の下限。設計書 §6.3 の表で自動リンクの対象になるのは
#: 共通 3〜4 項目(2 肢差し替え・1 肢差し替え)だけなので、設定できる幅もそこに限る。
#: 範囲外の値を入れても ``choiceset.should_autolink`` に弾かれ、何も変わらない。
MIN_SHARED_RANGE = (3, 4)
DEFAULT_MIN_SHARED = 3


@dataclass(frozen=True)
class FontSettings:
    """冊子の基準フォント(設計書 §5.3, §14-10)。``WriterConfig`` に写す。"""

    mincho: str = "ＭＳ 明朝"
    gothic: str = "ＭＳ ゴシック"
    latin: str = "Times New Roman"
    columns: int = 2
    font_size_pt: float = 10.5


@dataclass(frozen=True)
class Settings:
    """設定画面が扱う値のすべて。"""

    thresholds: FlagThresholds = field(default_factory=FlagThresholds)
    fonts: FontSettings = field(default_factory=FontSettings)
    negative_words: tuple[str, ...] = DEFAULT_NEGATIVE_WORDS
    #: 近似セットを自動リンクする共通項目数の下限(設計書 §6.3)。
    min_shared: int = DEFAULT_MIN_SHARED

    # -- 相互変換 -----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "thresholds": asdict(self.thresholds),
            "fonts": asdict(self.fonts),
            "negative_words": list(self.negative_words),
            "min_shared": self.min_shared,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Settings:
        """壊れた値は既定に落とす。**起動できなくなるより既定で動くほうがよい。**"""
        if not isinstance(data, dict):
            log.warning("設定が辞書ではありません。既定値を使います")
            return cls()

        base = cls()
        return cls(
            thresholds=_load_dataclass(FlagThresholds, data.get("thresholds"), base.thresholds),
            fonts=_load_dataclass(FontSettings, data.get("fonts"), base.fonts),
            negative_words=_load_words(data.get("negative_words"), base.negative_words),
            min_shared=_load_int(
                data.get("min_shared"),
                base.min_shared,
                lo=MIN_SHARED_RANGE[0],
                hi=MIN_SHARED_RANGE[1],
            ),
        )

    def writer_config(self):
        """``io.docx_write.WriterConfig`` に変換する(``io`` は遅延輸入)。"""
        from ..io.docx_write import WriterConfig

        return WriterConfig(**asdict(self.fonts))

    def with_thresholds(self, **kwargs: float | int) -> Settings:
        return replace(self, thresholds=replace(self.thresholds, **kwargs))


def _load_dataclass(cls: type, value: Any, fallback: Any) -> Any:
    if not isinstance(value, dict):
        return fallback
    known = {f: getattr(fallback, f) for f in cls.__dataclass_fields__}
    for key, val in value.items():
        if key not in known:
            log.warning("設定に未知の項目があります: %s.%s", cls.__name__, key)
            continue
        if not isinstance(val, (int, float, str)) or isinstance(val, bool):
            log.warning("設定の値が不正です: %s.%s=%r", cls.__name__, key, val)
            continue
        try:
            known[key] = type(known[key])(val)
        except (TypeError, ValueError):
            # 数値欄に文字列が入っているなど。その項目だけ既定のままにする。
            log.warning("設定の値を解釈できません: %s.%s=%r", cls.__name__, key, val)
    return cls(**known)


def _load_words(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return fallback
    words = tuple(w.strip() for w in value if w.strip())
    # 空リストにすると否定形が一切検出されなくなる。既定に戻す(設計書 §4)。
    return words or fallback


def _load_int(value: Any, fallback: int, *, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        return fallback
    return value


def settings_path() -> Path:
    return paths.data_dir() / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> Settings:
    """設定を読む。ファイルが無い・壊れている場合は既定値を返す。"""
    p = path or settings_path()
    if not p.exists():
        return Settings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("設定を読めませんでした(%s)。既定値を使います: %s", p, exc)
        return Settings()
    return Settings.from_dict(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """設定を書く。書き込み中の中断で壊さないよう一時ファイル経由で置き換える。"""
    p = path or settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(p)
    return p
