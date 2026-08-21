"""SQLAlchemy モデルとセッション。設計書 §8 のスキーマをそのまま写したもの。

設計書 §8 の末尾にあるとおり、**プレーン版の列・``item_type`` 列・``n_select`` 列・
否定形フラグ列はいずれも持たない**(すべて導出)。導出は ``core.typing_rules`` と
``core.stats`` が行う。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

# -- 状態値 ------------------------------------------------------------------

#: questions.status
Q_ACTIVE, Q_DRAFT, Q_RETIRED = "active", "draft", "retired"
QUESTION_STATUSES = (Q_ACTIVE, Q_DRAFT, Q_RETIRED)

#: exams.status。draft → finalized → imported と一方向に進む(設計書 §8, §9.2)。
E_DRAFT, E_FINALIZED, E_IMPORTED = "draft", "finalized", "imported"
EXAM_STATUSES = (E_DRAFT, E_FINALIZED, E_IMPORTED)


def utcnow_iso() -> str:
    """``created_at`` などに入れる ISO8601 文字列(秒精度、UTC)。"""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Base(DeclarativeBase):
    pass


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, default=Q_ACTIVE)
    #: 派生元の **qversion_id**(question_id ではない)。設計書 §2.3 / §8。
    #: questions ↔ question_versions が相互参照になるため FK 制約は張らない
    #: (SQLite は ALTER TABLE ADD CONSTRAINT を持たず、循環 DDL を解けないため)。
    derived_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, default=utcnow_iso)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    versions: Mapped[list[QuestionVersion]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionVersion.version_no",
    )

    @property
    def latest_version(self) -> QuestionVersion | None:
        """最新版。改訂すると出題対象はこれだけになる(設計書 §2.2)。

        ``version_no`` の最大で選ぶ。``versions`` の ``order_by`` は DB から読んだ
        ときにしか効かず、同一セッション内で追加した版には並び順が保証されないため。
        """
        if not self.versions:
            return None
        return max(self.versions, key=lambda v: v.version_no)


class ChoiceSet(Base):
    __tablename__ = "choice_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: HTML5 項目のソート連結ハッシュ(設計書 §6.2)。
    signature: Mapped[str] = mapped_column(Text, unique=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, default=utcnow_iso)

    items: Mapped[list[ChoiceSetItem]] = relationship(
        back_populates="choice_set", cascade="all, delete-orphan", order_by="ChoiceSetItem.item_no"
    )

    def items_by_no(self) -> dict[int, str]:
        return {i.item_no: i.text_html for i in self.items}

    def item_htmls(self) -> list[str]:
        return [i.text_html for i in self.items]


class ChoiceSetItem(Base):
    __tablename__ = "choice_set_items"

    choice_set_id: Mapped[int] = mapped_column(
        ForeignKey("choice_sets.id", ondelete="CASCADE"), primary_key=True
    )
    #: 1〜5。**順序ではなく安定 ID**(設計書 §6.1)。
    item_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 均等割の空白は含まない(設計書 §7、§8)。
    text_html: Mapped[str] = mapped_column(Text, nullable=False)
    #: 通常 NULL。均等割の例外用(設計書 §7)。
    render_override: Mapped[str | None] = mapped_column(Text, nullable=True)

    choice_set: Mapped[ChoiceSet] = relationship(back_populates="items")

    __table_args__ = (Index("idx_csi_text", "text_html"),)


class ChoiceSetLink(Base):
    """近似セットの自動リンク(設計書 §6.3)。``set_a < set_b`` に正規化して入れる。"""

    __tablename__ = "choice_set_links"

    set_a: Mapped[int] = mapped_column(ForeignKey("choice_sets.id"), primary_key=True)
    set_b: Mapped[int] = mapped_column(ForeignKey("choice_sets.id"), primary_key=True)
    shared: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relation: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionVersion(Base):
    __tablename__ = "question_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    choice_set_id: Mapped[int] = mapped_column(ForeignKey("choice_sets.id"), nullable=False)
    #: 例 '31524' = a←項目3, b←項目1, …(設計書 §8)
    choice_order: Mapped[str] = mapped_column(Text, nullable=False)
    #: 否定形かは <strong> の有無で判定する。専用列は持たない(設計書 §4)。
    stem_html: Mapped[str] = mapped_column(Text, nullable=False)
    correct: Mapped[str] = mapped_column(Text, nullable=False)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, default=utcnow_iso)

    question: Mapped[Question] = relationship(back_populates="versions")
    choice_set: Mapped[ChoiceSet] = relationship()

    __table_args__ = (
        UniqueConstraint("question_id", "version_no"),
        CheckConstraint("length(correct) BETWEEN 1 AND 5", name="ck_qv_correct_len"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id"), nullable=True)


class QuestionTag(Base):
    __tablename__ = "question_tags"

    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    exam_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    course: Mapped[str | None] = mapped_column(Text, nullable=True)
    cohort: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_examinees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default=E_DRAFT)

    items: Mapped[list[ExamItem]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="ExamItem.position"
    )


class ExamItem(Base):
    """finalize 時点で「どの版を何番として出したか」を確定させる恒久記録(設計書 §1.2)。"""

    __tablename__ = "exam_items"

    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    qversion_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"), nullable=False)
    correct_asked: Mapped[str] = mapped_column(Text, nullable=False)

    exam: Mapped[Exam] = relationship(back_populates="items")
    version: Mapped[QuestionVersion] = relationship()


class ItemPatternCount(Base):
    """設問別のマークパターン度数。無回答は ``pattern=''``(設計書 §8)。"""

    __tablename__ = "item_pattern_counts"

    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    qversion_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"), primary_key=True)
    pattern: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("idx_ipc", "exam_id", "qversion_id"),)


class ItemStatRow(Base):
    """``item_stats``。度数から導出した値の書き出し先(設計書 §8, §12)。"""

    __tablename__ = "item_stats"

    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    qversion_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"), primary_key=True)
    n: Mapped[int | None] = mapped_column(Integer)
    n_correct: Mapped[int | None] = mapped_column(Integer)
    p: Mapped[float | None] = mapped_column(Float)
    disc: Mapped[float | None] = mapped_column(Float)
    disc_type: Mapped[str | None] = mapped_column(Text)
    sel_a: Mapped[float | None] = mapped_column(Float)
    sel_b: Mapped[float | None] = mapped_column(Float)
    sel_c: Mapped[float | None] = mapped_column(Float)
    sel_d: Mapped[float | None] = mapped_column(Float)
    sel_e: Mapped[float | None] = mapped_column(Float)
    blank_rate: Mapped[float | None] = mapped_column(Float)
    overselect_rate: Mapped[float | None] = mapped_column(Float)
    top_wrong_pattern: Mapped[str | None] = mapped_column(Text)
    top_wrong_count: Mapped[int | None] = mapped_column(Integer)
    flags: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)


class ExamStat(Base):
    __tablename__ = "exam_stats"

    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    n: Mapped[int | None] = mapped_column(Integer)
    mean: Mapped[float | None] = mapped_column(Float)
    sd: Mapped[float | None] = mapped_column(Float)
    median: Mapped[float | None] = mapped_column(Float)


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# エンジンとセッション
# ---------------------------------------------------------------------------


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """SQLite の外部キー制約は既定で無効なので明示的に有効化する。"""
    try:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    except Exception:  # pragma: no cover - SQLite 以外では無視
        pass


def make_engine(path: Path | str | None = None, *, echo: bool = False) -> Engine:
    """SQLite エンジンを作る。``path=None`` または ``':memory:'`` でインメモリ。"""
    if path is None or str(path) == ":memory:":
        return create_engine("sqlite+pysqlite:///:memory:", echo=echo, future=True)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite+pysqlite:///{p}", echo=echo, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
