"""テスト共通のフィクスチャ。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from designdata import DESIGN_Q1_VALUES, counts_from_row
from qbank_mcq.core.db import make_engine, make_session_factory
from qbank_mcq.core.migrate import ensure_schema

TESTDATA = Path(__file__).parent.parent / "testdata"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``%APPDATA%`` 相当を一時ディレクトリに逃がし、開発機を汚さない。"""
    d = tmp_path / "appdata"
    monkeypatch.setenv("QBANK_MCQ_DATA_DIR", str(d))
    return d


@pytest.fixture
def session() -> Iterator[Session]:
    """インメモリ SQLite の空バンク(実装計画 §6 の統合テスト用)。"""
    engine = make_engine(":memory:")
    ensure_schema(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture
def design_q1_counts() -> dict[str, int]:
    """設計書 §10.2 の問1の度数。"""
    return counts_from_row(DESIGN_Q1_VALUES)
