"""スキーマ移行のテスト(実装計画 §4 M2 受入条件、§7)。

受入条件: **旧版 DB から新版 DB への移行が、バックアップを残して成功する。**

現在の ``TARGET_VERSION`` は 1 なので「旧版 DB」は実在しない。そこで
``MIGRATIONS`` に一時的な移行 2 を差し込み、v1 の DB ファイルを旧版に見立てて
バックアップ・適用・失敗時の巻き戻しを検証する。実際にスキーマを上げるときは
``testdata/legacy/`` に 1 つ前の DB ファイルを置けば
``test_legacy_databases_migrate`` が自動的に拾う(実装計画 §7)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from qbank_mcq.core import paths
from qbank_mcq.core.db import make_engine
from qbank_mcq.core.migrate import (
    MIGRATIONS,
    TARGET_VERSION,
    MigrationFailedError,
    SchemaTooNewError,
    backup_database,
    ensure_schema,
    open_database,
    read_schema_version,
)

EXPECTED_TABLES = {
    "questions",
    "choice_sets",
    "choice_set_items",
    "choice_set_links",
    "question_versions",
    "tags",
    "question_tags",
    "exams",
    "exam_items",
    "item_pattern_counts",
    "item_stats",
    "exam_stats",
    "schema_meta",
}


def test_fresh_database_gets_every_table(tmp_path: Path) -> None:
    engine, result = open_database(tmp_path / "bank.sqlite")
    assert result.applied == list(range(1, TARGET_VERSION + 1))
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    assert read_schema_version(engine) == TARGET_VERSION


def test_fresh_database_takes_no_backup(tmp_path: Path, isolated_data_dir: Path) -> None:
    """まっさらな DB には守るべき中身がないのでバックアップしない。"""
    _, result = open_database(tmp_path / "bank.sqlite")
    assert result.backup is None
    assert list(paths.backup_dir().glob("*.sqlite")) == []


def test_reopening_is_a_noop(tmp_path: Path) -> None:
    db = tmp_path / "bank.sqlite"
    open_database(db)
    _, result = open_database(db)
    assert result.applied == []
    assert not result.changed


def test_schema_from_the_future_is_refused(tmp_path: Path) -> None:
    """古い exe で新しいデータを開いたら、黙って壊さず起動を止める。"""
    db = tmp_path / "bank.sqlite"
    engine, _ = open_database(db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'"))
    with pytest.raises(SchemaTooNewError):
        ensure_schema(engine, db_file=db)


# ---------------------------------------------------------------------------
# 旧版 → 新版(一時的な移行 2 を差し込んで検証する)
# ---------------------------------------------------------------------------


def _m002_add_column(conn) -> None:
    conn.execute(text("ALTER TABLE questions ADD COLUMN spare TEXT"))


def _m002_broken(conn) -> None:
    raise RuntimeError("わざと失敗させる")


@pytest.fixture
def legacy_v1_db(tmp_path: Path) -> Path:
    db = tmp_path / "bank.sqlite"
    engine, _ = open_database(db)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO questions (id, status) VALUES (1, 'active')"))
    engine.dispose()
    return db


def test_upgrade_keeps_data_and_leaves_a_backup(
    legacy_v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(MIGRATIONS, 2, _m002_add_column)

    engine = make_engine(legacy_v1_db)
    result = ensure_schema(engine, db_file=legacy_v1_db, target=2)

    assert result.from_version == 1 and result.to_version == 2
    assert result.applied == [2]
    # 移行前に必ずバックアップ(実装計画 §7)
    assert result.backup is not None and result.backup.exists()
    assert result.backup.parent == paths.backup_dir()

    assert read_schema_version(engine) == 2
    assert "spare" in {c["name"] for c in inspect(engine).get_columns("questions")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one() == 1


def test_failed_upgrade_restores_the_backup(
    legacy_v1_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失敗したらバックアップを戻して起動を中止する(実装計画 §7)。"""
    monkeypatch.setitem(MIGRATIONS, 2, _m002_broken)

    engine = make_engine(legacy_v1_db)
    with pytest.raises(MigrationFailedError):
        ensure_schema(engine, db_file=legacy_v1_db, target=2)

    # 書き戻した DB は移行前の姿(v1、データはそのまま)に戻っている。
    reopened = make_engine(legacy_v1_db)
    assert read_schema_version(reopened) == 1
    with reopened.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one() == 1


def test_missing_migration_number_is_an_error(legacy_v1_db: Path) -> None:
    engine = make_engine(legacy_v1_db)
    with pytest.raises(MigrationFailedError, match="登録されていません"):
        ensure_schema(engine, db_file=legacy_v1_db, target=2)


def test_migration_numbers_are_a_gapless_sequence() -> None:
    assert sorted(MIGRATIONS) == list(range(1, TARGET_VERSION + 1))


# ---------------------------------------------------------------------------
# バックアップ単体
# ---------------------------------------------------------------------------


def test_backup_of_missing_file_is_none(tmp_path: Path) -> None:
    assert backup_database(tmp_path / "nope.sqlite") is None


def test_backups_in_the_same_second_do_not_collide(tmp_path: Path) -> None:
    db = tmp_path / "bank.sqlite"
    open_database(db)
    first = backup_database(db)
    second = backup_database(db)
    assert first and second and first != second
    assert first.exists() and second.exists()


# ---------------------------------------------------------------------------
# 実装計画 §7: バージョンを上げるたびに旧 DB ファイルを 1 つ残す
# ---------------------------------------------------------------------------

LEGACY_DIR = Path(__file__).parent.parent / "testdata" / "legacy"
LEGACY_DBS = sorted(LEGACY_DIR.glob("*.sqlite")) if LEGACY_DIR.exists() else []


@pytest.mark.skipif(not LEGACY_DBS, reason="testdata/legacy/ に旧版 DB がまだ無い")
@pytest.mark.parametrize("legacy", LEGACY_DBS, ids=lambda p: p.name)
def test_legacy_databases_migrate(legacy: Path, tmp_path: Path) -> None:
    import shutil

    work = tmp_path / legacy.name
    shutil.copy2(legacy, work)
    engine, result = open_database(work)
    assert read_schema_version(engine) == TARGET_VERSION
    assert result.backup is not None
