"""問題バンク一覧・問題編集・問題詳細(設計書 §14-1〜3)。

一覧の絞り込みは ``BankFilter.matches`` に閉じているので、画面を組み立てずに
反例を並べられる。編集ダイアログは**保存経路が設計書 §2.2 の規則どおりか**
(改訂は同じ question_id、派生は新しい question_id と derived_from)を見る。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from qbank_mcq.core.bank import (
    create_question_from_printed,
    latest_versions_using_set,
    unused_correct_item_nos,
    upsert_choice_set,
)
from qbank_mcq.core.db import Question, QuestionVersion
from qbank_mcq.core.exam import build_candidates
from qbank_mcq.core.selection import Candidate
from qbank_mcq.core.typing_rules import set_instruction

pytest.importorskip("PySide6")

from qbank_mcq.ui.bank_view import UNUSED_YEAR, BankFilter, BankTableModel, BankView  # noqa: E402
from qbank_mcq.ui.question_detail import QuestionDetail  # noqa: E402
from qbank_mcq.ui.question_editor import QuestionEditor  # noqa: E402

CHOICES = ["エナメル質", "象牙質", "セメント質", "歯髄", "歯根膜"]


def _candidate(**kwargs) -> Candidate:
    base = dict(
        question_id=1,
        qversion_id=1,
        stem_html="最も硬いのはどれか。1つ選べ。",
        correct="a",
        choice_set_id=1,
    )
    base.update(kwargs)
    return Candidate(**base)


# ---------------------------------------------------------------------------
# 指示文言の差し替え(設計書 §14-2 のドロップダウン)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "item_type", "expected"),
    [
        ("正しいのはどれか。1つ選べ。", "X2", "正しいのはどれか。2つ選べ。"),
        ("正しいのはどれか。すべて選べ。", "A", "正しいのはどれか。1つ選べ。"),
        ("正しいのはどれか。", "A", "正しいのはどれか。1つ選べ。"),
        # 全角数字・全角空白でも差し替え位置を見つける。
        ("正しいのはどれか。２　つ選べ。", "X3", "正しいのはどれか。3つ選べ。"),
        # タグを跨いでいても、指示文言そのものは素のままなので置き換わる。
        (
            "含ま<strong>ない</strong>のはどれか。1つ選べ。",
            "X4",
            "含ま<strong>ない</strong>のはどれか。4つ選べ。",
        ),
    ],
)
def test_set_instruction(stem: str, item_type: str, expected: str) -> None:
    assert set_instruction(stem, item_type) == expected


def test_set_instruction_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        set_instruction("どれか。", "X5")


# ---------------------------------------------------------------------------
# 絞り込み(設計書 §14-1)
# ---------------------------------------------------------------------------


def test_keyword_is_matched_after_stripping_tags() -> None:
    """否定形設問はタグが語中に入る(設計書 §3.2)。生の HTML を検索してはいけない。"""
    c = _candidate(stem_html="酸に溶け<strong>ない</strong>のはどれか。1つ選べ。")
    assert BankFilter(keyword="溶けない").matches(c)
    assert not BankFilter(keyword="ないのは、ない").matches(c)


def test_type_and_negative_filters() -> None:
    negative = _candidate(stem_html="含ま<strong>ない</strong>のはどれか。2つ選べ。")
    positive = _candidate(stem_html="正しいのはどれか。1つ選べ。")

    assert BankFilter(item_type="X2").matches(negative)
    assert not BankFilter(item_type="X2").matches(positive)
    assert BankFilter(negative=True).matches(negative)
    assert not BankFilter(negative=True).matches(positive)
    assert BankFilter(negative=False).matches(positive)


def test_stat_filters_drop_items_without_stats() -> None:
    """統計の無い問題を「正答率 60% 以上」に混ぜない。条件を満たした証拠がない。"""
    with_stats = _candidate(p=0.7, disc=0.3)
    without = _candidate(p=None, disc=None)

    assert BankFilter(p_min=0.6).matches(with_stats)
    assert not BankFilter(p_min=0.6).matches(without)
    assert not BankFilter(min_disc=0.2).matches(without)
    assert not BankFilter(p_max=0.5).matches(with_stats)


def test_draft_and_retired_are_opt_in() -> None:
    draft = _candidate(status="draft")
    retired = _candidate(status="retired")

    assert BankFilter().matches(draft), "draft は既定で見える(中断・再開できることが要件)"
    assert not BankFilter(include_draft=False).matches(draft)
    assert not BankFilter().matches(retired)
    assert BankFilter(include_retired=True).matches(retired)


def test_year_tag_flag_and_set_filters() -> None:
    used = _candidate(
        last_exam_year=2025, tags=frozenset({"発生"}), flags=frozenset({"overselect"})
    )
    unused = _candidate(last_exam_year=None, choice_set_id=9)

    assert BankFilter(last_exam_year=2025).matches(used)
    assert not BankFilter(last_exam_year=2025).matches(unused)
    assert BankFilter(last_exam_year=UNUSED_YEAR).matches(unused)
    assert not BankFilter(last_exam_year=UNUSED_YEAR).matches(used)
    assert BankFilter(tag="発生").matches(used)
    assert not BankFilter(tag="発生").matches(unused)
    assert BankFilter(flag="overselect").matches(used)
    assert BankFilter(choice_set_id=9).matches(unused)
    assert not BankFilter(choice_set_id=9).matches(used)


def test_table_model_shows_p_with_type(qapp) -> None:
    """正答率はタイプと併記する(設計書 §12)。"""
    model = BankTableModel([_candidate(p=0.5)])
    headers = [name for name, _ in BankTableModel.COLUMNS]
    column = headers.index("正答率")
    assert model.data(model.index(0, column)) == "50%(A)"


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------


def _seed(session: Session) -> Question:
    result, _ = create_question_from_printed(
        session,
        stem_html="最も硬い組織はどれか。1つ選べ。",
        printed_choices=CHOICES,
        correct="a",
        tags=["発生"],
    )
    return result.question


def test_bank_view_lists_and_filters(workspace) -> None:
    _seed(workspace.session)
    create_question_from_printed(
        workspace.session,
        stem_html="含ま<strong>ない</strong>のはどれか。2つ選べ。",
        printed_choices=CHOICES,
        correct="bc",
    )
    workspace.commit()

    view = BankView(workspace)
    assert view.model.rowCount() == 2
    assert view.proxy.rowCount() == 2

    view.keyword.setText("最も硬い")
    assert view.proxy.rowCount() == 1
    assert view.selected_candidate() is None

    view.keyword.setText("")
    view.type_box.setCurrentIndex(view.type_box.findData("X2"))
    assert [c.item_type for c in view.visible_candidates()] == ["X2"]
    assert "1 / 2 問" in view.count_label.text()


def test_bank_view_select_question(workspace) -> None:
    question = _seed(workspace.session)
    workspace.commit()

    view = BankView(workspace)
    assert view.select_question(question.id)
    assert view.selected_candidate().question_id == question.id
    assert not view.select_question(9999)


def test_editor_creates_a_question(workspace) -> None:
    editor = QuestionEditor(workspace)
    editor.stem.set_fragment_html("最も硬い組織はどれか。1つ選べ。")
    editor._set_choices(CHOICES)
    editor.correct_boxes[0].setChecked(True)
    editor.tags.setText("発生、硬組織")
    editor.save()

    question = editor.saved_question
    assert question is not None
    version = question.latest_version
    assert version.version_no == 1
    assert version.correct == "a"
    assert build_candidates(workspace.session)[0].tags == frozenset({"発生", "硬組織"})


def test_editor_blocks_a_correct_count_mismatch(workspace) -> None:
    """タイプと正答個数の不整合は保存前に止まる(実装計画 M4 の受入条件)。"""
    editor = QuestionEditor(workspace)
    editor.stem.set_fragment_html("正しいのはどれか。2つ選べ。")
    editor._set_choices(CHOICES)
    editor.correct_boxes[0].setChecked(True)

    assert any(i.blocking for i in editor.current_issues())
    assert not editor.save_button.isEnabled()

    editor.correct_boxes[1].setChecked(True)
    assert not any(i.blocking for i in editor.current_issues())
    assert editor.save_button.isEnabled()


def test_draft_keeps_a_provisional_correct(workspace) -> None:
    """draft は正答が暫定でも保持される(設計書 §2.5)。"""
    editor = QuestionEditor(workspace)
    editor.stem.set_fragment_html("正しいのはどれか。2つ選べ。")
    editor._set_choices(CHOICES)
    editor.correct_boxes[0].setChecked(True)
    editor.draft_check.setChecked(True)

    assert not any(i.blocking for i in editor.current_issues())
    editor.save()
    assert editor.saved_question.status == "draft"


def test_editor_revision_keeps_the_question_id(workspace, monkeypatch) -> None:
    question = _seed(workspace.session)
    workspace.commit()

    editor = QuestionEditor(workspace, question_id=question.id)
    monkeypatch.setattr(editor, "_confirm_revision", lambda: True)
    editor.mode_box.setCurrentIndex(editor.mode_box.findData("revise"))
    editor.correct_boxes[0].setChecked(False)
    editor.correct_boxes[1].setChecked(True)
    editor.save()

    saved = editor.saved_question
    assert saved.id == question.id
    assert saved.latest_version.version_no == 2, "正答が変われば必ず新版(設計書 §2.2)"
    assert saved.latest_version.correct == "b"


def test_editor_typo_fix_stays_in_the_same_version(workspace, monkeypatch) -> None:
    question = _seed(workspace.session)
    workspace.commit()

    editor = QuestionEditor(workspace, question_id=question.id)
    assert not editor.creates_new_version()

    monkeypatch.setattr(editor, "_confirm_revision", lambda: True)
    editor.mode_box.setCurrentIndex(editor.mode_box.findData("revise"))
    editor.stem.set_fragment_html("最も硬い組織はどれか。1つ選べ。 ")
    editor.save()

    assert editor.saved_question.latest_version.version_no == 1


def test_editor_derivation_makes_a_new_question(workspace) -> None:
    question = _seed(workspace.session)
    workspace.commit()
    source_version_id = question.latest_version.id

    editor = QuestionEditor(workspace, question_id=question.id)
    editor.stem.set_fragment_html("最も軟らかい組織はどれか。1つ選べ。")
    editor.correct_boxes[0].setChecked(False)
    editor.correct_boxes[3].setChecked(True)
    editor.save()

    derived = editor.saved_question
    assert derived.id != question.id
    assert derived.derived_from == source_version_id
    # 元の問題は残る(設計書 §2.2)。
    assert workspace.session.get(Question, question.id).status == "active"


def test_duplicate_entry_derives_without_touching_the_source(workspace) -> None:
    question = _seed(workspace.session)
    workspace.commit()

    editor = QuestionEditor(workspace, derive_from_question_id=question.id)
    assert not editor.mode_box.isVisible(), "複製作成は必ず派生。選ばせない"
    editor.stem.set_fragment_html("最も脆いのはどれか。1つ選べ。")
    editor.save()

    assert editor.saved_question.id != question.id
    assert editor.saved_question.derived_from == question.latest_version.id


def test_reordering_choices_reuses_the_same_set(workspace) -> None:
    """並び替えはセットを増やさない(設計書 §6.1)。違いは choice_order にだけ出る。"""
    question = _seed(workspace.session)
    workspace.commit()
    before = workspace.session.query(QuestionVersion).count()

    editor = QuestionEditor(workspace, derive_from_question_id=question.id)
    editor._swap(0, 1)
    editor.stem.set_fragment_html("2 番目に硬いのはどれか。1つ選べ。")
    editor.save()

    from qbank_mcq.core.db import ChoiceSet

    assert workspace.session.query(ChoiceSet).count() == 1
    assert workspace.session.query(QuestionVersion).count() == before + 1
    assert editor.saved_question.latest_version.choice_order != question.latest_version.choice_order


def test_editor_from_a_set_shows_unused_correct_items(workspace) -> None:
    """セットからの作問支援(設計書 §2.4)。"""
    _seed(workspace.session)
    workspace.commit()
    cset, _ = upsert_choice_set(workspace.session, CHOICES)

    editor = QuestionEditor(workspace, choice_set_id=cset.id)
    assert [e.fragment_html() for e in editor.choice_edits] == CHOICES
    assert "このセットを使う設問 1 件" in editor.set_status.text()
    assert "まだ正答に使っていない項目" in editor.set_status.text()
    assert "エナメル質" not in editor.set_status.text().split("まだ正答に使っていない項目")[1]


def test_unused_correct_items_tracks_the_choice_order(workspace) -> None:
    """正答は印字記号で保存されている。並び順を通して項目番号に戻す。"""
    session = workspace.session
    cset, _ = upsert_choice_set(session, CHOICES)
    create_question_from_printed(
        session,
        stem_html="最も硬い組織はどれか。1つ選べ。",
        printed_choices=list(reversed(CHOICES)),  # 印字順は逆
        correct="e",  # 逆順の e = エナメル質 = 項目 1
    )
    assert unused_correct_item_nos(session, cset) == [2, 3, 4, 5]
    assert len(latest_versions_using_set(session, cset.id)) == 1


def test_question_detail_with_stats_draws_every_chart(loaded_workspace) -> None:
    """設計書 §14-3 の 4 つの図が、実績のある問題で描ける。"""
    candidates = build_candidates(loaded_workspace.session)
    with_stats = [c for c in candidates if c.p is not None]
    assert with_stats, "サンプルは統計つきで取り込まれているはず"

    detail = QuestionDetail(loaded_workspace, with_stats[0].question_id)
    assert detail.appearance_table.rowCount() == 1
    appearance = detail.history.appearances[0]
    assert appearance.has_stats
    assert appearance.top_wrong(), "誤答パターン上位が取れる(設計書 §14-3)"
    assert sum(appearance.partial().values()) == appearance.n

    # 図が実際に描かれている(軸が立つ)。
    assert detail.mark_chart.figure.axes
    assert detail.wrong_chart.figure.axes
    assert detail.partial_chart.figure.axes
    assert detail.trend_chart.figure.axes


def test_question_detail_opens_without_stats(workspace) -> None:
    question = _seed(workspace.session)
    workspace.commit()

    detail = QuestionDetail(workspace, question.id)
    assert detail.history.question_id == question.id
    assert detail.appearance_table.rowCount() == 0

    seen: list[int] = []
    detail.reviseRequested.connect(seen.append)
    detail._request_revision()
    assert seen == [question.id]
