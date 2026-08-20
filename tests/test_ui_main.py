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


def test_main_window_has_every_screen(workspace) -> None:
    """設計書 §14 の 10 画面。7 と 8 はタブ、2・3 はダイアログで出す。"""
    window = MainWindow(workspace)
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == [
        "問題バンク",
        "選択肢セット",
        "選択肢アイテム",
        "過去問一括取込",
        "試験セット",
        "出力",
        "統計取込",
        "設定",
    ]


def test_main_window_refresh_all_touches_every_tab(loaded_workspace) -> None:
    window = MainWindow(loaded_workspace)
    window.refresh_all()  # 例外なく通ること(タブが増えても壊れない)


def test_main_window_opens_the_editor_and_the_detail(loaded_workspace) -> None:
    window = MainWindow(loaded_workspace)
    question_id = window.bank_view.model.candidate_at(0).question_id

    editor = window.open_editor(question_id=question_id)
    assert editor.question.id == question_id
    editor.reject()

    detail = window.open_detail(question_id)
    assert detail.history.question_id == question_id
    detail.reject()

    duplicate = window.open_duplicate(question_id)
    assert duplicate.question is None
    assert duplicate.derive_source.id == question_id
    duplicate.reject()


def test_show_question_switches_to_the_bank(loaded_workspace) -> None:
    """フラグ一覧などからバンクの当該行へ飛ぶ導線(設計書 §9.3)。"""
    window = MainWindow(loaded_workspace)
    question_id = window.bank_view.model.candidate_at(1).question_id

    window.tabs.setCurrentWidget(window.settings_view)
    window.show_question(question_id)

    assert window.tabs.currentWidget() is window.bank_view
    assert window.bank_view.selected_candidate().question_id == question_id


def test_check_for_update_shows_the_result(workspace, monkeypatch) -> None:
    """更新の確認は状態表示に出すだけ(実装計画 M7-5)。自動更新はしない。"""
    from itembank.core.updates import VersionInfo
    from itembank.ui import main_window as module

    monkeypatch.setattr(
        module,
        "check_update",
        lambda current: (VersionInfo("9.9.9"), "新しい版があります: v9.9.9"),
    )
    window = MainWindow(workspace)
    assert "9.9.9" in window.check_for_update()
    assert "9.9.9" in window.statusBar().currentMessage()


def test_check_for_update_survives_a_network_failure(workspace, monkeypatch) -> None:
    from itembank.ui import main_window as module

    monkeypatch.setattr(
        module, "check_update", lambda current: (None, "更新を確認できませんでした")
    )
    window = MainWindow(workspace)
    assert "確認できませんでした" in window.check_for_update()


def test_main_window_backup(workspace) -> None:
    window = MainWindow(workspace)
    window._backup()
    assert "バックアップ" in window.statusBar().currentMessage()
