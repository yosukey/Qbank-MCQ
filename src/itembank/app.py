"""GUI エントリ(実装計画 §5 の ``app.py``)。

    itembank gui            # CLI 経由
    python -m itembank.app  # 直接

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

log = logging.getLogger(__name__)

GUI_MISSING_MESSAGE = (
    "GUI には PySide6 が要ります。次のいずれかで入れてください:\n"
    '    pip install -e ".[gui]"\n'
    "    pip install PySide6\n"
    "GUI なしでも CLI(itembank --help)で運用サイクルは一周します。"
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

    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    app.setApplicationName("ItemBank")
    app.setOrganizationName("ItemBank")

    try:
        workspace = Workspace.open(db_file)
    except Exception as exc:
        log.exception("DB を開けませんでした")
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None,
            "起動できません",
            f"DB を開けませんでした。\n\n{exc}\n\nログ: {paths.log_dir() / 'itembank.log'}",
        )
        return 1

    window = MainWindow(workspace)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
