"""スキーマ移行(通し番号方式)。

実装計画 §7 / §0:

- exe 配布後はユーザーが DB を直せない。移行機構を後付けするのは高くつくので初日から入れる
- 起動時に ``schema_meta.schema_version`` を読み、不足分を順に適用する
- **適用前に必ずバックアップ**。失敗したらバックアップを戻して起動を中止し、ログに理由を残す
- Alembic は単独 SQLite には重い。自前で十分

新しい移行を足すときは関数を書いて ``MIGRATIONS`` に次の番号で登録し、
``testdata/legacy/`` に**その 1 つ前のバージョンの DB ファイルを 1 つ残す**
(実装計画 §7)。``tests/test_migrate.py`` がそれを総当たりで移行してみる。
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from . import paths
from .db import Base

log = logging.getLogger(__name__)

SCHEMA_VERSION_KEY = "schema_version"


def m001_initial(conn: Connection) -> None:
    """設計書 §8 のスキーマを作る。"""
    Base.metadata.create_all(bind=conn)


#: 通し番号 → 移行関数。番号は 1 から連番で、欠番を作らない。
MIGRATIONS: dict[int, Callable[[Connection], None]] = {
    1: m001_initial,
}

#: アプリが期待するスキーマ版。
TARGET_VERSION: int = max(MIGRATIONS)


class SchemaTooNewError(RuntimeError):
    """DB のスキーマ版がアプリより新しい。古い exe で新しいデータを開いた場合。"""


class MigrationFailedError(RuntimeError):
    """移行に失敗した。バックアップを戻したうえで起動を中止する。"""


def read_schema_version(engine: Engine) -> int:
    """現在のスキーマ版。まっさらな DB では 0。"""
    if not inspect(engine).has_table("schema_meta"):
        return 0
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM schema_meta WHERE key = :k"), {"k": SCHEMA_VERSION_KEY}
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _write_schema_version(conn: Connection, version: int) -> None:
    conn.execute(
        text(
            "INSERT INTO schema_meta (key, value) VALUES (:k, :v) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        {"k": SCHEMA_VERSION_KEY, "v": str(version)},
    )


def backup_database(db_file: Path, *, dest_dir: Path | None = None) -> Path | None:
    """``backup/YYYYMMDD-HHMMSS.sqlite`` に控えを取り、そのパスを返す。

    DB ファイルがまだ無ければ ``None``(取るべき中身がない)。
    """
    if not db_file.exists():
        return None
    dest_dir = dest_dir or paths.backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{stamp}.sqlite"
    n = 1
    while dest.exists():  # 同一秒に 2 回走った場合
        dest = dest_dir / f"{stamp}-{n}.sqlite"
        n += 1
    shutil.copy2(db_file, dest)
    log.info("バックアップを作成しました: %s", dest)
    return dest


def restore_database(backup_file: Path, db_file: Path) -> Path | None:
    """バックアップを書き戻す(設計書 §14-10 の「復元」)。

    **書き戻す前に、いま入っている DB の控えを取る。** 復元先を選び間違えたときに
    取り返しがつかなくなるのを防ぐ。戻り値はその控えのパス(元 DB が無ければ None)。

    呼ぶ前にエンジンを ``dispose()`` しておくこと。SQLite はファイルを開いたまま
    差し替えると、開きっぱなしの接続が古い内容を見続ける。
    """
    if not backup_file.exists():
        raise FileNotFoundError(f"バックアップがありません: {backup_file}")
    if backup_file.resolve() == db_file.resolve():
        raise ValueError("復元元と復元先が同じファイルです")

    previous = backup_database(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_file, db_file)
    log.warning("バックアップを復元しました: %s → %s", backup_file, db_file)
    return previous


def list_backups(dest_dir: Path | None = None) -> list[Path]:
    """新しい順のバックアップ一覧(復元ダイアログの初期表示に使う)。"""
    d = dest_dir or paths.backup_dir()
    if not d.exists():
        return []
    return sorted(d.glob("*.sqlite"), key=lambda p: p.name, reverse=True)


@dataclass
class MigrationResult:
    from_version: int
    to_version: int
    applied: list[int]
    backup: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def ensure_schema(
    engine: Engine,
    *,
    db_file: Path | None = None,
    target: int = TARGET_VERSION,
) -> MigrationResult:
    """起動時に呼ぶ。不足している移行を順に適用する。

    ``db_file`` を渡すと、既存 DB の更新前に自動バックアップを取る
    (まっさらな DB を作るときは取らない)。移行が途中で失敗したらバックアップを
    書き戻し、``MigrationFailedError`` を送出する。
    """
    current = read_schema_version(engine)
    if current > target:
        raise SchemaTooNewError(
            f"DB のスキーマ版 {current} はこのアプリ(対応 {target})より新しいため開けません。"
            "アプリを更新してください。"
        )
    if current == target:
        return MigrationResult(current, target, [])

    backup: Path | None = None
    if current > 0 and db_file is not None:
        backup = backup_database(db_file)

    applied: list[int] = []
    try:
        for version in range(current + 1, target + 1):
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise MigrationFailedError(f"移行 {version} が登録されていません")
            log.info("移行 %d を適用します (%s)", version, migration.__name__)
            with engine.begin() as conn:
                migration(conn)
                _write_schema_version(conn, version)
            applied.append(version)
    except Exception as exc:
        log.exception("移行に失敗しました: %s", exc)
        if backup is not None and db_file is not None:
            engine.dispose()
            shutil.copy2(backup, db_file)
            log.error("バックアップを書き戻しました: %s → %s", backup, db_file)
        raise MigrationFailedError(
            f"スキーマ移行に失敗しました({current} → {target})。"
            + (f"バックアップ {backup} を書き戻しました。" if backup else "")
            + f"原因: {exc}"
        ) from exc

    return MigrationResult(current, target, applied, backup)


def open_database(
    db_file: Path | None = None, *, echo: bool = False
) -> tuple[Engine, MigrationResult]:
    """DB を開き、必要なら移行してからエンジンを返す。

    ``db_file=None`` なら ``paths.db_path()``(``%APPDATA%\\ItemBank``)を使う。
    """
    from .db import make_engine

    path = db_file or paths.db_path()
    engine = make_engine(path, echo=echo)
    result = ensure_schema(engine, db_file=path)
    return engine, result
