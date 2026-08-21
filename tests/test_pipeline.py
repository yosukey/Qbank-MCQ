"""CLI を通した通し試験と、ゴールデンファイルによる回帰(実装計画 §4 M3/M5/M6)。

受入条件のうちここで押さえるもの:

- ``--dry-run`` で結果を JSON に出力し、目視確認できる (M3)
- **抽出結果をゴールデンファイルとして固定し、以後の回帰テストに使う** (M3)
- 生成した冊子 docx を**再取込したとき、元の問題と一致する** (M5)
- わざと壊した CSV で**確実にブロックされる** (M6)

``testdata/sample/`` は ``tools/make_sample_data.py`` が決定的に生成したもの。
2025年度の実 docx / 集計 CSV を ``testdata/`` に置けば、
``test_real_docx_matches_golden`` がそちらも自動で回帰対象にする。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qbank_mcq.__main__ import main
from qbank_mcq.io.docx_read import parse_docx

TESTDATA = Path(__file__).parent.parent / "testdata"
SAMPLE = TESTDATA / "sample"
SAMPLE_DOCX = SAMPLE / "exam_2025.docx"
SAMPLE_STATS = SAMPLE / "item_stats_2025.csv"
SAMPLE_GOLDEN = SAMPLE / "exam_2025.golden.json"

pytestmark = pytest.mark.skipif(
    not SAMPLE_DOCX.exists(),
    reason="testdata/sample/ がありません。python tools/make_sample_data.py で作れます",
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "bank.sqlite"


def run(*argv: str) -> int:
    return main(list(argv))


# ---------------------------------------------------------------------------
# ゴールデンファイル(実装計画 §6)
# ---------------------------------------------------------------------------


def test_sample_docx_matches_golden() -> None:
    """抽出結果が固定した JSON と一致する。差分が出たらテスト失敗。"""
    expected = json.loads(SAMPLE_GOLDEN.read_text(encoding="utf-8"))
    actual = json.loads(
        json.dumps(parse_docx(SAMPLE_DOCX).as_dict(), ensure_ascii=False, sort_keys=True)
    )
    assert actual == expected


REAL_PAIRS = [
    (p, p.with_suffix(".golden.json"))
    for p in sorted(TESTDATA.glob("*.docx"))
    if p.with_suffix(".golden.json").exists()
]


@pytest.mark.skipif(not REAL_PAIRS, reason="testdata/ に実 docx とゴールデンの組がまだ無い")
@pytest.mark.parametrize("docx,golden", REAL_PAIRS, ids=lambda p: p.name)
def test_real_docx_matches_golden(docx: Path, golden: Path) -> None:
    """2025年度の実データを置いたら自動的に回帰対象になる(実装計画 §0)。"""
    expected = json.loads(golden.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(parse_docx(docx).as_dict(), ensure_ascii=False, sort_keys=True))
    assert actual == expected


# ---------------------------------------------------------------------------
# CLI: 局面A の通し(設計書 §1.1)
# ---------------------------------------------------------------------------


def test_dry_run_writes_inspectable_json(tmp_path: Path, db: Path, capsys) -> None:
    out = tmp_path / "dry.json"
    code = run(
        "--db",
        str(db),
        "import-exam",
        "--docx",
        str(SAMPLE_DOCX),
        "--stats",
        str(SAMPLE_STATS),
        "--dry-run",
        "--json",
        str(out),
    )
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["document"]["questions"]) == 15
    assert len(data["stats_rows"]) == 15
    assert data["issues"] == []
    # --dry-run では DB を作らない。
    assert not db.exists()


def test_full_import_then_export_then_reimport(tmp_path: Path, db: Path, capsys) -> None:
    """docx+CSV → バンク → 冊子 → 再取込 まで通し、往復一致を確かめる。"""
    assert (
        run(
            "--db",
            str(db),
            "import-exam",
            "--docx",
            str(SAMPLE_DOCX),
            "--stats",
            str(SAMPLE_STATS),
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "15 問を登録しました" in captured.out
    assert "統計を 15 問に取り込みました" in captured.out

    out_dir = tmp_path / "out"
    assert (
        run("--db", str(db), "export", "--exam", "1", "--what", "all", "--out", str(out_dir)) == 0
    )

    assert (out_dir / "answer_key_1.csv").exists()
    assert (out_dir / "crosswalk_1.xlsx").exists()
    assert (out_dir / "report_1.xlsx").exists()

    booklet = out_dir / "booklet_1.docx"
    assert booklet.exists()

    # **受入条件**: 生成した冊子を再取込すると元の問題と一致する(実装計画 §4 M5)。
    original = parse_docx(SAMPLE_DOCX)
    again = parse_docx(booklet)
    assert len(again.questions) == len(original.questions)
    for before, after in zip(original.questions, again.questions):
        assert after.number == before.number
        assert after.stem_html == before.stem_html
        assert after.choice_htmls == before.choice_htmls


def test_answer_key_is_readable_by_ss_database(tmp_path: Path, db: Path) -> None:
    """UTF-8 BOM 付き・CRLF で出ていること(設計書 §10.1)。"""
    run("--db", str(db), "import-exam", "--docx", str(SAMPLE_DOCX), "--stats", str(SAMPLE_STATS))
    out_dir = tmp_path / "out"
    run("--db", str(db), "export", "--exam", "1", "--what", "key", "--out", str(out_dir))

    raw = (out_dir / "answer_key_1.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    lines = raw.decode("utf-8-sig").strip().splitlines()
    assert lines[0] == "問題,正答肢"
    assert len(lines) == 16  # ヘッダ + 15 問


# ---------------------------------------------------------------------------
# CLI: 局面B の統計取込(設計書 §1.2, §9)
# ---------------------------------------------------------------------------


BROKEN = [
    ("broken_missing_column.csv", "列欠落"),
    ("broken_wrong_correct.csv", "正答不一致"),
    ("broken_ratio_not_count.csv", "割合を人数と誤設定"),
    ("broken_total_mismatch.csv", "度数合計の不一致"),
    ("broken_row_missing.csv", "行数不足"),
]


@pytest.fixture
def imported_db(db: Path) -> Path:
    run("--db", str(db), "import-exam", "--docx", str(SAMPLE_DOCX), "--stats", str(SAMPLE_STATS))
    return db


@pytest.mark.parametrize("filename,label", BROKEN, ids=[b[0] for b in BROKEN])
def test_broken_csv_is_blocked(imported_db: Path, filename: str, label: str) -> None:
    """**わざと壊した CSV で確実にブロックされる**(実装計画 §4 M6 受入条件)。"""
    path = SAMPLE / filename
    assert path.exists(), f"{filename} がありません"
    assert (
        run("--db", str(imported_db), "import-stats", "--exam", "1", "--csv", str(path)) == 1
    ), f"{label} が素通りしました"


def test_a_good_csv_passes(imported_db: Path) -> None:
    assert (
        run("--db", str(imported_db), "import-stats", "--exam", "1", "--csv", str(SAMPLE_STATS))
        == 0
    )


def test_stats_cannot_be_given_to_a_draft_exam(db: Path, capsys) -> None:
    """設計書 §9.1: 取込画面ではまず試験を選ぶ。finalized のものにのみ与えられる。"""
    run("--db", str(db), "import-exam", "--docx", str(SAMPLE_DOCX), "--stats", str(SAMPLE_STATS))
    assert run("--db", str(db), "select", "--total", "5", "--create-exam", "次年度") in (0, 1)
    code = run("--db", str(db), "import-stats", "--exam", "2", "--csv", str(SAMPLE_STATS))
    assert code == 2
    assert "確定していません" in capsys.readouterr().err


def test_missing_exam_is_reported(db: Path) -> None:
    run("--db", str(db), "db", "init")
    assert run("--db", str(db), "import-stats", "--exam", "99", "--csv", str(SAMPLE_STATS)) == 2


# ---------------------------------------------------------------------------
# CLI: その他
# ---------------------------------------------------------------------------


def test_inspect_docx_summarizes_formats(capsys) -> None:
    """スパイク①: 想定外の書式がどれだけあるかを目で確認する(実装計画 §12-3)。"""
    assert run("inspect-docx", str(SAMPLE_DOCX)) == 0
    out = capsys.readouterr().out
    assert "eastAsia=ＭＳ ゴシック" in out
    assert "想定外の書式を含む run: 0 件" in out


def test_select_and_finalize_flow(imported_db: Path, capsys) -> None:
    assert run("--db", str(imported_db), "select", "--total", "5", "--create-exam", "次年度") == 0
    assert "試験 2" in capsys.readouterr().out

    assert run("--db", str(imported_db), "finalize", "--exam", "2", "--check-only") == 0
    assert run("--db", str(imported_db), "finalize", "--exam", "2") == 0
    assert run("--db", str(imported_db), "exams") == 0
    assert "finalized" in capsys.readouterr().out


def test_bank_listing_pairs_p_with_type(imported_db: Path, capsys) -> None:
    assert run("--db", str(imported_db), "bank") == 0
    out = capsys.readouterr().out
    assert "正答率" in out and "(A)" in out


def test_audit_sets_is_clean_on_the_sample(imported_db: Path) -> None:
    assert run("--db", str(imported_db), "audit-sets") == 0


def test_db_commands(db: Path, capsys) -> None:
    assert run("--db", str(db), "db", "init") == 0
    assert "スキーマ版: 1" in capsys.readouterr().out
    assert run("--db", str(db), "db", "migrate") == 0
    assert "移行はありません" in capsys.readouterr().out
    assert run("--db", str(db), "db", "backup") == 0
    assert "バックアップ:" in capsys.readouterr().out
