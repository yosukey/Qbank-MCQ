"""問題バンクの操作。新規作成・改訂・派生の 3 経路(設計書 §2)。

この層は GUI に依存しない。設計書 §2.2 の「改訂」と「派生」の区別をここで担保する
ので、画面からも CLI からも同じ規則が効く。

===== ==================================== ====================================
       改訂(新版)                          派生(新問題)
===== ==================================== ====================================
意味   この問題を直す。旧版は使わない        元の問題は残したまま、別の問題を作る
ID     同じ question_id、version_no +1       新しい question_id(derived_from に元版)
統計   旧統計は旧版に残る                    元問題の統計は元問題のもの
===== ==================================== ====================================

さらに §2.2 の但し書きが効く: **正答・選択肢・並び順・指示文言のいずれかが変わる
編集は、改訂を選んでも必ず新版になる**(同一版内での上書きは禁止)。設問本体の
誤字修正など、これらに影響しない修正のみ同一版内で許可する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import choiceset as cs
from .db import (
    Q_ACTIVE,
    Q_DRAFT,
    ChoiceSet,
    ChoiceSetItem,
    ChoiceSetLink,
    Question,
    QuestionTag,
    QuestionVersion,
    Tag,
    utcnow_iso,
)
from .text import normalize_choice, normalize_stem, strip_tags
from .typing_rules import (
    ValidationIssue,
    check_emphasis_rule,
    derive_item_type_detail,
    normalize_correct,
    validate_correct,
)

log = logging.getLogger(__name__)

#: 保存経路。設計書 §2.2 では既定を「派生」とする。
MODE_REVISE = "revise"
MODE_DERIVE = "derive"


@dataclass
class SaveResult:
    """保存の結果。``issues`` のうち ``blocking`` のものがあれば保存していない。"""

    version: QuestionVersion | None = None
    question: Question | None = None
    created_new_version: bool = False
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(i.blocking for i in self.issues)

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if not i.blocking]


# ---------------------------------------------------------------------------
# 選択肢セット
# ---------------------------------------------------------------------------


def upsert_choice_set(
    session: Session, items: list[str], *, name: str | None = None, note: str | None = None
) -> tuple[ChoiceSet, bool]:
    """選択肢セットを署名で照合し、無ければ作る。``(セット, 新規作成したか)``。

    項目は ``normalize_choice`` を通す(NFKC + 均等割除去)。並び順はセットに属さない
    ため、既存セットと**並びだけが違う**場合は既存セットが返る(設計書 §6.1, §6.2)。
    """
    normalized = [normalize_choice(i) for i in items]
    problems = cs.validate_items(normalized)
    if problems:
        raise ValueError("; ".join(problems))

    signature = cs.choice_set_signature(normalized)
    existing = session.scalar(select(ChoiceSet).where(ChoiceSet.signature == signature))
    if existing is not None:
        return existing, False

    obj = ChoiceSet(name=name, signature=signature, note=note, created_at=utcnow_iso())
    obj.items = [ChoiceSetItem(item_no=i + 1, text_html=html) for i, html in enumerate(normalized)]
    session.add(obj)
    session.flush()
    refresh_links_for(session, obj)
    return obj, True


def refresh_links_for(
    session: Session, target: ChoiceSet, *, min_shared: int = 3
) -> list[ChoiceSetLink]:
    """``target`` と他セットの近似リンクを張り直す(設計書 §6.3)。

    **推移的に閉じない。** target を含む組だけを見る。
    """
    others = session.scalars(select(ChoiceSet).where(ChoiceSet.id != target.id)).all()
    target_items = set(target.item_htmls())

    created: list[ChoiceSetLink] = []
    for other in others:
        shared = cs.set_similarity(target_items, other.item_htmls())
        if not cs.should_autolink(shared) or shared < min_shared:
            continue
        a, b = sorted((target.id, other.id))
        existing = session.get(ChoiceSetLink, (a, b))
        if existing is not None:
            existing.shared = shared
            existing.relation = cs.relation_for(shared)
            continue
        link = ChoiceSetLink(set_a=a, set_b=b, shared=shared, relation=cs.relation_for(shared))
        session.add(link)
        created.append(link)
    session.flush()
    return created


def rebuild_all_links(session: Session, *, min_shared: int = 3) -> int:
    """全セットの近似リンクを張り直す。設定画面で閾値を変えたときに呼ぶ(設計書 §14-10)。

    閾値を上げても古いリンクが残ると、露出管理(§6.4-1)が実際より厳しく効き続ける。
    そこで**いったん全消ししてから**張り直す。
    """
    session.query(ChoiceSetLink).delete()
    session.flush()
    sets = session.scalars(select(ChoiceSet)).all()
    for target in sets:
        refresh_links_for(session, target, min_shared=min_shared)
    return session.query(ChoiceSetLink).count()


def linked_set_ids(session: Session, set_id: int) -> set[int]:
    """``set_id`` に自動リンクされたセット ID(露出管理で使う。設計書 §6.4-1)。"""
    rows = session.execute(
        select(ChoiceSetLink.set_a, ChoiceSetLink.set_b).where(
            (ChoiceSetLink.set_a == set_id) | (ChoiceSetLink.set_b == set_id)
        )
    ).all()
    return {a if b == set_id else b for a, b in rows}


# ---------------------------------------------------------------------------
# 保存前の検証
# ---------------------------------------------------------------------------


def validate_draft(
    stem_html: str, choice_htmls: list[str], correct: str, *, status: str = Q_ACTIVE
) -> list[ValidationIssue]:
    """保存前の検証。タイプ導出 → 正答個数 → 強調規則の順に見る。

    ``status='draft'`` のときは正答が暫定でもよいので個数不整合をブロックしない
    (設計書 §2.5: 作りかけの問題は正答が暫定でも保持される)。
    """
    issues: list[ValidationIssue] = []
    derivation = derive_item_type_detail(stem_html)
    if not derivation.ok:
        issues.append(
            ValidationIssue("type_underivable", derivation.reason or "タイプを導出できません")
        )
    issues.extend(validate_correct(correct, derivation.item_type))
    issues.extend(check_emphasis_rule(stem_html, choice_htmls))

    if status == Q_DRAFT:
        # draft は中断・再開できることが重要なので、警告に落として保持を許す。
        for issue in issues:
            issue.blocking = False
    return issues


def requires_new_version(
    old: QuestionVersion, *, choice_set_id: int, choice_order: str, correct: str, stem_html: str
) -> bool:
    """同一版内での上書きが禁じられる編集か(設計書 §2.2 但し書き)。

    正答・選択肢セット・並び順・指示文言のいずれかが変われば ``True``。
    設問本体の誤字修正など、これらに影響しない修正だけが ``False`` になる。
    """
    if old.choice_set_id != choice_set_id:
        return True
    if old.choice_order != choice_order:
        return True
    if normalize_correct(old.correct) != normalize_correct(correct):
        return True
    return derive_item_type(old.stem_html) != derive_item_type(stem_html)


def derive_item_type(stem_html: str) -> str | None:
    """``typing_rules.derive_item_type`` の再輸出(この層から使いやすくするため)。"""
    return derive_item_type_detail(stem_html).item_type


# ---------------------------------------------------------------------------
# 3 つの保存経路(設計書 §2.1, §2.2)
# ---------------------------------------------------------------------------


def create_question(
    session: Session,
    *,
    stem_html: str,
    choice_set: ChoiceSet,
    choice_order: str,
    correct: str,
    status: str = Q_ACTIVE,
    tags: list[str] | None = None,
    note: str | None = None,
    derived_from: int | None = None,
    image_path: str | None = None,
) -> SaveResult:
    """白紙から / セットから 1 問作る(設計書 §2.1 の入口 1・2)。"""
    stem = normalize_stem(stem_html)
    correct_n = normalize_correct(correct)
    cs.parse_choice_order(choice_order)

    issues = validate_draft(stem, choice_set.item_htmls(), correct_n, status=status)
    if any(i.blocking for i in issues):
        return SaveResult(issues=issues)

    question = Question(
        status=status, derived_from=derived_from, created_at=utcnow_iso(), note=note
    )
    session.add(question)
    session.flush()

    version = QuestionVersion(
        version_no=1,
        choice_set_id=choice_set.id,
        choice_order=choice_order,
        stem_html=stem,
        correct=correct_n,
        image_path=image_path,
        created_at=utcnow_iso(),
    )
    # リレーション経由で足す。``session.add`` だと ``question.versions`` が
    # 読み込み済みのとき古いままになり、``latest_version`` が旧版を返す。
    question.versions.append(version)
    session.flush()

    if tags:
        set_tags(session, question, tags)
    return SaveResult(version=version, question=question, created_new_version=True, issues=issues)


def create_question_from_printed(
    session: Session,
    *,
    stem_html: str,
    printed_choices: list[str],
    correct: str,
    status: str = Q_ACTIVE,
    tags: list[str] | None = None,
    note: str | None = None,
    image_path: str | None = None,
) -> tuple[SaveResult, bool]:
    """**印字順**の選択肢から 1 問作る。docx 取込と「セットから作る」導線の共通経路。

    セットが既にあれば再利用し、印字順を ``choice_order`` として表す。順序シャッフルが
    別セットとして重複登録されないのはこの経路のおかげ(設計書 §6.1)。

    ``(保存結果, セットを新規作成したか)`` を返す。
    """
    choice_set, order, created = resolve_printed(session, printed_choices)
    result = create_question(
        session,
        stem_html=stem_html,
        choice_set=choice_set,
        choice_order=order,
        correct=correct,
        status=status,
        tags=tags,
        note=note,
        image_path=image_path,
    )
    return result, created


def resolve_printed(session: Session, printed_choices: list[str]) -> tuple[ChoiceSet, str, bool]:
    """印字順の選択肢を ``(セット, choice_order, セットを新規作成したか)`` に解く。

    セットは順序を持たない集合なので(設計書 §6.1)、並び替えただけの選択肢は
    同じセットに解決され、違いは ``choice_order`` にだけ現れる。**画面もこの経路を
    通す**。通さないと、並び替えのたびに新しいセットが増える。
    """
    normalized = [normalize_choice(c) for c in printed_choices]
    choice_set, created = upsert_choice_set(session, normalized)
    order = cs.resolve_choice_order(choice_set.items_by_no(), normalized)
    return choice_set, order, created


def revise_question_from_printed(
    session: Session,
    question: Question,
    *,
    stem_html: str,
    printed_choices: list[str],
    correct: str,
    image_path: str | None = None,
    allow_inplace: bool = True,
) -> tuple[SaveResult, bool]:
    """印字順の選択肢で改訂する(``revise_question`` の印字順版)。"""
    choice_set, order, created = resolve_printed(session, printed_choices)
    result = revise_question(
        session,
        question,
        stem_html=stem_html,
        choice_set=choice_set,
        choice_order=order,
        correct=correct,
        image_path=image_path,
        allow_inplace=allow_inplace,
    )
    return result, created


def derive_question_from_printed(
    session: Session,
    source: QuestionVersion,
    *,
    stem_html: str,
    printed_choices: list[str],
    correct: str,
    status: str = Q_ACTIVE,
    note: str | None = None,
    image_path: str | None = None,
    inherit_tags: bool = True,
) -> tuple[SaveResult, bool]:
    """印字順の選択肢で派生を作る(``derive_question`` の印字順版)。"""
    choice_set, order, created = resolve_printed(session, printed_choices)
    result = derive_question(
        session,
        source,
        stem_html=stem_html,
        choice_set=choice_set,
        choice_order=order,
        correct=correct,
        status=status,
        note=note,
        image_path=image_path,
        inherit_tags=inherit_tags,
    )
    return result, created


def latest_versions_using_set(session: Session, choice_set_id: int) -> list[QuestionVersion]:
    """このセットを使っている問題の**最新版**(設計書 §2.4 の「セット内の既存設問」)。

    旧版も混ぜると「同じ問い方が既にある」の判断を誤る。
    """
    rows = session.scalars(
        select(QuestionVersion).where(QuestionVersion.choice_set_id == choice_set_id)
    ).all()
    latest: dict[int, QuestionVersion] = {}
    for version in rows:
        current = latest.get(version.question_id)
        if current is None or version.version_no > current.version_no:
            latest[version.question_id] = version
    return [latest[qid] for qid in sorted(latest)]


def unused_correct_item_nos(session: Session, choice_set: ChoiceSet) -> list[int]:
    """まだ正答として使われていない項目番号(設計書 §2.4)。

        セット内の各項目について「まだ正答として使われていない項目」を提示すると、
        未着手の問い方が一目で分かる

    正答は印字記号で保存されているので、版ごとの ``choice_order`` を通して
    項目番号に戻してから数える。
    """
    used: set[int] = set()
    for version in latest_versions_using_set(session, choice_set.id):
        used.update(cs.correct_to_item_nos(version.correct, version.choice_order))
    return [item.item_no for item in choice_set.items if item.item_no not in used]


def find_duplicate_question(
    session: Session, stem_html: str, choice_set_id: int
) -> Question | None:
    """同じセットで設問文も同じ問題を探す(設計書 §1.4 の二重登録防止)。

    比較はタグ除去後に行う(設計書 §3.2)。
    """
    target = strip_tags(normalize_stem(stem_html))
    versions = session.scalars(
        select(QuestionVersion).where(QuestionVersion.choice_set_id == choice_set_id)
    ).all()
    for version in versions:
        if strip_tags(version.stem_html) == target:
            return session.get(Question, version.question_id)
    return None


def revise_question(
    session: Session,
    question: Question,
    *,
    stem_html: str,
    choice_set: ChoiceSet,
    choice_order: str,
    correct: str,
    image_path: str | None = None,
    allow_inplace: bool = True,
) -> SaveResult:
    """改訂 = 同じ ``question_id`` で ``version_no`` を 1 つ進める(設計書 §2.2)。

    旧統計は旧版に残り、新版は実績ゼロから始まる。以後の出題対象は新版のみ。

    正答・選択肢・並び順・指示文言のいずれも変わらない修正(設問本体の誤字直しなど)
    は、``allow_inplace=True`` なら同一版を書き換える。
    """
    latest = question.latest_version
    if latest is None:
        raise ValueError("版のない問題は改訂できません")

    stem = normalize_stem(stem_html)
    correct_n = normalize_correct(correct)
    cs.parse_choice_order(choice_order)

    issues = validate_draft(stem, choice_set.item_htmls(), correct_n, status=question.status)
    if any(i.blocking for i in issues):
        return SaveResult(issues=issues)

    needs_new = requires_new_version(
        latest,
        choice_set_id=choice_set.id,
        choice_order=choice_order,
        correct=correct_n,
        stem_html=stem,
    )

    if not needs_new and allow_inplace:
        latest.stem_html = stem
        if image_path is not None:
            latest.image_path = image_path
        session.flush()
        return SaveResult(
            version=latest, question=question, created_new_version=False, issues=issues
        )

    version = QuestionVersion(
        version_no=latest.version_no + 1,
        choice_set_id=choice_set.id,
        choice_order=choice_order,
        stem_html=stem,
        correct=correct_n,
        image_path=image_path if image_path is not None else latest.image_path,
        created_at=utcnow_iso(),
    )
    question.versions.append(version)
    session.flush()
    return SaveResult(version=version, question=question, created_new_version=True, issues=issues)


def derive_question(
    session: Session,
    source: QuestionVersion,
    *,
    stem_html: str,
    choice_set: ChoiceSet,
    choice_order: str,
    correct: str,
    status: str = Q_ACTIVE,
    note: str | None = None,
    image_path: str | None = None,
    inherit_tags: bool = True,
) -> SaveResult:
    """派生 = 新しい ``question_id`` を作り ``derived_from`` に元版を記録する。

    **元の問題も引き続き出題対象**であり、統計はそれぞれのものとして分かれる
    (設計書 §2.2)。``derived_from`` は露出管理と系譜表示に効く(§2.3)。
    """
    tags = [t for t in tag_names(session, source.question_id)] if inherit_tags else None
    return create_question(
        session,
        stem_html=stem_html,
        choice_set=choice_set,
        choice_order=choice_order,
        correct=correct,
        status=status,
        tags=tags,
        note=note,
        derived_from=source.id,
        image_path=image_path,
    )


def retire_question(session: Session, question: Question) -> None:
    """以後の出題対象から外す。統計は残る。"""
    question.status = "retired"
    session.flush()


# ---------------------------------------------------------------------------
# 派生系譜(設計書 §2.3)
# ---------------------------------------------------------------------------


def derivation_parent(session: Session, question: Question) -> QuestionVersion | None:
    """この問題の派生元の版。"""
    if question.derived_from is None:
        return None
    return session.get(QuestionVersion, question.derived_from)


def derived_children(session: Session, question: Question) -> list[Question]:
    """この問題のいずれかの版から派生した問題(「この問題から3問が派生」の表示用)。"""
    version_ids = [v.id for v in question.versions]
    if not version_ids:
        return []
    return list(
        session.scalars(select(Question).where(Question.derived_from.in_(version_ids))).all()
    )


def derivation_family(session: Session, question_id: int) -> set[int]:
    """同時出題を禁じるべき問題 ID の集合(設計書 §2.3, §13.3)。

    自分自身・祖先・子孫をすべて含む。**実質同じ問題**になるため同じ試験に入れない。
    """
    family: set[int] = set()

    def walk_up(qid: int) -> None:
        while qid is not None and qid not in family:
            family.add(qid)
            q = session.get(Question, qid)
            if q is None or q.derived_from is None:
                return
            parent_version = session.get(QuestionVersion, q.derived_from)
            if parent_version is None:
                return
            qid = parent_version.question_id

    def walk_down(qid: int) -> None:
        q = session.get(Question, qid)
        if q is None:
            return
        for child in derived_children(session, q):
            if child.id not in family:
                family.add(child.id)
                walk_down(child.id)

    walk_up(question_id)
    for qid in list(family):
        walk_down(qid)
    return family


# ---------------------------------------------------------------------------
# タグ
# ---------------------------------------------------------------------------


def ensure_tag(session: Session, name: str, *, parent_id: int | None = None) -> Tag:
    tag = session.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        tag = Tag(name=name, parent_id=parent_id)
        session.add(tag)
        session.flush()
    return tag


def set_tags(session: Session, question: Question, names: list[str]) -> None:
    session.query(QuestionTag).filter(QuestionTag.question_id == question.id).delete()
    for name in dict.fromkeys(names):
        tag = ensure_tag(session, name)
        session.add(QuestionTag(question_id=question.id, tag_id=tag.id))
    session.flush()


def tag_names(session: Session, question_id: int) -> list[str]:
    rows = session.execute(
        select(Tag.name)
        .join(QuestionTag, QuestionTag.tag_id == Tag.id)
        .where(QuestionTag.question_id == question_id)
    ).all()
    return sorted(r[0] for r in rows)


def tag_usage(session: Session) -> list[tuple[Tag, int]]:
    """``(タグ, 付いている問題数)`` の一覧。設定画面のタグ管理が使う(設計書 §14-10)。"""
    counts = dict(
        session.execute(select(QuestionTag.tag_id, func.count()).group_by(QuestionTag.tag_id)).all()
    )
    tags = session.scalars(select(Tag).order_by(Tag.name)).all()
    return [(t, int(counts.get(t.id, 0))) for t in tags]


def rename_tag(session: Session, tag: Tag, new_name: str) -> Tag:
    """タグの名前を変える。**同名のタグがあれば統合する**(付け替えてから消す)。"""
    name = new_name.strip()
    if not name:
        raise ValueError("タグ名が空です")
    if name == tag.name:
        return tag

    existing = session.scalar(select(Tag).where(Tag.name == name))
    if existing is None:
        tag.name = name
        session.flush()
        return tag

    # 統合。両方に付いている問題で主キーが衝突しないよう、先に重複を除く。
    already = {
        r[0]
        for r in session.execute(
            select(QuestionTag.question_id).where(QuestionTag.tag_id == existing.id)
        ).all()
    }
    for link in session.scalars(select(QuestionTag).where(QuestionTag.tag_id == tag.id)).all():
        if link.question_id in already:
            session.delete(link)
        else:
            link.tag_id = existing.id
    session.flush()
    delete_tag(session, tag)
    return existing


def delete_tag(session: Session, tag: Tag) -> None:
    """タグを消す。問題との結び付きも消える(問題自体は残る)。"""
    session.query(QuestionTag).filter(QuestionTag.tag_id == tag.id).delete()
    session.query(Tag).filter(Tag.parent_id == tag.id).update({Tag.parent_id: None})
    session.delete(tag)
    session.flush()
