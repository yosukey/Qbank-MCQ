"""問題編集(設計書 §14-2)。

    設問本体・指示文言(ドロップダウン5種)・選択肢セット選択・並び順指定・
    正答(チェックボックス5個)・タグ。書式ボタンは強調/イタリック/上付き/下付きの
    4つのみ。**保存時に「改訂」か「派生」かを選択**(§2.2)。強調規則違反は
    その場で警告。均等割の印字プレビューを併記

3 つの入口(設計書 §2.1)をこの 1 つのダイアログで受ける:

===================== ==================================================
新規作成               ``QuestionEditor(workspace)``
選択肢セットから作る   ``QuestionEditor(workspace, choice_set_id=…)``
既存問題をベースに     ``QuestionEditor(workspace, question_id=…)``
===================== ==================================================

選択肢は**印字順(a〜e)で編集する**。セットは順序を持たない集合なので
(設計書 §6.1)、並び替えても同じセットに解決され、違いは ``choice_order`` に
だけ現れる。この解決は ``core.bank.resolve_printed`` が行い、画面は素通しする。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.bank import (
    MODE_DERIVE,
    MODE_REVISE,
    create_question_from_printed,
    derive_question_from_printed,
    latest_versions_using_set,
    requires_new_version,
    resolve_printed,
    revise_question_from_printed,
    set_tags,
    tag_names,
    unused_correct_item_nos,
    validate_draft,
)
from ..core.choiceset import ordered_items
from ..core.db import Q_ACTIVE, Q_DRAFT, ChoiceSet, Question
from ..core.text import normalize_choice, normalize_stem, render_choice
from ..core.typing_rules import (
    INSTRUCTION_CHOICES,
    ITEM_TYPES,
    LABELS,
    REQUIRED_COUNT,
    derive_item_type_detail,
    normalize_correct,
    set_instruction,
)
from .common import fill_issue_list, plain
from .richtext import FormatToolBar, RichTextEdit

log = logging.getLogger(__name__)


class QuestionEditor(QDialog):
    """1 問を作る・直すダイアログ。"""

    def __init__(
        self,
        workspace,
        *,
        question_id: int | None = None,
        choice_set_id: int | None = None,
        derive_from_question_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.session = workspace.session

        self.question: Question | None = (
            self.session.get(Question, question_id) if question_id else None
        )
        #: 「複製作成」で開いたとき。編集対象ではなく**派生元**として扱う。
        self.derive_source: Question | None = (
            self.session.get(Question, derive_from_question_id) if derive_from_question_id else None
        )
        self.saved_question: Question | None = None

        self.setWindowTitle(self._title())
        self.resize(760, 720)
        self._build()
        self._load(choice_set_id)
        self._revalidate()

    def _title(self) -> str:
        if self.question is not None:
            return f"問題 {self.question.id} を編集"
        if self.derive_source is not None:
            return f"問題 {self.derive_source.id} から複製作成(派生)"
        return "新規作成"

    # -- 組み立て -----------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.toolbar = FormatToolBar(self)
        layout.addWidget(self.toolbar)

        layout.addWidget(self._build_stem())
        layout.addWidget(self._build_choices())
        layout.addWidget(self._build_meta())
        layout.addWidget(self._build_validation())
        layout.addWidget(self._build_buttons())

    def _build_stem(self) -> QGroupBox:
        box = QGroupBox("設問本体", self)
        layout = QVBoxLayout(box)

        self.stem = RichTextEdit(box)
        self.stem.setMinimumHeight(80)
        self.stem.htmlChanged.connect(self._revalidate)
        self.toolbar.attach(self.stem)
        layout.addWidget(self.stem)

        row = QHBoxLayout()
        row.addWidget(QLabel("指示文言", box))
        self.instruction = QComboBox(box)
        for item_type in ITEM_TYPES:
            self.instruction.addItem(f"{INSTRUCTION_CHOICES[item_type]}({item_type})", item_type)
        self.instruction.setToolTip(
            "設問文の末尾の指示文言を差し替える。タイプはこの文言から導出する"
            "(設計書 §11、item_type 列は持たない)。"
        )
        apply_button = QPushButton("設問文に反映", box)
        apply_button.clicked.connect(self._apply_instruction)
        row.addWidget(self.instruction)
        row.addWidget(apply_button)

        self.type_label = QLabel("", box)
        row.addWidget(self.type_label, 1)
        layout.addLayout(row)
        return box

    def _build_choices(self) -> QGroupBox:
        box = QGroupBox("選択肢(印字順)と正答", self)
        layout = QVBoxLayout(box)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("既存セットから読み込む", box))
        self.set_box = QComboBox(box)
        self.set_box.setMinimumWidth(320)
        source_row.addWidget(self.set_box, 1)
        load_button = QPushButton("読み込む", box)
        load_button.clicked.connect(self._load_selected_set)
        source_row.addWidget(load_button)
        layout.addLayout(source_row)

        self.choice_edits: list[RichTextEdit] = []
        self.correct_boxes: list[QCheckBox] = []
        for i, label in enumerate(LABELS):
            row = QHBoxLayout()
            row.addWidget(QLabel(label, box))

            edit = RichTextEdit(box)
            edit.setFixedHeight(34)
            edit.htmlChanged.connect(self._revalidate)
            self.toolbar.attach(edit)
            row.addWidget(edit, 1)

            check = QCheckBox("正答", box)
            check.stateChanged.connect(self._revalidate)
            row.addWidget(check)

            up = QPushButton("↑", box)
            up.setFixedWidth(30)
            up.clicked.connect(lambda _=False, index=i: self._swap(index, index - 1))
            down = QPushButton("↓", box)
            down.setFixedWidth(30)
            down.clicked.connect(lambda _=False, index=i: self._swap(index, index + 1))
            row.addWidget(up)
            row.addWidget(down)

            self.choice_edits.append(edit)
            self.correct_boxes.append(check)
            layout.addLayout(row)

        self.preview = QLabel("", box)
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setWordWrap(True)
        self.preview.setToolTip("均等割は保存形に空白を持たず、冊子にだけ復元される(設計書 §7)")
        layout.addWidget(self.preview)

        self.set_status = QLabel("", box)
        self.set_status.setWordWrap(True)
        layout.addWidget(self.set_status)
        return box

    def _build_meta(self) -> QGroupBox:
        box = QGroupBox("メタ情報と保存方法", self)
        form = QFormLayout(box)

        self.tags = QLineEdit(box)
        self.tags.setPlaceholderText("発生、エナメル質(読点区切り)")
        form.addRow("タグ", self.tags)

        self.draft_check = QCheckBox("下書き(draft)として保存する", box)
        self.draft_check.setToolTip(
            "作りかけでも保存できる。draft は出題セットに入れられない(設計書 §2.5, §13.3)。"
        )
        self.draft_check.stateChanged.connect(self._revalidate)
        form.addRow("状態", self.draft_check)

        self.mode_box = QComboBox(box)
        # 設計書 §2.2: **既定は「派生」**。取り違えると統計が壊れるか問題を失う。
        self.mode_box.addItem("派生(新問題として保存。元の問題も残る)", MODE_DERIVE)
        self.mode_box.addItem("改訂(新版。旧版は今後出題されなくなる)", MODE_REVISE)
        self.mode_box.currentIndexChanged.connect(self._revalidate)
        self.mode_row_label = QLabel("保存方法", box)
        form.addRow(self.mode_row_label, self.mode_box)
        return box

    def _build_validation(self) -> QGroupBox:
        box = QGroupBox("検証", self)
        layout = QVBoxLayout(box)
        self.issues = QListWidget(box)
        self.issues.setMaximumHeight(110)
        layout.addWidget(self.issues)
        return box

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        return buttons

    # -- 読み込み -----------------------------------------------------------
    def _load(self, choice_set_id: int | None) -> None:
        self._reload_set_box()

        base = self.question or self.derive_source
        if base is not None:
            version = base.latest_version
            if version is not None:
                self.stem.set_fragment_html(version.stem_html)
                cset = self.session.get(ChoiceSet, version.choice_set_id)
                printed = ordered_items(cset.items_by_no(), version.choice_order)
                self._set_choices([html for _, _, html in printed])
                self._set_correct(version.correct)
            self.tags.setText("、".join(tag_names(self.session, base.id)))
            self.draft_check.setChecked(base.status == Q_DRAFT)
        elif choice_set_id is not None:
            cset = self.session.get(ChoiceSet, choice_set_id)
            if cset is not None:
                self._set_choices(cset.item_htmls())
                self._show_set_hints(cset)

        # 保存方法を選ばせるのは「既存問題を編集して保存するとき」だけ(設計書 §2.2)。
        editing = self.question is not None
        self.mode_box.setVisible(editing)
        self.mode_row_label.setVisible(editing)

    def _reload_set_box(self) -> None:
        self.set_box.clear()
        self.set_box.addItem("(選ばない)", None)
        for cset in self.session.query(ChoiceSet).order_by(ChoiceSet.id).all():
            preview = "、".join(plain(h) for h in cset.item_htmls())
            label = f"#{cset.id} {cset.name or preview}"
            self.set_box.addItem(label[:80], cset.id)

    def _load_selected_set(self) -> None:
        set_id = self.set_box.currentData()
        if set_id is None:
            return
        cset = self.session.get(ChoiceSet, set_id)
        if cset is None:
            return
        self._set_choices(cset.item_htmls())
        self._show_set_hints(cset)
        self._revalidate()

    def _show_set_hints(self, cset: ChoiceSet) -> None:
        """セットからの作問を助ける表示(設計書 §2.4)。

        セット内の既存設問と正答、まだ正答に使われていない項目を並べる。
        """
        versions = latest_versions_using_set(self.session, cset.id)
        lines = [f"このセットを使う設問 {len(versions)} 件"]
        for version in versions[:5]:
            lines.append(f"  ・{plain(version.stem_html)[:40]} → 正答 {version.correct}")

        unused = unused_correct_item_nos(self.session, cset)
        if unused:
            by_no = cset.items_by_no()
            terms = "、".join(plain(by_no[no]) for no in unused)
            lines.append(f"まだ正答に使っていない項目: {terms}")
        else:
            lines.append("すべての項目が正答として使われている")
        self.set_status.setText("\n".join(lines))

    def _set_choices(self, htmls: list[str]) -> None:
        for edit, html in zip(self.choice_edits, list(htmls) + [""] * 5, strict=False):
            edit.set_fragment_html(html)

    def _set_correct(self, correct: str) -> None:
        normalized = normalize_correct(correct)
        for label, check in zip(LABELS, self.correct_boxes, strict=True):
            check.setChecked(label in normalized)

    # -- 編集操作 -----------------------------------------------------------
    def _apply_instruction(self) -> None:
        item_type = self.instruction.currentData()
        updated = set_instruction(self.stem.fragment_html(), item_type)
        self.stem.set_fragment_html(updated)
        self._revalidate()

    def _swap(self, a: int, b: int) -> None:
        """印字順を入れ替える。**セットは変わらない**(順序はセットに属さない)。"""
        if not (0 <= a < len(self.choice_edits) and 0 <= b < len(self.choice_edits)):
            return
        ha, hb = self.choice_edits[a].fragment_html(), self.choice_edits[b].fragment_html()
        self.choice_edits[a].set_fragment_html(hb)
        self.choice_edits[b].set_fragment_html(ha)
        ca, cb = self.correct_boxes[a].isChecked(), self.correct_boxes[b].isChecked()
        self.correct_boxes[a].setChecked(cb)
        self.correct_boxes[b].setChecked(ca)
        self._revalidate()

    # -- 入力値 -------------------------------------------------------------
    def stem_html(self) -> str:
        return normalize_stem(self.stem.fragment_html())

    def printed_choices(self) -> list[str]:
        return [normalize_choice(edit.fragment_html()) for edit in self.choice_edits]

    def correct(self) -> str:
        return "".join(
            label
            for label, check in zip(LABELS, self.correct_boxes, strict=True)
            if check.isChecked()
        )

    def tag_list(self) -> list[str]:
        raw = self.tags.text().replace(",", "、")
        return [t.strip() for t in raw.split("、") if t.strip()]

    def status(self) -> str:
        return Q_DRAFT if self.draft_check.isChecked() else Q_ACTIVE

    # -- 検証(その場で警告。設計書 §14-2)---------------------------------
    def current_issues(self) -> list:
        return validate_draft(
            self.stem_html(), self.printed_choices(), self.correct(), status=self.status()
        )

    def _revalidate(self) -> None:
        derivation = derive_item_type_detail(self.stem_html())
        required = REQUIRED_COUNT.get(derivation.item_type or "")
        need = "1〜5 個" if derivation.item_type == "XX" else f"{required} 個" if required else "—"
        self.type_label.setText(
            f"導出タイプ: {derivation.item_type or '導出できません'} / 正答は {need}"
            f" / いま {len(self.correct())} 個"
        )

        printed = self.printed_choices()
        self.preview.setText(
            "印字プレビュー: "
            + "　".join(
                f"{label}　{render_choice(html)}"
                for label, html in zip(LABELS, printed, strict=True)
                if html
            )
        )

        issues = self.current_issues()
        fill_issue_list(self.issues, issues)
        blocked = any(i.blocking for i in issues)
        if hasattr(self, "save_button") and self.save_button is not None:
            self.save_button.setEnabled(not blocked)

    # -- 保存 ---------------------------------------------------------------
    def save(self) -> None:
        if self.question is None:
            result = self._save_new()
        elif self.mode_box.currentData() == MODE_REVISE:
            result = self._save_revision()
        else:
            result = self._save_derivation()

        if result is None:
            return
        if result.blocked:
            fill_issue_list(self.issues, result.issues)
            QMessageBox.warning(
                self, "保存できません", "検証に引っかかりました。内容を直してください。"
            )
            self.workspace.rollback()
            return

        self.workspace.commit()
        self.saved_question = result.question
        log.info(
            "問題 %s を保存しました(新しい版: %s)",
            result.question.id if result.question else "?",
            result.created_new_version,
        )
        self.accept()

    def _save_new(self):
        source = self.derive_source.latest_version if self.derive_source else None
        if source is not None:
            result, _ = derive_question_from_printed(
                self.session,
                source,
                stem_html=self.stem_html(),
                printed_choices=self.printed_choices(),
                correct=self.correct(),
                status=self.status(),
            )
        else:
            result, _ = create_question_from_printed(
                self.session,
                stem_html=self.stem_html(),
                printed_choices=self.printed_choices(),
                correct=self.correct(),
                status=self.status(),
            )
        if not result.blocked and result.question is not None:
            set_tags(self.session, result.question, self.tag_list())
        return result

    def _save_revision(self):
        """改訂。**新版になる編集かどうかを先に伝えてから確認を取る**(設計書 §2.2)。"""
        if not self._confirm_revision():
            return None
        result, _ = revise_question_from_printed(
            self.session,
            self.question,
            stem_html=self.stem_html(),
            printed_choices=self.printed_choices(),
            correct=self.correct(),
        )
        if not result.blocked and result.question is not None:
            result.question.status = self.status()
            set_tags(self.session, result.question, self.tag_list())
        return result

    def _confirm_revision(self) -> bool:
        if self.creates_new_version():
            message = (
                "改訂として保存します。旧版は今後出題されなくなります。\n\n"
                "正答・選択肢・並び順・指示文言のいずれかが変わるため、新しい版になります"
                "(設計書 §2.2)。過去の統計は旧版に残り、新版は実績ゼロから始まります。"
            )
        else:
            message = (
                "改訂として保存します。\n\n"
                "正答・選択肢・並び順・指示文言のいずれも変わらないため、同じ版を書き換えます"
                "(設計書 §2.2)。過去の統計はそのまま残ります。"
            )
        answer = QMessageBox.question(
            self,
            "改訂として保存",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def creates_new_version(self) -> bool:
        """いまの入力を改訂として保存したとき新版になるか(設計書 §2.2 但し書き)。"""
        if self.question is None:
            return True
        latest = self.question.latest_version
        if latest is None:
            return True
        choice_set, order, _ = resolve_printed(self.session, self.printed_choices())
        return requires_new_version(
            latest,
            choice_set_id=choice_set.id,
            choice_order=order,
            correct=normalize_correct(self.correct()),
            stem_html=self.stem_html(),
        )

    def _save_derivation(self):
        source = self.question.latest_version if self.question else None
        if source is None:
            return None
        result, _ = derive_question_from_printed(
            self.session,
            source,
            stem_html=self.stem_html(),
            printed_choices=self.printed_choices(),
            correct=self.correct(),
            status=self.status(),
        )
        if not result.blocked and result.question is not None:
            set_tags(self.session, result.question, self.tag_list())
        return result
