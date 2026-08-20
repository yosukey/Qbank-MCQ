"""開いている DB・セッション・設定をまとめて画面に配る器。

画面ごとにセッションを作ると、あるタブで作った問題が別のタブから見えないという
分かりにくい不整合が起きる。**GUI は 1 本のセッションを共有し、操作の区切りで
コミットする**。``expire_on_commit=False`` なので、コミット後もオブジェクトは
そのまま読める(``core.db.make_session_factory``)。

起動時のスキーマ移行もここで行う。設計書 §15:

    起動時にスキーマ版を確認し、旧DBは自動バックアップの上でマイグレーション
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..core import paths
from ..core.config import Settings, load_settings, save_settings
from ..core.db import make_session_factory
from ..core.migrate import MigrationResult, backup_database, open_database

log = logging.getLogger(__name__)


@dataclass
class Workspace:
    """1 つの DB を開いた状態。"""

    db_file: Path
    engine: Engine
    session: Session
    settings: Settings
    migration: MigrationResult

    @classmethod
    def open(cls, db_file: Path | None = None) -> Workspace:
        """DB を開き、必要なら移行してから返す。"""
        target = db_file or paths.db_path()
        engine, migration = open_database(target)
        if migration.changed:
            log.info(
                "スキーマを %s → %s に移行しました(バックアップ: %s)",
                migration.from_version,
                migration.to_version,
                migration.backup,
            )
        return cls(
            db_file=target,
            engine=engine,
            session=make_session_factory(engine)(),
            settings=load_settings(),
            migration=migration,
        )

    # -- 設定 ---------------------------------------------------------------
    def update_settings(self, settings: Settings) -> None:
        self.settings = settings
        save_settings(settings)

    # -- トランザクション ---------------------------------------------------
    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def backup(self) -> Path | None:
        """手動バックアップ(設計書 §14-10)。**コミットしてから取る。**"""
        self.commit()
        return backup_database(self.db_file)

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()
