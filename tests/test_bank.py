"""問題バンク操作の統合テスト(設計書 §2、実装計画 §4 M4 受入条件)。

受入条件:

- 新規作成・改訂・派生の 3 経路がそれぞれ正しいレコードを作る
- タイプと正答個数の不整合が保存時にブロックされる
- draft 保存と復帰ができる
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from itembank.core.bank import (
    create_question,
    derivation_family,
    derivation_parent,
    derive_question,
    derived_children,
    linked_set_ids,
    requires_new_version,
    revise_question,
    set_tags,
    tag_names,
    upsert_choice_set,
    validate_draft,
)
from itembank.core.db import Q_DRAFT, ChoiceSet, QuestionVersion
from itembank.core.typing_rules import TYPE_A

HARD_TISSUE = ["エナメル質", "象牙質", "セメント質", "歯髄", "歯根膜"]
STEM_A = "最も硬い組織はどれか。1つ選べ。"


def make_set(session: Session, items: list[str] | None = None) -> ChoiceSet:
    cset, _ = upsert_choice_set(session, items or list(HARD_TISSUE))
    return cset


def make_question(session: Session, **kw):
    cset = kw.pop("choice_set", None) or make_set(session)
    return create_question(
        session,
        stem_html=kw.pop("stem_html", STEM_A),
        choice_set=cset,
        choice_order=kw.pop("choice_order", "12345"),
        correct=kw.pop("correct", "a"),
        **kw,
    )


# ---------------------------------------------------------------------------
# 選択肢セット(設計書 §6)
# ---------------------------------------------------------------------------


def test_upsert_normalizes_items(session: Session) -> None:
    """均等割は保存時に落とす(設計書 §7, §8)。"""
    cset, created = upsert_choice_set(session, ["横　紋", "死　帯", "頰　骨", "導　管", "歯　堤"])
    assert created
    assert cset.item_htmls() == ["横紋", "死帯", "頰骨", "導管", "歯堤"]


def test_upsert_reuses_a_set_that_differs_only_in_order(session: Session) -> None:
    """順序はセットに属さないので、並び違いは別セットにならない(設計書 §6.1)。"""
    first, created1 = upsert_choice_set(session, HARD_TISSUE)
    second, created2 = upsert_choice_set(session, list(reversed(HARD_TISSUE)))
    assert created1 and not created2
    assert first.id == second.id


def test_upsert_rejects_a_malformed_set(session: Session) -> None:
    with pytest.raises(ValueError):
        upsert_choice_set(session, HARD_TISSUE[:4])


def test_near_sets_are_linked_automatically(session: Session) -> None:
    """共通 4 項目は近似として自動リンク(設計書 §6.3)。"""
    a = make_set(session)
    b = make_set(session, [*HARD_TISSUE[:4], "歯肉"])
    session.flush()
    assert linked_set_ids(session, a.id) == {b.id}
    assert linked_set_ids(session, b.id) == {a.id}


def test_distant_sets_are_not_linked(session: Session) -> None:
    a = make_set(session)
    b = make_set(session, ["歯肉", "口蓋", "舌", "唾液腺", "顎骨"])
    session.flush()
    assert linked_set_ids(session, a.id) == set()
    assert linked_set_ids(session, b.id) == set()


# ---------------------------------------------------------------------------
# 経路 1: 新規作成
# ---------------------------------------------------------------------------


def test_create_makes_question_and_version_one(session: Session) -> None:
    result = make_question(session)
    assert not result.blocked
    assert result.question is not None and result.version is not None
    assert result.version.version_no == 1
    assert result.version.correct == "a"
    assert result.question.derived_from is None


def test_create_blocks_on_wrong_correct_count(session: Session) -> None:
    """設計書 §11: A/X2/X3/X4 で個数が不一致なら保存をブロックする。"""
    result = make_question(session, correct="ab")
    assert result.blocked
    assert "correct_count" in {i.code for i in result.issues}
    assert result.version is None
    assert session.query(QuestionVersion).count() == 0


def test_create_blocks_when_the_instruction_is_missing(session: Session) -> None:
    result = make_question(session, stem_html="最も硬い組織はどれか。")
    assert result.blocked
    assert "type_underivable" in {i.code for i in result.issues}


def test_create_warns_but_saves_on_emphasis_slip(session: Session) -> None:
    """強調規則違反は警告でありブロックしない(設計書 §4)。"""
    result = make_question(session, stem_html="硬く<strong>ない</strong>のはどれか。1つ選べ。")
    assert not result.blocked
    result2 = make_question(session, stem_html="硬くないのはどれか。1つ選べ。")
    assert not result2.blocked
    assert "emphasis_missing" in {i.code for i in result2.warnings}


def test_correct_is_normalized_on_save(session: Session) -> None:
    cset = make_set(session)
    result = create_question(
        session,
        stem_html="正しいのはどれか。2つ選べ。",
        choice_set=cset,
        choice_order="12345",
        correct="DA",
    )
    assert result.version is not None and result.version.correct == "ad"


# ---------------------------------------------------------------------------
# draft(設計書 §2.5)
# ---------------------------------------------------------------------------


def test_draft_tolerates_a_provisional_correct(session: Session) -> None:
    """作りかけは正答が暫定でも保持できる。作問の中断・再開のため。"""
    result = make_question(session, correct="ab", status=Q_DRAFT)
    assert not result.blocked
    assert result.version is not None
    assert result.question is not None and result.question.status == Q_DRAFT
    assert result.warnings  # 不整合は警告として残る


def test_draft_can_be_resumed_and_promoted(session: Session) -> None:
    result = make_question(session, correct="ab", status=Q_DRAFT)
    question = result.question
    assert question is not None

    question.status = "active"
    fixed = revise_question(
        session,
        question,
        stem_html=STEM_A,
        choice_set=session.get(ChoiceSet, result.version.choice_set_id),
        choice_order="12345",
        correct="a",
    )
    assert not fixed.blocked
    assert fixed.version is not None and fixed.version.correct == "a"


def test_validate_draft_downgrades_blocking_issues() -> None:
    hard = validate_draft(STEM_A, HARD_TISSUE, "ab")
    assert any(i.blocking for i in hard)
    soft = validate_draft(STEM_A, HARD_TISSUE, "ab", status=Q_DRAFT)
    assert not any(i.blocking for i in soft)


# ---------------------------------------------------------------------------
# 経路 2: 改訂(設計書 §2.2)
# ---------------------------------------------------------------------------


def test_revise_bumps_the_version_on_the_same_question(session: Session) -> None:
    created = make_question(session)
    question = created.question
    assert question is not None

    revised = revise_question(
        session,
        question,
        stem_html=STEM_A,
        choice_set=session.get(ChoiceSet, created.version.choice_set_id),
        choice_order="12345",
        correct="b",  # 正答を直した
    )
    assert revised.created_new_version
    assert revised.version is not None
    assert revised.version.question_id == question.id
    assert revised.version.version_no == 2
    session.refresh(question)
    assert len(question.versions) == 2
    # 旧版はレコードとして残る。旧統計は旧版に紐づいたまま。
    assert question.versions[0].correct == "a"
    assert question.latest_version is not None and question.latest_version.correct == "b"


def test_typo_fix_stays_in_the_same_version(session: Session) -> None:
    """正答・選択肢・並び順・指示文言に影響しない修正のみ同一版内で許可(設計書 §2.2)。"""
    created = make_question(session)
    question = created.question
    assert question is not None

    result = revise_question(
        session,
        question,
        stem_html="最も硬い組織はどれか。1つ選べ。",  # 表記だけ整えた想定
        choice_set=session.get(ChoiceSet, created.version.choice_set_id),
        choice_order="12345",
        correct="a",
    )
    assert not result.created_new_version
    assert result.version is not None and result.version.version_no == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("correct", "b"),
        ("choice_order", "54321"),
        ("stem_html", "最も硬い組織はどれか。2つ選べ。"),
    ],
)
def test_these_changes_always_force_a_new_version(session: Session, field: str, value: str) -> None:
    created = make_question(session)
    old = created.version
    assert old is not None
    kwargs = {
        "choice_set_id": old.choice_set_id,
        "choice_order": old.choice_order,
        "correct": old.correct,
        "stem_html": old.stem_html,
        field: value,
    }
    assert requires_new_version(old, **kwargs)


def test_changing_the_choice_set_forces_a_new_version(session: Session) -> None:
    created = make_question(session)
    other = make_set(session, [*HARD_TISSUE[:4], "歯肉"])
    old = created.version
    assert old is not None
    assert requires_new_version(
        old,
        choice_set_id=other.id,
        choice_order=old.choice_order,
        correct=old.correct,
        stem_html=old.stem_html,
    )


def test_revise_blocks_on_an_inconsistent_correct(session: Session) -> None:
    created = make_question(session)
    question = created.question
    assert question is not None
    result = revise_question(
        session,
        question,
        stem_html=STEM_A,
        choice_set=session.get(ChoiceSet, created.version.choice_set_id),
        choice_order="12345",
        correct="abc",
    )
    assert result.blocked
    session.refresh(question)
    assert len(question.versions) == 1


# ---------------------------------------------------------------------------
# 経路 3: 派生(設計書 §2.2, §2.3)
# ---------------------------------------------------------------------------


def test_derive_creates_a_separate_question(session: Session) -> None:
    created = make_question(session)
    source = created.version
    assert source is not None

    derived = derive_question(
        session,
        source,
        stem_html="最も軟らかい組織はどれか。1つ選べ。",
        choice_set=session.get(ChoiceSet, source.choice_set_id),
        choice_order="12345",
        correct="d",
    )
    assert not derived.blocked
    assert derived.question is not None
    assert derived.question.id != created.question.id
    assert derived.question.derived_from == source.id
    assert derived.version is not None and derived.version.version_no == 1
    # 元の問題も引き続き出題対象。
    session.refresh(created.question)
    assert created.question.status == "active"
    assert len(created.question.versions) == 1


def test_derivation_genealogy_is_visible_both_ways(session: Session) -> None:
    """「この問題は問○○から派生」「この問題から3問が派生」の表示用(設計書 §2.3)。"""
    created = make_question(session)
    source = created.version
    cset = session.get(ChoiceSet, source.choice_set_id)

    for correct in "bcd":
        derive_question(
            session,
            source,
            stem_html=f"問い{correct}はどれか。1つ選べ。",
            choice_set=cset,
            choice_order="12345",
            correct=correct,
        )
    session.refresh(created.question)
    children = derived_children(session, created.question)
    assert len(children) == 3
    assert derivation_parent(session, children[0]) is source


def test_derivation_family_spans_ancestors_and_descendants(session: Session) -> None:
    """同時出題を禁じる範囲。実質同じ問題になるため(設計書 §2.3, §13.3)。"""
    root = make_question(session)
    cset = session.get(ChoiceSet, root.version.choice_set_id)

    child = derive_question(
        session,
        root.version,
        stem_html="問い2はどれか。1つ選べ。",
        choice_set=cset,
        choice_order="12345",
        correct="b",
    )
    grandchild = derive_question(
        session,
        child.version,
        stem_html="問い3はどれか。1つ選べ。",
        choice_set=cset,
        choice_order="12345",
        correct="c",
    )
    ids = {root.question.id, child.question.id, grandchild.question.id}
    for qid in ids:
        assert derivation_family(session, qid) == ids


def test_unrelated_questions_are_not_family(session: Session) -> None:
    a = make_question(session)
    b = make_question(session, stem_html="別の問いはどれか。1つ選べ。")
    assert derivation_family(session, a.question.id) == {a.question.id}
    assert derivation_family(session, b.question.id) == {b.question.id}


def test_derived_question_inherits_tags(session: Session) -> None:
    created = make_question(session)
    set_tags(session, created.question, ["硬組織", "エナメル質"])
    derived = derive_question(
        session,
        created.version,
        stem_html="最も軟らかい組織はどれか。1つ選べ。",
        choice_set=session.get(ChoiceSet, created.version.choice_set_id),
        choice_order="12345",
        correct="d",
    )
    assert tag_names(session, derived.question.id) == ["エナメル質", "硬組織"]


def test_tags_can_be_replaced(session: Session) -> None:
    created = make_question(session)
    set_tags(session, created.question, ["硬組織"])
    set_tags(session, created.question, ["軟組織", "発生"])
    assert tag_names(session, created.question.id) == ["発生", "軟組織"]


def test_derive_from_a_reshuffled_order(session: Session) -> None:
    """並び順だけ変えた派生も、同じセットを指したままでよい(設計書 §6.1)。"""
    created = make_question(session)
    source = created.version
    derived = derive_question(
        session,
        source,
        stem_html=STEM_A,
        choice_set=session.get(ChoiceSet, source.choice_set_id),
        choice_order="54321",
        correct="e",  # 並びが逆なので e が項目1(エナメル質)
    )
    assert not derived.blocked
    assert derived.version.choice_set_id == source.choice_set_id
    assert derived.version.choice_order == "54321"


def test_type_a_question_defaults_to_type_a(session: Session) -> None:
    from itembank.core.typing_rules import derive_item_type

    assert derive_item_type(STEM_A) == TYPE_A
