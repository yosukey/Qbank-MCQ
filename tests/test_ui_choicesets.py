"""選択肢セット・選択肢アイテム画面(設計書 §14-4, §14-5)。

統合とマトリクスは順序の扱いを間違えやすい。セットは順序を持たない集合なので
(設計書 §6.1)、**印字の見え方を変えずに項目番号だけ張り替えられる**ことを
統合の要件として押さえる。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from itembank.core.bank import (
    add_link,
    create_question_from_printed,
    linked_set_ids,
    merge_choice_sets,
    remove_link,
    upsert_choice_set,
)
from itembank.core.choiceset import ordered_items
from itembank.core.db import ChoiceSet, QuestionVersion
from itembank.core.reporting import choice_set_summaries, set_item_matrix

pytest.importorskip("PySide6")

from itembank.ui.choiceset_view import ChoiceSetView  # noqa: E402
from itembank.ui.item_view import ItemView, verdict  # noqa: E402

PLAIN = ["エナメル質", "象牙質", "セメント質", "歯髄", "歯根膜"]
#: 同じ用語だがマークアップだけ違う(監査が拾う形。設計書 §6.2)。
MARKED = ["エナメル質", "<i>象牙質</i>", "セメント質", "歯髄", "歯根膜"]


def _printed(session: Session, choices: list[str], correct: str, stem: str) -> QuestionVersion:
    result, _ = create_question_from_printed(
        session, stem_html=stem, printed_choices=choices, correct=correct
    )
    return result.version


# ---------------------------------------------------------------------------
# 統合(設計書 §6.3 の「統合を提案」)
# ---------------------------------------------------------------------------


def test_merge_keeps_the_printed_view_and_the_correct_answer(session: Session) -> None:
    version = _printed(session, MARKED, "b", "斜体で登録された問題。1つ選べ。")
    _printed(session, PLAIN, "a", "素で登録された問題。1つ選べ。")

    source = session.get(ChoiceSet, version.choice_set_id)
    target = next(
        session.get(ChoiceSet, s.set_id)
        for s in choice_set_summaries(session)
        if s.set_id != source.id
    )
    printed_before = [
        html for _, _, html in ordered_items(source.items_by_no(), version.choice_order)
    ]

    assert merge_choice_sets(session, source, target) == 1

    assert session.get(ChoiceSet, source.id) is None
    assert version.choice_set_id == target.id
    assert version.correct == "b", "正答は印字記号。統合で変わってはいけない"
    printed_after = [
        html for _, _, html in ordered_items(target.items_by_no(), version.choice_order)
    ]
    # マークアップは統合先のものになるが、並びと語は変わらない。
    assert [t.replace("<i>", "").replace("</i>", "") for t in printed_before] == [
        t.replace("<i>", "").replace("</i>", "") for t in printed_after
    ]


def test_merge_preserves_the_printed_order_of_a_shuffled_question(session: Session) -> None:
    shuffled = list(reversed(MARKED))
    version = _printed(session, shuffled, "a", "並びを変えた問題。1つ選べ。")
    _printed(session, PLAIN, "a", "素の問題。1つ選べ。")

    source = session.get(ChoiceSet, version.choice_set_id)
    target = next(
        session.get(ChoiceSet, s.set_id)
        for s in choice_set_summaries(session)
        if s.set_id != source.id
    )
    merge_choice_sets(session, source, target)

    printed = [html for _, _, html in ordered_items(target.items_by_no(), version.choice_order)]
    assert printed[0] == "歯根膜", "逆順で登録した問題の印字順は逆順のまま"


def test_merge_refuses_when_items_do_not_pair_up(session: Session) -> None:
    v = _printed(session, PLAIN, "a", "問題A。1つ選べ。")
    _printed(session, ["歯肉", "骨", "血管", "神経", "上皮"], "a", "問題B。1つ選べ。")

    source = session.get(ChoiceSet, v.choice_set_id)
    target = next(
        session.get(ChoiceSet, s.set_id)
        for s in choice_set_summaries(session)
        if s.set_id != source.id
    )
    with pytest.raises(ValueError):
        merge_choice_sets(session, source, target)


# ---------------------------------------------------------------------------
# リンクの手動追加・解除(設計書 §6.3)
# ---------------------------------------------------------------------------


def test_manual_link_and_unlink(session: Session) -> None:
    a, _ = upsert_choice_set(session, PLAIN)
    b, _ = upsert_choice_set(session, ["歯肉", "骨", "血管", "神経", "上皮"])

    assert linked_set_ids(session, a.id) == set(), "共通 0 なら自動リンクされない"
    add_link(session, b.id, a.id, note="手で足した")
    assert linked_set_ids(session, a.id) == {b.id}

    link = choice_set_summaries(session)[0].links[0]
    assert link.note == "手で足した"
    assert link.relation == "手動"

    assert remove_link(session, a.id, b.id)
    assert linked_set_ids(session, a.id) == set()
    assert not remove_link(session, a.id, b.id)


def test_link_to_itself_is_rejected(session: Session) -> None:
    a, _ = upsert_choice_set(session, PLAIN)
    with pytest.raises(ValueError):
        add_link(session, a.id, a.id)


# ---------------------------------------------------------------------------
# マトリクス(設計書 §14-4)
# ---------------------------------------------------------------------------


def test_matrix_is_keyed_by_item_number_not_by_label(loaded_workspace) -> None:
    """順序が変わっても項目単位で追える(設計書 §6.4-5)。"""
    session = loaded_workspace.session
    summaries = choice_set_summaries(session)
    used = [s for s in summaries if s.n_questions]
    assert used

    rows = set_item_matrix(session, used[0].set_id)
    assert rows
    for row in rows:
        assert set(row.rates) == {1, 2, 3, 4, 5}
        assert row.correct_item_nos
        # 統計を取り込んだ試験に出ている行はマーク率を持つ。
        if row.exam_id is not None:
            assert all(value is not None for value in row.rates.values())


def test_choiceset_view_shows_items_links_and_matrix(loaded_workspace) -> None:
    view = ChoiceSetView(loaded_workspace)
    assert view.set_table.rowCount() == len(view.summaries)
    assert view.selected_set_id() == view.summaries[0].set_id
    assert view.items_label.text()
    # 4 つの固定列 + 項目 5 列。
    assert view.matrix_table.columnCount() == 9


def test_choiceset_view_merges_from_the_screen(workspace) -> None:
    session = workspace.session
    _printed(session, MARKED, "b", "斜体で登録。1つ選べ。")
    _printed(session, PLAIN, "a", "素で登録。1つ選べ。")
    workspace.commit()

    view = ChoiceSetView(workspace)
    ids = [s.set_id for s in view.summaries]
    assert len(ids) == 2

    assert view.merge_into(ids[0], ids[1])
    assert [s.set_id for s in view.summaries] == [ids[1]]
    assert "統合しました" in view.status.text()


def test_choiceset_view_reports_a_failed_merge(workspace) -> None:
    session = workspace.session
    _printed(session, PLAIN, "a", "問題A。1つ選べ。")
    _printed(session, ["歯肉", "骨", "血管", "神経", "上皮"], "a", "問題B。1つ選べ。")
    workspace.commit()

    view = ChoiceSetView(workspace)
    ids = [s.set_id for s in view.summaries]
    assert not view.merge_into(ids[0], ids[1])
    assert "統合できません" in view.status.text()
    assert session.get(ChoiceSet, ids[0]) is not None


def test_choiceset_view_audit_finds_markup_differences(workspace) -> None:
    """タグ除去一致の監査(設計書 §6.2, §17)。"""
    session = workspace.session
    _printed(session, MARKED, "b", "斜体で登録。1つ選べ。")
    _printed(session, PLAIN, "a", "素で登録。1つ選べ。")
    workspace.commit()

    view = ChoiceSetView(workspace)
    duplicates = view.run_audit()
    assert "象牙質" in duplicates
    assert "疑い 1 件" in view.status.text()


def test_choiceset_view_create_from_set_signal(workspace) -> None:
    _printed(workspace.session, PLAIN, "a", "問題A。1つ選べ。")
    workspace.commit()

    view = ChoiceSetView(workspace)
    seen: list[int] = []
    view.createFromSetRequested.connect(seen.append)
    view._create_from_set()
    assert seen == [view.summaries[0].set_id]


# ---------------------------------------------------------------------------
# 選択肢アイテム(設計書 §14-5, §6.5)
# ---------------------------------------------------------------------------


def test_item_view_lists_terms_with_performance(loaded_workspace) -> None:
    view = ItemView(loaded_workspace)
    assert view.rows, "統計を取り込んだサンプルでは用語の実績が出る"
    assert view.table.rowCount() == len(view.rows)

    header = [view.table.horizontalHeaderItem(i).text() for i in range(view.table.columnCount())]
    assert header[0] == "用語"
    assert "最も混同される相手" in header


def test_item_view_filters(loaded_workspace) -> None:
    view = ItemView(loaded_workspace)
    term = view.rows[0].text_html

    view.keyword.setText(term[:3])
    assert all(term[:3] in row.text_html for row in view.visible_rows())

    view.keyword.setText("")
    view.only_distractors.setChecked(True)
    assert all(row.as_distractor > 0 for row in view.visible_rows())


def test_item_view_without_stats(workspace) -> None:
    view = ItemView(workspace)
    assert view.rows == []
    assert "統計を取り込んだ試験がまだありません" in view.status.text()


@pytest.mark.parametrize(
    ("as_distractor", "median", "expected"),
    [
        (0, None, "錯乱肢としての実績なし"),
        (3, 0.01, "死んだ選択肢"),
        (3, 0.31, "強力な錯乱肢"),
        (3, 0.12, "ふつう"),
    ],
)
def test_verdict(as_distractor: int, median: float | None, expected: str) -> None:
    from itembank.core.stats import ItemPerformance

    performance = ItemPerformance(
        text_html="レッチウス条",
        appearances=8,
        as_correct=3,
        as_distractor=as_distractor,
        median_p_when_correct=0.52,
        median_mark_rate_when_distractor=median,
        top_confused_with="エブネル線",
        top_confused_count=5,
        co_occurrences=6,
    )
    assert verdict(performance, dead_rate=0.05) == expected
