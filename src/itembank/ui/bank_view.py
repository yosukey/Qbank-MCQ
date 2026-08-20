"""問題バンク一覧(設計書 §14-1)。

    フィルタ付き一覧(キーワード[タグ除去後に検索]/タグ/タイプ/否定形の別/
    正答率レンジ/識別係数/フラグ/最終出題年/使用セット/draft)。
    ここから**新規作成・複製作成**を起動

絞り込みの判定は ``BankFilter.matches`` に閉じている。``core.selection.Candidate``
だけを見る純関数なので、画面を出さずに反例を並べて確かめられる。

**キーワードはタグ除去後に当てる**(設計書 §3.2)。否定形設問は
``酸に溶け<strong>ない</strong>`` のようにタグが語中に入るため、生の HTML を
検索すると「溶けない」で引けない。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.exam import build_candidates
from ..core.selection import Candidate
from ..core.stats import FLAG_NO_STATS
from ..core.typing_rules import ITEM_TYPES
from .common import number, p_with_type, plain

#: 「未出題」を表す最終出題年の値。
UNUSED_YEAR = -1

#: ルートを指す不正な ``QModelIndex``。Qt の仮想関数の既定値として使う。
#: 毎回 ``QModelIndex()`` を作ると引数既定での関数呼び出しになるため、1 つ持ち回す。
NO_PARENT = QModelIndex()


@dataclass
class BankFilter:
    """一覧の絞り込み条件(設計書 §14-1)。すべて省略可。"""

    keyword: str = ""
    tag: str | None = None
    item_type: str | None = None
    #: True=否定形のみ、False=肯定形のみ、None=問わない
    negative: bool | None = None
    p_min: float | None = None
    p_max: float | None = None
    min_disc: float | None = None
    flag: str | None = None
    #: 出題年。``UNUSED_YEAR`` で「未出題のみ」。
    last_exam_year: int | None = None
    choice_set_id: int | None = None
    #: draft を一覧に含めるか(設計書 §2.5)。既定は含める(復帰できることが要件)。
    include_draft: bool = True
    include_retired: bool = False

    def matches(self, c: Candidate) -> bool:
        if not self.include_draft and c.status == "draft":
            return False
        if not self.include_retired and c.status == "retired":
            return False
        if self.keyword and self.keyword not in plain(c.stem_html):
            return False
        if self.tag and self.tag not in c.tags:
            return False
        if self.item_type and c.item_type != self.item_type:
            return False
        if self.negative is not None and c.negative != self.negative:
            return False
        if self.flag and self.flag not in c.flags:
            return False
        if self.choice_set_id is not None and c.choice_set_id != self.choice_set_id:
            return False
        if self.last_exam_year is not None:
            if self.last_exam_year == UNUSED_YEAR:
                if c.last_exam_year is not None:
                    return False
            elif c.last_exam_year != self.last_exam_year:
                return False
        # 統計の無い問題(新作)は正答率・識別係数で絞ると消える。**消してよい。**
        # 「正答率 60% 以上」で新作が出てきたら、それは条件を満たした証拠ではない。
        if self.p_min is not None and (c.p is None or c.p < self.p_min):
            return False
        if self.p_max is not None and (c.p is None or c.p > self.p_max):
            return False
        if self.min_disc is not None and (c.disc is None or c.disc < self.min_disc):
            return False
        return True


class BankTableModel(QAbstractTableModel):
    """``Candidate`` の一覧。"""

    COLUMNS = (
        ("ID", lambda c: c.question_id),
        ("設問", lambda c: plain(c.stem_html)),
        ("タイプ", lambda c: c.item_type or "?"),
        ("否定", lambda c: "否定" if c.negative else ""),
        ("正答", lambda c: c.correct),
        ("正答率", lambda c: p_with_type(c.p, c.item_type)),
        ("識別係数", lambda c: number(c.disc)),
        ("タグ", lambda c: "、".join(sorted(c.tags))),
        ("最終出題", lambda c: c.last_exam_year or "—"),
        ("出題回数", lambda c: c.times_used),
        ("セット", lambda c: c.choice_set_id),
        ("状態", lambda c: c.status),
        ("フラグ", lambda c: "、".join(sorted(c.flags))),
    )

    def __init__(self, candidates: list[Candidate] | None = None) -> None:
        super().__init__()
        self._rows: list[Candidate] = list(candidates or [])

    def set_candidates(self, candidates: list[Candidate]) -> None:
        self.beginResetModel()
        self._rows = list(candidates)
        self.endResetModel()

    def candidate_at(self, row: int) -> Candidate | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    # -- QAbstractTableModel ------------------------------------------------
    def rowCount(self, parent: QModelIndex = NO_PARENT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = NO_PARENT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        candidate = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return str(self.COLUMNS[index.column()][1](candidate))
        if role == Qt.ItemDataRole.UserRole:
            return candidate
        if role == Qt.ItemDataRole.ToolTipRole:
            return plain(candidate.stem_html)
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section][0]
        return None


class BankFilterProxy(QSortFilterProxyModel):
    """``BankFilter`` をそのまま当てるだけのプロキシ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.filter = BankFilter()

    def set_filter(self, bank_filter: BankFilter) -> None:
        self.filter = bank_filter
        # invalidateFilter() 系は Qt 6.11 で非推奨。公開 API の invalidate() を使う。
        self.invalidate()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        candidate = model.candidate_at(row) if isinstance(model, BankTableModel) else None
        return candidate is None or self.filter.matches(candidate)


class BankView(QWidget):
    """問題バンクのタブ。"""

    #: 問題を編集したい(question_id)。詳細・編集は MainWindow がつなぐ。
    editRequested = Signal(int)
    detailRequested = Signal(int)
    #: 新規作成(白紙 / 複製元の question_id)。
    createRequested = Signal()
    duplicateRequested = Signal(int)
    #: 一覧が読み直された。
    reloaded = Signal()

    def __init__(self, workspace, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.model = BankTableModel()
        self.proxy = BankFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_filters())

        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 設問の列に余りを全部渡す。他の列は中身に合わせる。既定幅のままだと
        # 横幅を使い切ってしまい、肝心の設問文が数文字しか見えない。
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _: self._emit_for_selection(self.detailRequested))
        layout.addWidget(self.table, 1)

        layout.addLayout(self._build_buttons())
        self.refresh()

    # -- 組み立て -----------------------------------------------------------
    def _build_filters(self) -> QGroupBox:
        box = QGroupBox("絞り込み", self)
        grid = QGridLayout(box)

        self.keyword = QLineEdit(box)
        self.keyword.setPlaceholderText("キーワード(タグ除去後の本文を検索)")
        self.keyword.textChanged.connect(self.apply_filter)
        grid.addWidget(self.keyword, 0, 0, 1, 3)

        self.tag_box = QComboBox(box)
        self.tag_box.currentIndexChanged.connect(self.apply_filter)
        grid.addWidget(QLabel("タグ", box), 0, 3)
        grid.addWidget(self.tag_box, 0, 4)

        self.type_box = QComboBox(box)
        self.type_box.addItem("すべて", None)
        for item_type in ITEM_TYPES:
            self.type_box.addItem(item_type, item_type)
        self.type_box.currentIndexChanged.connect(self.apply_filter)
        grid.addWidget(QLabel("タイプ", box), 0, 5)
        grid.addWidget(self.type_box, 0, 6)

        self.negative_box = QComboBox(box)
        self.negative_box.addItem("否定形を問わない", None)
        self.negative_box.addItem("否定形のみ", True)
        self.negative_box.addItem("肯定形のみ", False)
        self.negative_box.currentIndexChanged.connect(self.apply_filter)
        grid.addWidget(self.negative_box, 1, 0)

        self.p_min = self._rate_spin(box, "正答率下限")
        self.p_max = self._rate_spin(box, "正答率上限", value=100.0)
        grid.addWidget(QLabel("正答率(%)", box), 1, 1)
        grid.addWidget(self.p_min, 1, 2)
        grid.addWidget(self.p_max, 1, 3)

        self.min_disc = QDoubleSpinBox(box)
        self.min_disc.setRange(-1.0, 1.0)
        self.min_disc.setSingleStep(0.05)
        self.min_disc.setDecimals(2)
        self.min_disc.setSpecialValueText("下限なし")
        self.min_disc.setMinimum(-1.01)
        self.min_disc.setValue(-1.01)
        self.min_disc.valueChanged.connect(self.apply_filter)
        grid.addWidget(QLabel("識別係数 ≧", box), 1, 4)
        grid.addWidget(self.min_disc, 1, 5)

        self.flag_box = QComboBox(box)
        self.flag_box.currentIndexChanged.connect(self.apply_filter)
        grid.addWidget(QLabel("フラグ", box), 1, 6)
        grid.addWidget(self.flag_box, 1, 7)

        self.year_box = QComboBox(box)
        self.year_box.currentIndexChanged.connect(self.apply_filter)
        grid.addWidget(QLabel("最終出題", box), 2, 0)
        grid.addWidget(self.year_box, 2, 1)

        self.set_box = QComboBox(box)
        self.set_box.currentIndexChanged.connect(self.apply_filter)
        grid.addWidget(QLabel("使用セット", box), 2, 2)
        grid.addWidget(self.set_box, 2, 3)

        self.draft_check = QCheckBox("draft を含める", box)
        self.draft_check.setChecked(True)
        self.draft_check.stateChanged.connect(self.apply_filter)
        grid.addWidget(self.draft_check, 2, 4)

        self.retired_check = QCheckBox("退役を含める", box)
        self.retired_check.stateChanged.connect(self.apply_filter)
        grid.addWidget(self.retired_check, 2, 5)

        self.count_label = QLabel("", box)
        grid.addWidget(self.count_label, 2, 6, 1, 2)
        return box

    def _rate_spin(self, parent: QWidget, tip: str, *, value: float = 0.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(0.0, 100.0)
        spin.setDecimals(0)
        spin.setValue(value)
        spin.setToolTip(tip)
        spin.valueChanged.connect(self.apply_filter)
        return spin

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.new_button = QPushButton("新規作成", self)
        self.new_button.clicked.connect(self.createRequested)
        self.duplicate_button = QPushButton("複製作成(派生)", self)
        self.duplicate_button.clicked.connect(
            lambda: self._emit_for_selection(self.duplicateRequested)
        )
        self.edit_button = QPushButton("編集", self)
        self.edit_button.clicked.connect(lambda: self._emit_for_selection(self.editRequested))
        self.detail_button = QPushButton("詳細", self)
        self.detail_button.clicked.connect(lambda: self._emit_for_selection(self.detailRequested))

        row.addWidget(self.new_button)
        row.addWidget(self.duplicate_button)
        row.addWidget(self.edit_button)
        row.addWidget(self.detail_button)
        row.addStretch(1)
        return row

    # -- 動作 ---------------------------------------------------------------
    def refresh(self) -> None:
        """DB から候補を読み直し、絞り込みの選択肢も作り直す。"""
        candidates = build_candidates(self.workspace.session)
        self.model.set_candidates(candidates)
        self._reload_choices(candidates)
        self.apply_filter()
        self.reloaded.emit()

    def _reload_choices(self, candidates: list[Candidate]) -> None:
        def refill(box: QComboBox, first: tuple[str, object], values: list[tuple[str, object]]):
            previous = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem(*first)
            for label, value in values:
                box.addItem(label, value)
            index = box.findData(previous)
            box.setCurrentIndex(index if index >= 0 else 0)
            box.blockSignals(False)

        tags = sorted({t for c in candidates for t in c.tags})
        refill(self.tag_box, ("すべて", None), [(t, t) for t in tags])

        flags = sorted({f for c in candidates for f in c.flags})
        refill(self.flag_box, ("すべて", None), [(f, f) for f in flags])

        years = sorted({c.last_exam_year for c in candidates if c.last_exam_year}, reverse=True)
        refill(
            self.year_box,
            ("すべて", None),
            [("未出題", UNUSED_YEAR)] + [(str(y), y) for y in years],
        )

        sets = sorted({c.choice_set_id for c in candidates})
        refill(self.set_box, ("すべて", None), [(f"セット {s}", s) for s in sets])

    def current_filter(self) -> BankFilter:
        p_min = self.p_min.value() / 100 if self.p_min.value() > 0 else None
        p_max = self.p_max.value() / 100 if self.p_max.value() < 100 else None
        disc = self.min_disc.value() if self.min_disc.value() > -1.005 else None
        return BankFilter(
            keyword=self.keyword.text().strip(),
            tag=self.tag_box.currentData(),
            item_type=self.type_box.currentData(),
            negative=self.negative_box.currentData(),
            p_min=p_min,
            p_max=p_max,
            min_disc=disc,
            flag=self.flag_box.currentData(),
            last_exam_year=self.year_box.currentData(),
            choice_set_id=self.set_box.currentData(),
            include_draft=self.draft_check.isChecked(),
            include_retired=self.retired_check.isChecked(),
        )

    def apply_filter(self) -> None:
        self.proxy.set_filter(self.current_filter())
        shown, total = self.proxy.rowCount(), self.model.rowCount()
        new_items = sum(1 for c in self.visible_candidates() if FLAG_NO_STATS in c.flags)
        self.count_label.setText(f"{shown} / {total} 問(うち統計なし {new_items} 問)")

    def visible_candidates(self) -> list[Candidate]:
        return [
            self.model.candidate_at(self.proxy.mapToSource(self.proxy.index(row, 0)).row())
            for row in range(self.proxy.rowCount())
        ]

    def selected_candidate(self) -> Candidate | None:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            return None
        return self.model.candidate_at(self.proxy.mapToSource(indexes[0]).row())

    def select_question(self, question_id: int) -> bool:
        """一覧から特定の問題を選ぶ(フラグ一覧などからの導線)。"""
        for row in range(self.proxy.rowCount()):
            source = self.proxy.mapToSource(self.proxy.index(row, 0))
            candidate = self.model.candidate_at(source.row())
            if candidate and candidate.question_id == question_id:
                self.table.selectRow(row)
                return True
        return False

    def _emit_for_selection(self, signal: Signal) -> None:
        candidate = self.selected_candidate()
        if candidate is not None:
            signal.emit(candidate.question_id)
