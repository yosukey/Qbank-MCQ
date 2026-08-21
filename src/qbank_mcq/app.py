"""GUI エントリ(実装計画 §5 の ``app.py``)。

    qbank gui            # CLI 経由
    python -m qbank_mcq.app  # 直接

PySide6 は任意依存(``pip install -e ".[gui]"``)。入っていなければ何をすれば
よいかを表示して終える。**CLI だけで運用サイクルは一周する**ので、GUI が無い
環境でも詰まない(実装計画 §0)。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .core import paths
from .version import VERSION

log = logging.getLogger(__name__)

GUI_MISSING_MESSAGE = (
    "GUI には PySide6 が要ります。次のいずれかで入れてください:\n"
    '    pip install -e ".[gui]"\n'
    "    pip install PySide6\n"
    "GUI なしでも CLI(qbank --help)で運用サイクルは一周します。"
)


def main(argv: Sequence[str] | None = None, *, db_file: Path | None = None) -> int:
    """QApplication を起こしてメインウィンドウを出す。"""
    paths.setup_logging(logging.INFO)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(GUI_MISSING_MESSAGE, file=sys.stderr)
        return 2

    from .ui.main_window import MainWindow
    from .ui.workspace import Workspace

    log.info("%s %s を起動します", paths.APP_NAME, VERSION)

    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    app.setApplicationName(paths.APP_NAME)
    app.setOrganizationName(paths.APP_NAME)
    app.setApplicationVersion(VERSION)

    try:
        workspace = Workspace.open(db_file)
    except Exception as exc:
        log.exception("DB を開けませんでした")
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None,
            "起動できません",
            f"DB を開けませんでした。\n\n{exc}\n\nログ: {paths.log_dir() / 'qbank_mcq.log'}",
        )
        return 1

    window = MainWindow(workspace)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
