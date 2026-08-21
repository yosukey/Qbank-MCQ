"""起動確認用のメインウィンドウ(実装計画 §2.2 スパイク②)。

本番の画面構成は M4 で作る。ここでは配布物が起動したことと**バージョン**、それに
DB が読めていることを目視できれば足りる。表示文言は ``about.py`` にある。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.db import ChoiceSet, Exam, Question
from . import about


def bank_counts(session: Session) -> dict[str, int]:
    """窓に出す件数。SQLite が読めていることの確認を兼ねる。"""
    return {
        "問題": session.scalar(select(func.count()).select_from(Question)) or 0,
        "選択肢セット": session.scalar(select(func.count()).select_from(ChoiceSet)) or 0,
        "試験": session.scalar(select(func.count()).select_from(Exam)) or 0,
    }


class MainWindow(QMainWindow):
    """アプリ名とバージョンを掲げ、DB の所在と件数を並べるだけの窓。"""

    def __init__(
        self,
        *,
        schema_version: int | None = None,
        counts: dict[str, int] | None = None,
        note: str | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(about.window_title())

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel(paths.APP_NAME, central)
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 8)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # バージョンはタイトルバーにも出るが、スクリーンショットで拾えるよう窓の中にも出す。
        version = QLabel(about.version_label(), central)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(version)

        line = QFrame(central)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for label, value in about.about_rows(
            schema_version=schema_version,
            data_dir=paths.data_dir(),
            db_path=paths.db_path(),
            counts=counts,
        ):
            field = QLabel(value, central)
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(f"{label}:", field)
        layout.addLayout(form)

        if note:
            message = QLabel(note, central)
            message.setWordWrap(True)
            layout.addWidget(message)

        layout.addStretch(1)
        self.setCentralWidget(central)
        self.resize(560, 360)
        self._center()

    def _center(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())
