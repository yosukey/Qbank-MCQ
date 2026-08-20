"""採点システムから届く集計 CSV の読み取り(設計書 §10.2)。

設計書 §10.2 の形式が**正**。採点側の出力をそのまま受け取り、ItemBank 側で
事前加工を要求しない::

    問,配点,措置,正答,正答率(%),識別係数,点双列相関,a,b,…,abcde,無解答,その他
    1,5,none,b,50.0,0.294,0.265,13,69,24,20,12,0,…,0,0
    7,3,none,記述式,38.4,0.706,0.478,-,-,-,…,-,-

読み取りで押さえる点(設計書 §10.2 の (1)〜(4)):

- **メタ行が無い。** 受験者数は度数列の合計から導く(``StatsMeta.derived_n``)
- **``正答率(%)`` は 0〜100。** 見出しの ``(%)`` を見て 0〜1 に直す
- **``正答数`` 列が無い。** 正答パターン列の度数がそれにあたる
- **``その他`` 列がある。** 31 パターンでも無回答でもない区分として N に算入する
- **記述式が混在する。** 度数欄が丸ごと空の行はバンクの対象外として ``non_mcq_rows`` に分ける

設計書 v15 の書式(メタ行つき・``正答率`` が 0〜1・``空白`` 列)も読める。旧データの
取り込みに備えて残してあり、列名の別名表で吸収して ``legacy`` 方言と判定する。

**このモジュールは読むだけで、正しさの判定はしない。** 検証は
``core.validate.validate_stats_import``(設計書 §9.2)が行う。壊れた CSV でも
「どこが壊れているか」を示せるよう、ここでは例外を投げずに読めたものをそのまま持ち帰る。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..core.stats import BLANK, OTHER
from ..core.typing_rules import LABELS, normalize_correct

#: メタ行の接頭辞(``legacy`` 方言のみ)。
META_PREFIX = "#"

#: 出題番号の列名。
POSITION_ALIASES = ("問題", "問", "設問", "問題番号")
#: 正答肢の列名。
CORRECT_ALIASES = ("正答肢", "正答")
#: 正答率の列名。``(%)`` を含むものは 0〜100 とみなす。
P_ALIASES = ("正答率", "正答率(%)", "正答率(%)", "正答率％")
#: 正答数の列名(設計書 §10.2 の形式には無い。v15 形式にはある)。
N_CORRECT_ALIASES = ("正答数",)
#: 識別係数の列名。
DISC_ALIASES = ("識別係数", "識別指数", "D")
#: 無回答の列名。
BLANK_ALIASES = ("空白", "無解答", "未解答")
#: 分類できない解答の列名。
OTHER_ALIASES = ("その他",)

#: 読むが保存しない列。存在しても「知らない列」として警告しない。
IGNORED_ALIASES = ("配点", "措置", "点双列相関", "点双列相関係数", "備考")

#: 記述式など、選択式でない設問の正答欄に入る文字列。
NON_MCQ_MARKERS = ("記述式", "論述式", "記述", "自由記述")

#: 度数欄が空であることを表す記号。
EMPTY_CELL_MARKERS = ("-", "‐", "―", "—", "ー", "")

#: 設計書 §10.2 の形式(正)。
DIALECT_SSDB = "ssdb"
#: 設計書 v15 の形式。旧データ取り込み用に読めるようにしてある。
DIALECT_LEGACY = "legacy"

KIND_MCQ = "mcq"
KIND_NON_MCQ = "non_mcq"


class StatsFormatError(ValueError):
    """CSV としてそもそも読めない(ヘッダ行が無いなど)。"""


@dataclass
class StatsMeta:
    """``#`` 行から読んだ試験メタ情報。設計書 §10.2 の形式では空になる。"""

    raw: dict[str, str] = field(default_factory=dict)
    #: メタ行が無いとき、度数合計から導いた受験者数。
    derived_n: int | None = None

    @property
    def exam_name(self) -> str | None:
        return self.raw.get("試験名")

    @property
    def exam_date(self) -> str | None:
        return self.raw.get("試験日")

    @property
    def disc_type(self) -> str | None:
        return self.raw.get("識別係数定義")

    @property
    def n_examinees(self) -> int | None:
        """メタ行の受験者数。**無ければ None**(度数合計との照合は行われない)。"""
        value = self.raw.get("受験者数")
        if value is None:
            return None
        try:
            return int(float(value))
        except ValueError:
            return None

    @property
    def effective_n(self) -> int | None:
        """保存に使う受験者数。メタ行があればそれ、無ければ度数合計から導いた値。

        照合(検証チェーン §9.2-2)には ``n_examinees`` を使うこと。こちらは
        導出値を混ぜているので、突き合わせ相手にはならない。
        """
        return self.n_examinees if self.n_examinees is not None else self.derived_n


@dataclass
class StatsRow:
    """1 設問ぶんの行。"""

    position: int
    correct: str
    #: CSV に書かれていた正答率を 0〜1 に直したもの。
    #: **保存には使わない**(正答数/N から再計算する)。
    p_reported: float | None
    n_correct_reported: int | None
    disc: float | None
    #: パターン → 度数。読めたまま(整数とは限らない)保持する。
    counts_raw: dict[str, float] = field(default_factory=dict)
    #: 数値として読めなかった列。
    unreadable: dict[str, str] = field(default_factory=dict)
    line_no: int = 0
    #: ``mcq`` か ``non_mcq``(記述式など)か。
    kind: str = KIND_MCQ
    #: 正答欄の原文。記述式なら ``記述式`` などが入る。
    correct_raw: str = ""
    #: 配点。読むが保存しない。
    points: float | None = None
    #: 措置。``none`` 以外は統計の解釈が変わるので警告の材料にする。
    adjustment: str | None = None
    #: 保存しない列の原文(点双列相関など)。目視確認用。
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(self.counts_raw.values())

    @property
    def has_non_integer(self) -> bool:
        """人数のはずが割合になっていないか(実装計画 §11 の落とし穴)。"""
        return any(v != int(v) for v in self.counts_raw.values())

    @property
    def has_negative(self) -> bool:
        return any(v < 0 for v in self.counts_raw.values())

    @property
    def is_adjusted(self) -> bool:
        """措置が入っているか。``none`` / 空欄は素の結果とみなす。"""
        return bool(self.adjustment) and self.adjustment.strip().lower() not in (
            "none",
            "なし",
            "-",
        )

    def counts(self) -> dict[str, int]:
        """度数を整数の辞書にする。0 の列は落とす。"""
        return {k: int(v) for k, v in self.counts_raw.items() if v}


@dataclass
class StatsFile:
    meta: StatsMeta
    #: 選択式の行だけ。検証と取込はこれを見る。
    rows: list[StatsRow]
    #: 記述式など選択式でない行。統計の対象外だが件数を報告する。
    non_mcq_rows: list[StatsRow] = field(default_factory=list)
    #: ヘッダに現れた度数列名を正規化したもの(検証 §9.2-8 が突き合わせる)。
    pattern_columns_found: list[str] = field(default_factory=list)
    #: 固定列のうち欠けていたもの。
    missing_fixed_columns: list[str] = field(default_factory=list)
    #: どの方言と判定したか。
    dialect: str = DIALECT_LEGACY
    #: 正答率が 0〜100 で書かれていたか。
    percent_scale: bool = False
    source_file: str = ""

    @property
    def n_rows(self) -> int:
        return len(self.rows)


@dataclass
class _Layout:
    """ヘッダ 1 行から割り出した列の役割。"""

    position: str | None = None
    correct: str | None = None
    p: str | None = None
    n_correct: str | None = None
    disc: str | None = None
    points: str | None = None
    adjustment: str | None = None
    percent_scale: bool = False
    #: 度数列。``実際の見出し -> 正規化キー``
    counts: dict[str, str] = field(default_factory=dict)
    #: 読むが保存しない列。
    ignored: list[str] = field(default_factory=list)

    def missing_fixed(self) -> list[str]:
        out: list[str] = []
        if self.position is None:
            out.append(POSITION_ALIASES[0])
        if self.correct is None:
            out.append(CORRECT_ALIASES[0])
        if self.p is None:
            out.append(P_ALIASES[0])
        if self.disc is None:
            out.append(DISC_ALIASES[0])
        return out


def _pick(header: list[str], aliases: tuple[str, ...]) -> str | None:
    for name in header:
        if name in aliases:
            return name
    return None


def _analyze_header(header: list[str]) -> _Layout:
    layout = _Layout()
    layout.position = _pick(header, POSITION_ALIASES)
    layout.correct = _pick(header, CORRECT_ALIASES)
    layout.p = _pick(header, P_ALIASES)
    layout.n_correct = _pick(header, N_CORRECT_ALIASES)
    layout.disc = _pick(header, DISC_ALIASES)
    layout.points = _pick(header, ("配点",))
    layout.adjustment = _pick(header, ("措置",))

    # 「正答率(%)」のように見出しで単位が示されていれば 0〜100 とみなす。
    if layout.p and ("%" in layout.p or "％" in layout.p):
        layout.percent_scale = True

    taken = {
        layout.position,
        layout.correct,
        layout.p,
        layout.n_correct,
        layout.disc,
        layout.points,
        layout.adjustment,
    }
    valid_patterns = _valid_pattern_names()
    for name in header:
        if name in taken:
            continue
        if name in IGNORED_ALIASES:
            layout.ignored.append(name)
        elif name in BLANK_ALIASES:
            layout.counts[name] = BLANK
        elif name in OTHER_ALIASES:
            layout.counts[name] = OTHER
        elif name in valid_patterns:
            layout.counts[name] = name
        else:
            # 知らない列。度数列として扱い、検証 §9.2-8 に判断させる。
            layout.counts[name] = name
    return layout


def _valid_pattern_names() -> frozenset[str]:
    from ..core.stats import PATTERN_SET

    return PATTERN_SET


def _to_float(value: str) -> float | None:
    try:
        return float(value.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _is_empty_cell(value: str) -> bool:
    return value.strip() in EMPTY_CELL_MARKERS


def parse_stats_csv(path: Path | str) -> StatsFile:
    """集計 CSV を読む。BOM 付き UTF-8 を前提にする(``utf-8-sig``)。"""
    source = Path(path)
    meta = StatsMeta()
    header: list[str] | None = None
    layout: _Layout | None = None
    mcq: list[StatsRow] = []
    non_mcq: list[StatsRow] = []

    with source.open("r", encoding="utf-8-sig", newline="") as fh:
        for line_no, fields in enumerate(csv.reader(fh), start=1):
            if not fields or all(not f.strip() for f in fields):
                continue
            first = fields[0].strip()

            if first.startswith(META_PREFIX):
                key = first.lstrip(META_PREFIX).strip()
                meta.raw[key] = fields[1].strip() if len(fields) > 1 else ""
                continue

            if header is None:
                header = [f.strip() for f in fields]
                layout = _analyze_header(header)
                continue

            assert layout is not None
            row = _parse_row(header, layout, fields, line_no)
            (non_mcq if row.kind == KIND_NON_MCQ else mcq).append(row)

    if header is None or layout is None:
        raise StatsFormatError(f"ヘッダ行が見つかりません: {source}")

    totals = {int(r.total) for r in mcq if r.total}
    meta.derived_n = totals.pop() if len(totals) == 1 else None

    return StatsFile(
        meta=meta,
        rows=mcq,
        non_mcq_rows=non_mcq,
        # ヘッダに現れた順のまま渡す。同じ区分に寄る列名が 2 つあれば
        # 重複として検証チェーンが気づけるよう、集合にはしない。
        pattern_columns_found=list(layout.counts.values()),
        missing_fixed_columns=layout.missing_fixed(),
        dialect=DIALECT_SSDB if layout.percent_scale or not meta.raw else DIALECT_LEGACY,
        percent_scale=layout.percent_scale,
        source_file=str(source),
    )


def _parse_row(header: list[str], layout: _Layout, fields: list[str], line_no: int) -> StatsRow:
    # 列が足りない行はここでは落とさない。欠けた列は unreadable に回り、
    # 検証チェーン(設計書 §9.2)がどの列が無いかを名指しで報告する。
    record = dict(zip(header, (f.strip() for f in fields), strict=False))

    position_raw = record.get(layout.position or "", "")
    try:
        position = int(float(position_raw))
    except ValueError:
        position = 0

    correct_raw = record.get(layout.correct or "", "")
    counts_raw: dict[str, float] = {}
    unreadable: dict[str, str] = {}
    empty_cells: dict[str, str] = {}

    for column, key in layout.counts.items():
        cell = record.get(column, "")
        if _is_empty_cell(cell):
            empty_cells[column] = cell
            continue
        value = _to_float(cell)
        if value is None:
            unreadable[column] = cell
        else:
            counts_raw[key] = value

    # 記述式などの判定: 正答欄が選択肢でない、または度数欄が丸ごと空。
    all_empty = bool(layout.counts) and len(empty_cells) == len(layout.counts)
    is_non_mcq = all_empty or any(m in correct_raw for m in NON_MCQ_MARKERS)
    if not is_non_mcq and correct_raw and not set(correct_raw.lower()) <= set(LABELS):
        # a〜e 以外の文字が入っていれば選択式ではないとみなす。
        is_non_mcq = True

    if not is_non_mcq:
        # 度数欄が **一部だけ** 空なのは壊れた行。黙って 0 として扱うと受験者数が
        # 減り、正答率が狂う。読めなかった列として報告し、検証チェーンに止めさせる。
        unreadable.update(empty_cells)

    p_reported = _to_float(record.get(layout.p or "", ""))
    if p_reported is not None and layout.percent_scale:
        p_reported /= 100.0

    n_correct = _to_float(record.get(layout.n_correct or "", "")) if layout.n_correct else None

    return StatsRow(
        position=position,
        correct=normalize_correct(correct_raw),
        p_reported=p_reported,
        n_correct_reported=int(n_correct) if n_correct is not None else None,
        disc=_to_float(record.get(layout.disc or "", "")),
        counts_raw=counts_raw,
        unreadable=unreadable if not is_non_mcq else {},
        line_no=line_no,
        kind=KIND_NON_MCQ if is_non_mcq else KIND_MCQ,
        correct_raw=correct_raw,
        points=_to_float(record.get(layout.points or "", "")) if layout.points else None,
        adjustment=record.get(layout.adjustment or "") if layout.adjustment else None,
        extras={name: record.get(name, "") for name in layout.ignored},
    )
