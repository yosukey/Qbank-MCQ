"""ユーザーデータの置き場所の解決と初回セットアップ。

設計書 §15 / 実装計画 §11 より:

- exe は ``%LOCALAPPDATA%\\Programs\\Qbank-MCQ`` に入るが、そこは書込不可を前提とする
- **ユーザーデータは ``%APPDATA%\\Qbank-MCQ\\``**(DB・バックアップ・テンプレート・
  取込原本・ログ)。exe と同居させない

開発は Windows 以外でも行うため、Windows 以外では XDG 準拠の場所にフォールバックする。
``QBANK_MCQ_DATA_DIR`` 環境変数があれば常にそれを優先する(テストで使う)。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

APP_NAME = "Qbank-MCQ"
ENV_DATA_DIR = "QBANK_MCQ_DATA_DIR"

DB_FILENAME = "qbank_mcq.sqlite"


def data_dir() -> Path:
    """ユーザーデータのルート。存在しなければ作る。"""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        root = base / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = (Path(base) if base else Path.home() / ".local" / "share") / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sub(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    """SQLite ファイルのパス。ファイル自体は作らない。"""
    return data_dir() / DB_FILENAME


def backup_dir() -> Path:
    """マイグレーション前バックアップの置き場(実装計画 §4 M2)。"""
    return _sub("backup")


def log_dir() -> Path:
    """ログの置き場。exe 配布後は不具合の手がかりがログしかない(実装計画 §1)。"""
    return _sub("logs")


def images_dir() -> Path:
    """docx から抽出したインライン画像の置き場(設計書 §5.1-9)。"""
    return _sub("images")


def imports_dir() -> Path:
    """取込原本の控え。"""
    return _sub("imports")


def exports_dir() -> Path:
    """冊子 docx・正答キー csv・レポート xlsx の既定出力先。"""
    return _sub("exports")


_LOG_CONFIGURED = False


def setup_logging(level: int = logging.INFO, *, to_console: bool = True) -> Path:
    """``logs/qbank_mcq.log`` へのローテーティングログを設定し、そのパスを返す。

    二重呼び出しはハンドラを増やさない。
    """
    global _LOG_CONFIGURED
    path = log_dir() / "qbank_mcq.log"
    if _LOG_CONFIGURED:
        return path

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if to_console:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    _LOG_CONFIGURED = True
    logging.getLogger(__name__).info("data_dir=%s", data_dir())
    return path
