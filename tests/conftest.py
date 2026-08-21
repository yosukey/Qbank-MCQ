"""テスト共通のフィクスチャ。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from designdata import DESIGN_Q1_VALUES, counts_from_row
from qbank_mcq.core.db import make_engine, make_session_factory
from qbank_mcq.core.migrate import ensure_schema

TESTDATA = Path(__file__).parent.parent / "testdata"

# GUI テストは画面のない環境で走る。**QApplication を作る前に**決める必要があるため
# フィクスチャではなくここで設定する。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
    from qbank_mcq.ui.workspace import Workspace

    ws = Workspace.open()
    try:
        yield ws
    finally:
        ws.close()


SAMPLE_DOCX = TESTDATA / "sample" / "exam_2025.docx"
SAMPLE_STATS = TESTDATA / "sample" / "item_stats_2025.csv"


@pytest.fixture
def loaded_workspace(qapp, isolated_data_dir: Path) -> Iterator:
    """サンプルの過去問 1 回分(統計つき)を取り込んだ ``Workspace``。

    画面の多くは「実績のある問題」が無いと何も出ない。CLI の取込経路をそのまま
    使って作るので、GUI テスト用に別のデータ生成経路を持たずに済む。
    """
    if not SAMPLE_DOCX.exists():  # pragma: no cover - サンプル未生成の開発機
        pytest.skip("testdata/sample/ がありません。python tools/make_sample_data.py で作れます")

    from qbank_mcq.__main__ import main
    from qbank_mcq.core import paths
    from qbank_mcq.ui.workspace import Workspace

    code = main(
        [
            "--db",
            str(paths.db_path()),
            "import-exam",
            "--docx",
            str(SAMPLE_DOCX),
            "--stats",
            str(SAMPLE_STATS),
        ]
    )
    assert code == 0, "サンプルの取込に失敗しました"

    ws = Workspace.open()
    try:
        yield ws
    finally:
        ws.close()


@pytest.fixture
def design_q1_counts() -> dict[str, int]:
    """設計書 §10.2 の問1の度数。"""
    return counts_from_row(DESIGN_Q1_VALUES)
