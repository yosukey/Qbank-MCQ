"""正答キー CSV の書き出しと読み戻し(設計書 §10.1)。

``answer_key_{exam_id}.csv``(UTF-8 BOM 付、CRLF)::

    問題,正答肢
    1,ad
    2,bc
    22,abcde

``exam_items.correct_asked`` から生成する。**正答を書けるのは本アプリのみ**
(設計書 §17「正答の二重管理」)。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.typing_rules import normalize_correct

HEADER = ("問題", "正答肢")

#: ss-database 側が BOM 付き UTF-8 / CRLF を前提にしている(設計書 §10)。
ENCODING = "utf-8-sig"
LINE_TERMINATOR = "\r\n"


@dataclass(frozen=True)
class AnswerKeyRow:
    position: int
    correct: str


def answer_key_filename(exam_id: int) -> str:
    return f"answer_key_{exam_id}.csv"


def write_answer_key(rows: Iterable[AnswerKeyRow], path: Path | str) -> Path:
    """正答キーを書き出す。出題番号の昇順に並べる。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: r.position)

    with out.open("w", encoding=ENCODING, newline="") as fh:
        writer = csv.writer(fh, lineterminator=LINE_TERMINATOR)
        writer.writerow(HEADER)
        for row in ordered:
            writer.writerow([row.position, normalize_correct(row.correct)])
    return out


def read_answer_key(path: Path | str) -> list[AnswerKeyRow]:
    """書き出した正答キーを読み戻す(往復確認・実機確認用)。"""
    rows: list[AnswerKeyRow] = []
    with Path(path).open("r", encoding=ENCODING, newline="") as fh:
        for record in csv.reader(fh):
            if not record or record[0].strip() == HEADER[0]:
                continue
            if len(record) < 2:
                continue
            rows.append(AnswerKeyRow(int(record[0].strip()), normalize_correct(record[1])))
    return rows


def rows_from_exam_items(items: Sequence[tuple[int, str]]) -> list[AnswerKeyRow]:
    """``(position, correct_asked)`` の並びから行を作る。"""
    return [AnswerKeyRow(position, correct) for position, correct in items]
