"""ウィンドウに出す文言。Qt に依存しない。

バージョンの表示はリリースの受入条件そのもの(実装計画 §8: タグ ``v0.3.0`` で
作った配布物は ``Qbank-MCQ-0.3.0-setup.exe`` になり、起動した窓にも ``0.3.0`` が
出る)なので、Qt なしでテストできるようここに切り出してある。
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.paths import APP_NAME
from ..version import VERSION


def window_title(version: str | None = None) -> str:
    """メインウィンドウのタイトル。``Qbank-MCQ 0.3.0``。"""
    return f"{APP_NAME} {version or VERSION}"


def version_label(version: str | None = None) -> str:
    """窓の中に大きく出すバージョン表記。"""
    return f"バージョン {version or VERSION}"


def about_rows(
    *,
    version: str | None = None,
    schema_version: int | None = None,
    data_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    counts: dict[str, int] | None = None,
    frozen: bool | None = None,
) -> list[tuple[str, str]]:
    """窓に並べる「項目 → 値」の行。

    ``frozen`` は PyInstaller で固めた exe かどうか。省略すると実行中の状態を見る。
    配布物の不具合報告では「どのバージョンの、exe 版か開発版か」が最初に要るため、
    窓から読めるようにしておく。
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))

    rows: list[tuple[str, str]] = [
        ("バージョン", version or VERSION),
        ("実行形態", "配布 exe" if frozen else "開発環境 (Python)"),
        ("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        ("スキーマ版", "—" if schema_version is None else str(schema_version)),
        ("データ", "—" if data_dir is None else str(data_dir)),
        ("DB", "—" if db_path is None else str(db_path)),
    ]
    for label, value in (counts or {}).items():
        rows.append((label, str(value)))
    return rows
