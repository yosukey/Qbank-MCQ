"""GUI エントリ(実装計画 §5)。PyInstaller はこのファイルを入口に固める。

CLI(``__main__.py``)と同じロジック層を使う。ここがやるのは

1. ログの初期化(配布後は不具合の手がかりがログしかない — 実装計画 §1)
2. DB を開いてスキーマ移行(失敗したらダイアログで止める — 実装計画 §7)
3. 窓を出す

だけ。画面の中身は M4 で ``ui/`` に足していく。
"""

from __future__ import annotations

import logging
import sys

from .core import paths
from .core.migrate import MigrationFailedError, SchemaTooNewError, open_database
from .version import VERSION

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """GUI を起動する。終了コードは Qt のイベントループの戻り値。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:  # pragma: no cover - 開発環境で GUI 依存を入れていない場合
        print(
            "PySide6 が入っていません。開発環境では次で入れてください:\n"
            '    pip install -e ".[gui]"\n'
            "CLI だけなら `qbank --help` が使えます。",
            file=sys.stderr,
        )
        return 2

    paths.setup_logging()
    log.info("%s %s を起動します", paths.APP_NAME, VERSION)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(paths.APP_NAME)
    app.setApplicationVersion(VERSION)

    from .ui.main_window import MainWindow, bank_counts

    engine = None
    schema_version: int | None = None
    counts: dict[str, int] | None = None
    note: str | None = None
    try:
        engine, result = open_database()
        schema_version = result.to_version
        if result.changed:
            note = f"スキーマを {result.from_version} → {result.to_version} に移行しました" + (
                f"(バックアップ: {result.backup})" if result.backup else ""
            )
        from .core.db import make_session_factory

        with make_session_factory(engine)() as session:
            counts = bank_counts(session)
    except (MigrationFailedError, SchemaTooNewError) as exc:
        # 移行に失敗した DB で画面を開くと壊し方が増えるだけなので、ここで止める。
        log.exception("DB を開けませんでした")
        QMessageBox.critical(None, paths.APP_NAME, str(exc))
        return 1

    window = MainWindow(schema_version=schema_version, counts=counts, note=note)
    window.show()
    try:
        return app.exec()
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
