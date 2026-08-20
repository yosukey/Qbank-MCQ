"""集計 CSV の読み取りと正答キーの書き出し(設計書 §10)。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from designdata import (
    DESIGN_META,
    DESIGN_Q1_CORRECT,
    DESIGN_Q1_DISC,
    DESIGN_Q1_N,
    DESIGN_Q1_N_CORRECT,
    DESIGN_Q1_P,
    DESIGN_Q1_VALUES,
)
from itembank.core.stats import BLANK, OTHER, PATTERNS
from itembank.io.csv_key import (
    AnswerKeyRow,
    answer_key_filename,
    read_answer_key,
    rows_from_exam_items,
    write_answer_key,
)
from itembank.io.csv_stats import StatsFormatError, parse_stats_csv

HEADER = ["問題", "正答肢", "正答率", "正答数", "識別係数", *PATTERNS, "空白"]


def write_csv(path: Path, rows: list[list[object]], *, meta: bool = True) -> Path:
    """設計書 §10.2 の書式(UTF-8 BOM、CRLF)で書く。"""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        if meta:
            for key, value in DESIGN_META.items():
                writer.writerow([f"#{key}", value])
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


@pytest.fixture
def design_csv(tmp_path: Path) -> Path:
    """設計書 §10.2 にそのまま載っている 1 行だけの CSV。"""
    return write_csv(
        tmp_path / "item_stats_2025.csv",
        [
            [
                1,
                DESIGN_Q1_CORRECT,
                DESIGN_Q1_P,
                DESIGN_Q1_N_CORRECT,
                DESIGN_Q1_DISC,
                *DESIGN_Q1_VALUES,
            ]
        ],
    )


# ---------------------------------------------------------------------------
# 集計 CSV
# ---------------------------------------------------------------------------


def test_meta_rows_are_read(design_csv: Path) -> None:
    meta = parse_stats_csv(design_csv).meta
    assert meta.exam_name == "口腔組織学定期試験"
    assert meta.exam_date == "2025-08-25"
    assert meta.n_examinees == DESIGN_Q1_N
    assert meta.disc_type == "D_25"


def test_the_design_row_is_parsed_exactly(design_csv: Path) -> None:
    row = parse_stats_csv(design_csv).rows[0]
    assert row.position == 1
    assert row.correct == "ad"
    assert row.p_reported == DESIGN_Q1_P
    assert row.n_correct_reported == DESIGN_Q1_N_CORRECT
    assert row.disc == DESIGN_Q1_DISC
    assert row.total == DESIGN_Q1_N
    assert row.counts()["ad"] == 112
    assert row.counts()["ab"] == 7


def test_blank_column_maps_to_the_empty_pattern(tmp_path: Path) -> None:
    values = list(DESIGN_Q1_VALUES)
    values[-1] = 5
    values[7] = 107  # ad を 5 減らして合計を保つ
    path = write_csv(tmp_path / "b.csv", [[1, "ad", 0.7698, 107, 0.5, *values]])
    row = parse_stats_csv(path).rows[0]
    assert row.counts_raw[BLANK] == 5
    assert row.total == DESIGN_Q1_N


def test_header_columns_are_reported_for_validation(design_csv: Path) -> None:
    parsed = parse_stats_csv(design_csv)
    assert parsed.pattern_columns_found == [*PATTERNS, BLANK]
    assert parsed.missing_fixed_columns == []


def test_alternate_blank_and_other_column_names(tmp_path: Path) -> None:
    """実物の方言は ``無解答`` と ``その他``。同じ区分に正規化される。"""
    path = tmp_path / "ssdb.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        writer.writerow(["問", "正答", "正答率(%)", "識別係数", *PATTERNS, "無解答", "その他"])
        writer.writerow([1, "ad", 80.58, 0.529, *DESIGN_Q1_VALUES, 0])

    parsed = parse_stats_csv(path)
    assert parsed.pattern_columns_found == [*PATTERNS, BLANK, OTHER]
    assert parsed.percent_scale is True
    assert parsed.rows[0].p_reported == pytest.approx(0.8058)


def test_missing_fixed_column_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "nocorrect.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        writer.writerow(["問題", "正答率", "正答数", "識別係数", *PATTERNS, "空白"])
        writer.writerow([1, 0.8, 112, 0.5, *DESIGN_Q1_VALUES])
    assert parse_stats_csv(path).missing_fixed_columns == ["正答肢"]


def test_non_integer_counts_are_detected_not_silently_rounded(tmp_path: Path) -> None:
    """人数のはずが割合で届くと全統計が静かに壊れる(実装計画 §11)。"""
    ratios = [v / DESIGN_Q1_N for v in DESIGN_Q1_VALUES]
    path = write_csv(tmp_path / "r.csv", [[1, "ad", 0.8058, 112, 0.529, *ratios]])
    row = parse_stats_csv(path).rows[0]
    assert row.has_non_integer


def test_unreadable_cells_are_kept_for_reporting(tmp_path: Path) -> None:
    values = list(DESIGN_Q1_VALUES)
    values[2] = "―"
    path = write_csv(tmp_path / "u.csv", [[1, "ad", 0.8058, 112, 0.529, *values]])
    row = parse_stats_csv(path).rows[0]
    assert "c" in row.unreadable


def test_negative_counts_are_detected(tmp_path: Path) -> None:
    values = list(DESIGN_Q1_VALUES)
    values[1] = -1
    path = write_csv(tmp_path / "n.csv", [[1, "ad", 0.8058, 112, 0.529, *values]])
    assert parse_stats_csv(path).rows[0].has_negative


def test_correct_is_normalized_on_read(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "d.csv", [[1, "DA", 0.8058, 112, 0.529, *DESIGN_Q1_VALUES]])
    assert parse_stats_csv(path).rows[0].correct == "ad"


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = design_csv_with_blank_line(tmp_path)
    assert parse_stats_csv(path).n_rows == 1


def design_csv_with_blank_line(tmp_path: Path) -> Path:
    path = write_csv(
        tmp_path / "blank.csv",
        [[1, "ad", 0.8058, 112, 0.529, *DESIGN_Q1_VALUES]],
    )
    path.write_text(
        path.read_text(encoding="utf-8-sig").replace("問題,", "\r\n問題,"), encoding="utf-8-sig"
    )
    return path


def test_a_file_without_a_header_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "meta_only.csv"
    path.write_text("#試験名,x\r\n", encoding="utf-8-sig")
    with pytest.raises(StatsFormatError):
        parse_stats_csv(path)


def test_utf8_bom_is_required_but_tolerated_either_way(tmp_path: Path) -> None:
    """``utf-8-sig`` は BOM が無くても読める。"""
    path = tmp_path / "nobom.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        writer.writerow(HEADER)
        writer.writerow([1, "ad", 0.8058, 112, 0.529, *DESIGN_Q1_VALUES])
    assert parse_stats_csv(path).rows[0].correct == "ad"


# ---------------------------------------------------------------------------
# 正答キー(設計書 §10.1)
# ---------------------------------------------------------------------------


def test_answer_key_matches_the_documented_shape(tmp_path: Path) -> None:
    path = write_answer_key(
        [
            AnswerKeyRow(1, "ad"),
            AnswerKeyRow(2, "bc"),
            AnswerKeyRow(3, "d"),
            AnswerKeyRow(22, "abcde"),
        ],
        tmp_path / "key.csv",
    )
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM が必要"
    assert b"\r\n" in raw, "CRLF が必要"
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[:5] == ["問題,正答肢", "1,ad", "2,bc", "3,d", "22,abcde"]


def test_answer_key_is_sorted_by_position(tmp_path: Path) -> None:
    path = write_answer_key([AnswerKeyRow(3, "a"), AnswerKeyRow(1, "b")], tmp_path / "k.csv")
    assert [r.position for r in read_answer_key(path)] == [1, 3]


def test_answer_key_normalizes_labels(tmp_path: Path) -> None:
    path = write_answer_key([AnswerKeyRow(1, "DA")], tmp_path / "k.csv")
    assert read_answer_key(path)[0].correct == "ad"


def test_answer_key_roundtrip(tmp_path: Path) -> None:
    rows = rows_from_exam_items([(1, "ad"), (2, "b"), (3, "abcde")])
    path = write_answer_key(rows, tmp_path / "k.csv")
    assert read_answer_key(path) == rows


def test_answer_key_filename() -> None:
    assert answer_key_filename(7) == "answer_key_7.csv"
