"""設定画面(設計書 §14-10)とその土台。

タグ管理、フラグ閾値、近似リンク閾値、基準フォント、否定語リスト、
バックアップ/復元
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from itembank.core.bank import (
    delete_tag,
    ensure_tag,
    rebuild_all_links,
    rename_tag,
    set_tags,
    tag_names,
    tag_usage,
    upsert_choice_set,
)
from itembank.core.config import Settings, load_settings
from itembank.core.db import Question, QuestionTag
from itembank.core.migrate import list_backups, restore_database

pytest.importorskip("PySide6")

from itembank.ui.settings_view import SettingsView  # noqa: E402

# ---------------------------------------------------------------------------
# タグ管理(core 側)
# ---------------------------------------------------------------------------


def _question(session: Session, tags: list[str]) -> Question:
    q = Question()
    session.add(q)
    session.flush()
    set_tags(session, q, tags)
    return q


def test_tag_usage_counts(session: Session) -> None:
    _question(session, ["発生", "エナメル質"])
    _question(session, ["発生"])
    ensure_tag(session, "未使用")

    usage = {t.name: n for t, n in tag_usage(session)}
    assert usage == {"発生": 2, "エナメル質": 1, "未使用": 0}


def test_rename_tag(session: Session) -> None:
    q = _question(session, ["発生"])
    tag = ensure_tag(session, "発生")
    rename_tag(session, tag, "歯の発生")
    assert tag_names(session, q.id) == ["歯の発生"]


def test_rename_into_existing_tag_merges(session: Session) -> None:
    """同名に改名したら統合する。**問題からタグが消えてはいけない。**"""
    q1 = _question(session, ["発生"])
    q2 = _question(session, ["歯の発生"])
    both = _question(session, ["発生", "歯の発生"])

    rename_tag(session, ensure_tag(session, "発生"), "歯の発生")

    assert tag_names(session, q1.id) == ["歯の発生"]
    assert tag_names(session, q2.id) == ["歯の発生"]
    assert tag_names(session, both.id) == ["歯の発生"]
    assert [t.name for t, _ in tag_usage(session)] == ["歯の発生"]


def test_delete_tag_keeps_the_question(session: Session) -> None:
    q = _question(session, ["発生", "エナメル質"])
    delete_tag(session, ensure_tag(session, "発生"))

    assert session.get(Question, q.id) is not None
    assert tag_names(session, q.id) == ["エナメル質"]
    assert session.query(QuestionTag).count() == 1


def test_rename_to_blank_is_rejected(session: Session) -> None:
    with pytest.raises(ValueError):
        rename_tag(session, ensure_tag(session, "発生"), "  ")


# ---------------------------------------------------------------------------
# 近似リンクの張り直し(設計書 §6.3)
# ---------------------------------------------------------------------------


def test_rebuild_all_links_respects_the_threshold(session: Session) -> None:
    base = ["象牙質", "エナメル質", "セメント質", "歯髄", "歯根膜"]
    upsert_choice_set(session, base)  # 1
    upsert_choice_set(session, [*base[:4], "歯肉"])  # 2: 1 と共通 4
    upsert_choice_set(session, [*base[:3], "歯肉", "骨"])  # 3: 1 と共通 3、2 と共通 4

    assert rebuild_all_links(session, min_shared=3) == 3
    # 閾値を上げたら古いリンクは残らない(露出管理が過剰に効かないように)。
    assert rebuild_all_links(session, min_shared=4) == 2


# ---------------------------------------------------------------------------
# バックアップ / 復元
# ---------------------------------------------------------------------------


def test_restore_backs_up_the_current_database(tmp_path: Path) -> None:
    db = tmp_path / "itembank.sqlite"
    db.write_bytes(b"NOW")
    backup = tmp_path / "backup" / "20250101-000000.sqlite"
    backup.parent.mkdir()
    backup.write_bytes(b"OLD")

    previous = restore_database(backup, db)

    assert db.read_bytes() == b"OLD"
    assert previous is not None and previous.read_bytes() == b"NOW"


def test_restore_refuses_the_same_file(tmp_path: Path) -> None:
    db = tmp_path / "itembank.sqlite"
    db.write_bytes(b"NOW")
    with pytest.raises(ValueError):
        restore_database(db, db)


def test_restore_missing_backup(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore_database(tmp_path / "absent.sqlite", tmp_path / "db.sqlite")


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------


def test_view_saves_and_reloads(workspace) -> None:
    view = SettingsView(workspace)
    view.dead_rate.setValue(0.02)
    view.min_shared.setValue(4)
    view.mincho.setText("游明朝")
    view.words.setPlainText("ない\n誤っている\n")
    saved = view.save()

    assert saved.thresholds.dead_distractor_rate == pytest.approx(0.02)
    assert saved.min_shared == 4
    assert saved.fonts.mincho == "游明朝"
    assert saved.negative_words == ("ない", "誤っている")
    # ファイルに落ちている。
    assert load_settings() == saved
    assert workspace.settings == saved


def test_view_blank_font_falls_back(workspace) -> None:
    view = SettingsView(workspace)
    view.mincho.setText("   ")
    assert view.collect().fonts.mincho == Settings().fonts.mincho


def test_view_lists_tags(workspace) -> None:
    _question(workspace.session, ["発生"])
    view = SettingsView(workspace)
    view.refresh()

    assert view.tag_table.rowCount() == 1
    assert view.tag_table.item(0, 0).text() == "発生"
    assert view.tag_table.item(0, 1).text() == "1"


def test_view_backup_button(workspace) -> None:
    workspace.commit()
    view = SettingsView(workspace)
    dest = workspace.backup()
    assert dest is not None and dest.exists()
    assert list_backups() == [dest]
    assert view.db_label.text() == str(workspace.db_file)


def test_changing_min_shared_rebuilds_links(workspace, monkeypatch) -> None:
    """閾値を変えたらリンクを張り直す(古い緩いリンクを残さない)。"""
    from itembank.ui import settings_view as module

    calls: list[int] = []
    real = module.rebuild_all_links

    def spy(session, *, min_shared: int) -> int:
        calls.append(min_shared)
        return real(session, min_shared=min_shared)

    monkeypatch.setattr(module, "rebuild_all_links", spy)

    view = SettingsView(workspace)
    view.min_shared.setValue(Settings().min_shared)
    view.save()
    assert calls == []

    view.min_shared.setValue(4)
    view.save()
    assert calls == [4]
    assert "張り直しました" in view.status.text()
