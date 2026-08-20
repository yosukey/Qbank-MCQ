"""メインウィンドウと GUI エントリ。

GUI が任意依存であること(実装計画 §0: CLI だけで一周する)と、起動時に
スキーマ移行が走ること(設計書 §15)をここで押さえる。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from itembank.__main__ import build_parser
from itembank.app import GUI_MISSING_MESSAGE
from itembank.app import main as gui_main

pytest.importorskip("PySide6")

from itembank.ui.main_window import MainWindow  # noqa: E402
from itembank.ui.workspace import Workspace  # noqa: E402


def test_cli_has_gui_subcommand() -> None:
    args = build_parser().parse_args(["gui"])
    assert args.command == "gui"


def test_gui_entry_reports_missing_pyside(monkeypatch, capsys) -> None:
    """PySide6 が無い環境では、何をすればよいかを出して終える。"""
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
    assert gui_main([]) == 2
    assert GUI_MISSING_MESSAGE in capsys.readouterr().err


def test_workspace_opens_and_migrates(isolated_data_dir: Path) -> None:
    ws = Workspace.open()
    try:
        from itembank.core.migrate import TARGET_VERSION, read_schema_version

        assert ws.db_file.parent == isolated_data_dir
        assert read_schema_version(ws.engine) == TARGET_VERSION
        assert ws.settings.min_shared >= 3
    finally:
        ws.close()


def test_main_window_shows_the_database_in_the_status_bar(workspace) -> None:
    window = MainWindow(workspace)
    assert str(workspace.db_file) in window.statusBar().currentMessage()
    assert window.tabs.count() >= 1


def test_main_window_refresh_all_touches_every_tab(workspace) -> None:
    window = MainWindow(workspace)
    window.refresh_all()  # 例外なく通ること(タブが増えても壊れない)


def test_main_window_backup(workspace) -> None:
    window = MainWindow(workspace)
    window._backup()
    assert "バックアップ" in window.statusBar().currentMessage()
