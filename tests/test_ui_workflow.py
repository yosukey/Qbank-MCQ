"""取込・試験セット・出力・統計取込(設計書 §14-6〜9)。

設計書 §1 の運用サイクルを**画面から一周させる**:

    過去問一括取込(局面A) → 選定 → finalize → 出力 → 統計取込(局面B)

局面の取り違え(設計書 §1.4, §17)が起きないことも見る。統計取込の画面は
確定済みの試験しか受け付けず、問題を作る導線を持たない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from itembank.core.db import Exam
from itembank.core.exam import build_candidates

pytest.importorskip("PySide6")

from itembank.ui.exam_builder import ExamBuilderView  # noqa: E402
from itembank.ui.export_view import ExportView  # noqa: E402
from itembank.ui.import_view import ImportView  # noqa: E402
from itembank.ui.stats_import import StatsImportView  # noqa: E402

TESTDATA = Path(__file__).parent.parent / "testdata"
SAMPLE = TESTDATA / "sample"
SAMPLE_DOCX = SAMPLE / "exam_2025.docx"
SAMPLE_STATS = SAMPLE / "item_stats_2025.csv"

pytestmark = pytest.mark.skipif(
    not SAMPLE_DOCX.exists(),
    reason="testdata/sample/ がありません。python tools/make_sample_data.py で作れます",
)


def _import_view(workspace, *, stats: Path | None = SAMPLE_STATS) -> ImportView:
    view = ImportView(workspace)
    view.docx_edit.setText(str(SAMPLE_DOCX))
    view.stats_edit.setText(str(stats) if stats else "")
    return view


# ---------------------------------------------------------------------------
# 局面A: 過去問一括取込(設計書 §14-6)
# ---------------------------------------------------------------------------


def test_preview_shows_formatting_and_validation(workspace) -> None:
    view = _import_view(workspace)
    assert view.load_preview()

    html = view.preview.toHtml()
    assert "問 1" in html
    assert view.issue_list.count() >= 1
    assert "設問" in view.status.text()
    # 受験者数の導出は必ず画面に出す(設計書 §9.2-2)。
    assert "受験者数" in view.status.text()
    assert view.import_button.isEnabled()


def test_preview_reports_a_broken_csv(workspace) -> None:
    view = ImportView(workspace)
    view.docx_edit.setText(str(SAMPLE_DOCX))
    view.stats_edit.setText(str(SAMPLE / "broken_missing_column.csv"))

    view.load_preview()
    assert not view.import_button.isEnabled()
    assert "不整合" in view.status.text() or "読めませんでした" in view.status.text()


def test_preview_without_a_docx(workspace) -> None:
    view = ImportView(workspace)
    assert not view.load_preview()
    assert "docx を選んでください" in view.status.text()


def test_import_registers_the_bank_and_the_stats(workspace) -> None:
    view = _import_view(workspace)
    view.name_edit.setText("2025年度 本試験")
    view.date_edit.setText("2025-02-10")
    view.load_preview()

    report = view.run_import()
    assert report is not None and not report.blocked
    assert len(report.registered) == 15
    assert report.stats_written == 15
    assert report.exam.status == "imported"
    assert report.exam.name == "2025年度 本試験"
    assert "登録" in view.status.text()

    candidates = build_candidates(workspace.session)
    assert len(candidates) == 15
    assert all(c.p is not None for c in candidates)


def test_import_without_stats_leaves_drafts(workspace) -> None:
    """正答が分からない設問は draft で登録する(捨てない。設計書 §2.5)。"""
    view = _import_view(workspace, stats=None)
    view.load_preview()
    report = view.run_import()

    assert report is not None
    assert len(report.drafts) == 15
    assert report.stats_written == 0
    assert "下書き" in view.status.text()


def test_second_import_reports_duplicates(loaded_workspace) -> None:
    """同じ docx をもう一度入れたら重複として知らせる(設計書 §1.4)。"""
    view = _import_view(loaded_workspace)
    view.load_preview()
    report = view.run_import()

    assert report is not None
    assert len(report.duplicates) == 15
    assert "重複の疑い" in view.status.text()


# ---------------------------------------------------------------------------
# 試験セット作成(設計書 §14-7)
# ---------------------------------------------------------------------------


def test_selection_then_finalize(loaded_workspace, monkeypatch) -> None:
    view = ExamBuilderView(loaded_workspace)
    view.total.setValue(5)
    view.current_year.setValue(2026)
    view.run_selection()

    assert len(view.selected) == 5
    assert view.selected_table.rowCount() == 5
    assert "選定" in view.status.text()

    view.name_edit.setText("定期試験2026")
    view.date_edit.setText("2026-02-10")
    exam = view.create_or_update_exam()
    assert exam is not None and exam.status == "draft"
    assert [i.position for i in exam.items] == [1, 2, 3, 4, 5]

    view.run_check()
    assert view.check_list.count() >= 1

    monkeypatch.setattr(view, "_confirm_warnings", lambda warnings: True)
    assert view.run_finalize()
    assert exam.status == "finalized"
    assert "確定しました" in view.status.text()


def test_swapping_candidates(loaded_workspace) -> None:
    view = ExamBuilderView(loaded_workspace)
    view.total.setValue(3)
    view.run_selection()
    first = view.selected[0]

    view.selected_table.selectRow(0)
    view._move(1)
    assert view.selected[1] is first

    view.selected_table.selectRow(1)
    view._remove_selected()
    assert first not in view.selected
    assert len(view.selected) == 2

    view.pool_table.selectRow(0)
    view._add_selected()
    assert len(view.selected) == 3


def test_finalized_exam_is_locked(loaded_workspace) -> None:
    """確定後はセット・使用版・正答を変更できない(設計書 §13.3)。"""
    view = ExamBuilderView(loaded_workspace)
    imported = loaded_workspace.session.query(Exam).first()
    assert imported.status == "imported"

    view.exam_box.setCurrentIndex(view.exam_box.findData(imported.id))
    assert not view.create_button.isEnabled()
    assert not view.finalize_button.isEnabled()
    assert "変更できません" in view.status.text()


def test_creating_an_exam_needs_a_selection(loaded_workspace) -> None:
    view = ExamBuilderView(loaded_workspace)
    assert view.create_or_update_exam() is None
    assert "空です" in view.status.text()


def test_finalize_without_an_exam(loaded_workspace) -> None:
    view = ExamBuilderView(loaded_workspace)
    assert not view.run_finalize()
    assert "先に" in view.status.text()


# ---------------------------------------------------------------------------
# 出力(設計書 §14-8)
# ---------------------------------------------------------------------------


def test_exports_every_artifact(loaded_workspace, tmp_path: Path) -> None:
    view = ExportView(loaded_workspace)
    exam = loaded_workspace.session.query(Exam).first()

    written = view.export(exam, ["booklet", "key", "crosswalk", "report"], tmp_path)
    names = sorted(p.name for p in written)
    assert names == [
        f"answer_key_{exam.id}.csv",
        f"booklet_{exam.id}.docx",
        f"crosswalk_{exam.id}.xlsx",
        f"report_{exam.id}.xlsx",
    ]
    assert all(p.exists() and p.stat().st_size > 0 for p in written)
    assert view.result_list.count() == 4


def test_booklet_uses_the_configured_fonts(loaded_workspace, tmp_path: Path) -> None:
    """冊子の体裁は設定画面の基準フォントに従う(設計書 §14-10)。"""
    from dataclasses import replace

    from docx import Document

    from itembank.core.config import FontSettings

    settings = replace(loaded_workspace.settings, fonts=FontSettings(mincho="游明朝"))
    loaded_workspace.update_settings(settings)

    view = ExportView(loaded_workspace)
    exam = loaded_workspace.session.query(Exam).first()
    path = view.export(exam, ["booklet"], tmp_path)[0]

    document = Document(str(path))
    xml = document.paragraphs[-3]._p.xml
    assert "游明朝" in xml


def test_export_reports_a_failure_without_losing_the_rest(
    loaded_workspace, tmp_path: Path, monkeypatch
) -> None:
    from itembank.ui import export_view as module

    def boom(*args, **kwargs):
        raise RuntimeError("書けません")

    monkeypatch.setattr(module, "write_booklet", boom)
    view = ExportView(loaded_workspace)
    exam = loaded_workspace.session.query(Exam).first()

    written = view.export(exam, ["booklet", "key"], tmp_path)
    assert [p.name for p in written] == [f"answer_key_{exam.id}.csv"]
    assert "失敗" in view.result_list.item(0).text()


# ---------------------------------------------------------------------------
# 局面B: 統計取込(設計書 §14-9)
# ---------------------------------------------------------------------------


def _finalized_exam(workspace) -> Exam:
    """統計をまだ持たない、確定済みの試験を 1 つ作る。"""
    view = ImportView(workspace)
    view.docx_edit.setText(str(SAMPLE_DOCX))
    view.stats_edit.setText(str(SAMPLE_STATS))
    view.load_preview()
    report = view.run_import()

    exam = report.exam
    # 取込直後は imported。統計を与え直す経路を試すため状態だけ戻す。
    exam.status = "finalized"
    workspace.commit()
    return exam


def test_stats_import_shows_derived_n_and_skipped_questions(workspace) -> None:
    """導出した受験者数と飛ばした設問を必ず出す(設計書 §9.2-2、§10.2-(4))。"""
    exam = _finalized_exam(workspace)

    view = StatsImportView(workspace)
    view.exam_box.setCurrentIndex(view.exam_box.findData(exam.id))
    view.csv_edit.setText(str(SAMPLE_STATS))

    assert view.run_validation()
    summary = view.summary_label.text()
    assert "受験者数" in summary
    assert "飛ばした" in summary
    assert view.apply_button.isEnabled()


def test_stats_import_applies_and_lists_flags(workspace) -> None:
    exam = _finalized_exam(workspace)

    view = StatsImportView(workspace)
    view.exam_box.setCurrentIndex(view.exam_box.findData(exam.id))
    view.csv_edit.setText(str(SAMPLE_STATS))
    view.run_validation()

    assert view.run_import()
    assert exam.status == "imported"
    assert "取り込みました" in view.status.text()
    # フラグの付いた問題から改訂へ進める(設計書 §9.3, §2.6)。
    if view.flag_table.rowCount():
        view.flag_table.selectRow(0)
        seen: list[int] = []
        view.reviseRequested.connect(seen.append)
        view._revise_selected()
        assert seen


def test_stats_import_refuses_a_draft_exam(loaded_workspace) -> None:
    """統計を与えられるのは確定済みの試験だけ(設計書 §9.1)。"""
    from itembank.core.exam import create_exam

    draft = create_exam(loaded_workspace.session, name="まだ作りかけ")
    loaded_workspace.commit()

    view = StatsImportView(loaded_workspace)
    view.exam_box.setCurrentIndex(view.exam_box.findData(draft.id))

    assert not view.validate_button.isEnabled()
    assert "確定していません" in view.status.text()
    assert not view.run_validation()


@pytest.mark.parametrize(
    "broken",
    ["broken_missing_column.csv", "broken_wrong_correct.csv", "broken_ratio_not_count.csv"],
)
def test_stats_import_blocks_broken_csv(workspace, broken: str) -> None:
    """わざと壊した CSV は確実にブロックされる(実装計画 M6 の受入条件)。"""
    exam = _finalized_exam(workspace)

    view = StatsImportView(workspace)
    view.exam_box.setCurrentIndex(view.exam_box.findData(exam.id))
    view.csv_edit.setText(str(SAMPLE / broken))

    assert not view.run_validation()
    assert not view.apply_button.isEnabled()
    assert not view.run_import()
    assert exam.status == "finalized", "ブロックされたら状態は変わらない"


def test_stats_import_has_no_authoring_path(workspace) -> None:
    """局面Bでは問題を作れない(設計書 §1.4, §17)。"""
    view = StatsImportView(workspace)
    labels = {
        child.text()
        for child in view.findChildren(type(view.validate_button))
        if hasattr(child, "text")
    }
    assert not any("作" in label and "試験" not in label for label in labels)
