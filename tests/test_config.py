"""設定の読み書き(``core.config``)。設計書 §14-10 が設定画面で扱う値。

exe 配布後にユーザーが壊れた JSON を残しても起動できなくなってはいけないため、
**壊れた値を既定に落とす**ことを反例側で厚く見る(実装計画 §6)。
"""

from __future__ import annotations

import json
from pathlib import Path

from itembank.core.config import (
    DEFAULT_MIN_SHARED,
    FontSettings,
    Settings,
    load_settings,
    save_settings,
    settings_path,
)
from itembank.core.stats import FlagThresholds
from itembank.core.typing_rules import DEFAULT_NEGATIVE_WORDS


def test_defaults_match_core_defaults() -> None:
    s = Settings()
    assert s.thresholds == FlagThresholds()
    assert s.negative_words == DEFAULT_NEGATIVE_WORDS
    assert s.min_shared == DEFAULT_MIN_SHARED


def test_roundtrip(tmp_path: Path) -> None:
    original = Settings(
        thresholds=FlagThresholds(dead_distractor_rate=0.02, persistent_min_exams=3),
        fonts=FontSettings(mincho="游明朝", columns=1),
        negative_words=("ない", "誤り"),
        min_shared=4,
    )
    path = save_settings(original, tmp_path / "settings.json")
    assert load_settings(path) == original


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "absent.json") == Settings()


def test_broken_json_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ これは JSON ではない", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_unknown_and_ill_typed_values_fall_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "thresholds": {"dead_distractor_rate": "たくさん", "low_disc": 0.2, "謎": 1},
                "fonts": ["リストは想定外"],
                "negative_words": [1, 2, 3],
                "min_shared": 99,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    s = load_settings(path)
    # 読めた値だけ効く。範囲外の min_shared(自動リンクは共通 3〜4 のみ)は既定へ。
    assert s.thresholds.low_disc == 0.2
    assert s.thresholds.dead_distractor_rate == FlagThresholds().dead_distractor_rate
    assert s.fonts == FontSettings()
    assert s.negative_words == DEFAULT_NEGATIVE_WORDS
    assert s.min_shared == DEFAULT_MIN_SHARED


def test_empty_negative_words_falls_back(tmp_path: Path) -> None:
    """否定語を空にすると否定形が一切検出されなくなる(設計書 §4)。既定に戻す。"""
    path = save_settings(Settings(negative_words=()), tmp_path / "s.json")
    assert load_settings(path).negative_words == DEFAULT_NEGATIVE_WORDS


def test_writer_config_mapping() -> None:
    config = Settings(fonts=FontSettings(gothic="游ゴシック", columns=1)).writer_config()
    assert config.gothic == "游ゴシック"
    assert config.columns == 1


def test_settings_path_follows_data_dir(isolated_data_dir: Path) -> None:
    """設定は %APPDATA%\\ItemBank に置く(設計書 §15)。"""
    assert settings_path().parent == isolated_data_dir


def test_save_replaces_atomically(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(Settings(min_shared=3), path)
    save_settings(Settings(min_shared=4), path)
    assert load_settings(path).min_shared == 4
    assert not list(tmp_path.glob("*.tmp"))
