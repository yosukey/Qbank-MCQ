"""テスト共通のフィクスチャ。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from designdata import DESIGN_Q1_VALUES, counts_from_row
from itembank.core.db import make_engine, make_session_factory
from itembank.core.migrate import ensure_schema

TESTDATA = Path(__file__).parent.parent / "testdata"

# GUI テストは画面のない環境で走る。**QApplication を作る前に**決める必要があるため
# フィクスチャではなくここで設定する。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``%APPDATA%`` 相当を一時ディレクトリに逃がし、開発機を汚さない。"""
    d = tmp_path / "appdata"
    monkeypatch.setenv("ITEMBANK_DATA_DIR", str(d))
    return d


@pytest.fixture
def session() -> Iterator[Session]:
    """インメモリ SQLite の空バンク(実装計画 §6 の統合テスト用)。"""
    engine = make_engine(":memory:")
    ensure_schema(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture(scope="session")
def qapp():
    """``QApplication`` は 1 プロセスに 1 つ。PySide6 が無い環境では飛ばす。

    実装計画 §0 のとおり GUI は任意依存であり、CLI だけでも運用サイクルは一周する。
    ここで落とすと「GUI を入れていない開発機では pytest が通らない」ことになる。
    """
    pytest.importorskip("PySide6", reason='GUI テストには pip install -e ".[gui]" が要ります')
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def workspace(qapp, isolated_data_dir: Path) -> Iterator:
    """GUI が使う ``Workspace``(一時ディレクトリの実ファイル DB)。"""
    from itembank.ui.workspace import Workspace

    ws = Workspace.open()
    try:
        yield ws
    finally:
        ws.close()


@pytest.fixture
def design_q1_counts() -> dict[str, int]:
    """設計書 §10.2 の問1の度数。"""
    return counts_from_row(DESIGN_Q1_VALUES)
