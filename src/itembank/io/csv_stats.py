"""ss-database から届く集計 CSV の読み取り(設計書 §10.2)。

書式::

    #試験名,口腔組織学定期試験
    #試験日,2025-08-25
    #受験者数,139
    #識別係数定義,D_25
    問題,正答肢,正答率,正答数,識別係数,a,b,…,abcde,空白
    1,ad,0.8058,112,0.529,0,1,0,…,0

UTF-8 BOM 付き・CRLF。正答率は丸めない実数、値はすべて人数(整数)、
パターン列は 31 列 + 空白 1 列。

**このモジュールは読むだけで、正しさの判定はしない。** 検証は
``core.validate.validate_stats_import``(設計書 §9.2 の 9 項目)が行う。
壊れた CSV でも「どこが壊れているか」を示せるよう、ここでは例外を投げずに
読めたものをそのまま持ち帰る。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..core.stats import BLANK, BLANK_COLUMN
from ..core.typing_rules import normalize_correct

#: メタ行の接頭辞。
META_PREFIX = "#"

#: 度数列の前に並ぶ固定列(設計書 §10.2)。
FIXED_COLUMNS = ("問題", "正答肢", "正答率", "正答数", "識別係数")

META_EXAM_NAME = "試験名"
META_EXAM_DATE = "試験日"
META_N_EXAMINEES = "受験者数"
META_DISC_TYPE = "識別係数定義"


class StatsFormatError(ValueError):
    """CSV としてそもそも読めない(ヘッダ行が無いなど)。"""


@dataclass
class StatsMeta:
    """``#`` 行から読んだ試験メタ情報。"""

    raw: dict[str, str] = field(default_factory=dict)

    @property
    def exam_name(self) -> str | None:
        return self.raw.get(META_EXAM_NAME)

    @property
    def exam_date(self) -> str | None:
        return self.raw.get(META_EXAM_DATE)

    @property
    def disc_type(self) -> str | None:
        return self.raw.get(META_DISC_TYPE)

    @property
    def n_examinees(self) -> int | None:
        value = self.raw.get(META_N_EXAMINEES)
        if value is None:
            return None
        try:
            return int(float(value))
        except ValueError:
            return None


@dataclass
class StatsRow:
    """1 設問ぶんの行。"""

    position: int
    correct: str
    #: CSV に書かれていた正答率。**保存には使わない**(正答数/N から再計算する)。
    p_reported: float | None
    n_correct_reported: int | None
    disc: float | None
    #: パターン → 度数。読めたまま(整数とは限らない)保持する。
    counts_raw: dict[str, float] = field(default_factory=dict)
    #: 数値として読めなかった列。
    unreadable: dict[str, str] = field(default_factory=dict)
    line_no: int = 0

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

    def counts(self) -> dict[str, int]:
        """度数を整数の辞書にする。0 の列は落とす。"""
        return {k: int(v) for k, v in self.counts_raw.items() if v}


@dataclass
class StatsFile:
    meta: StatsMeta
    rows: list[StatsRow]
    #: ヘッダに現れた度数列名(検証 §9.2-8 が 31+1 と突き合わせる)。
    pattern_columns_found: list[str]
    #: 固定列のうち欠けていたもの。
    missing_fixed_columns: list[str]
    source_file: str = ""

    @property
    def n_rows(self) -> int:
        return len(self.rows)


def _to_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def parse_stats_csv(path: Path | str) -> StatsFile:
    """集計 CSV を読む。BOM 付き UTF-8 を前提にする(``utf-8-sig``)。"""
    source = Path(path)
    meta = StatsMeta()
    header: list[str] | None = None
    rows: list[StatsRow] = []

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
                continue

            rows.append(_parse_row(header, fields, line_no))

    if header is None:
        raise StatsFormatError(f"ヘッダ行が見つかりません: {source}")

    fixed = set(FIXED_COLUMNS)
    return StatsFile(
        meta=meta,
        rows=rows,
        pattern_columns_found=[c for c in header if c not in fixed],
        missing_fixed_columns=[c for c in FIXED_COLUMNS if c not in header],
        source_file=str(source),
    )


def _parse_row(header: list[str], fields: list[str], line_no: int) -> StatsRow:
    # 列が足りない行はここでは落とさない。欠けた列は unreadable に回り、
    # 検証チェーン(設計書 §9.2)がどの列が無いかを名指しで報告する。
    record = dict(zip(header, (f.strip() for f in fields), strict=False))

    position_raw = record.get("問題", "")
    try:
        position = int(float(position_raw))
    except ValueError:
        position = 0

    counts_raw: dict[str, float] = {}
    unreadable: dict[str, str] = {}
    for column in header:
        if column in FIXED_COLUMNS:
            continue
        key = BLANK if column == BLANK_COLUMN else column
        value = _to_float(record.get(column, ""))
        if value is None:
            unreadable[column] = record.get(column, "")
        else:
            counts_raw[key] = value

    n_correct = _to_float(record.get("正答数", ""))
    return StatsRow(
        position=position,
        correct=normalize_correct(record.get("正答肢", "")),
        p_reported=_to_float(record.get("正答率", "")),
        n_correct_reported=int(n_correct) if n_correct is not None else None,
        disc=_to_float(record.get("識別係数", "")),
        counts_raw=counts_raw,
        unreadable=unreadable,
        line_no=line_no,
    )
