"""設計書に実データとして載っている値。テストの基準にする。

実装計画 §0 は「実データをテスト資産にする」ことを開発方針に挙げ、2025年度の問題docx
と集計CSVをゴールデンファイルにするとしている。それらのファイルはまだリポジトリに
無いが、**設計書 §10.2 には集計CSVの実際の1行が転記されている**。正答率・受験者数・
識別係数が同時に載っており突き合わせが成立するため、これを検証可能な最小のゴールデン
データとして使う。

実ファイルが ``testdata/`` に置かれたら ``tests/test_import_golden.py`` が自動的に
それも回帰テストの対象にする。
"""

from __future__ import annotations

from itembank.core.stats import BLANK, PATTERNS

#: 設計書 §10.2 の 1 行目::
#:
#:   1,ad,0.8058,112,0.529,0,1,0,0,0,7,4,112,6,3,2,0,1,0,3,0,…,0
#:
#: 先頭 5 列(問題・正答肢・正答率・正答数・識別係数)に続く 32 個の度数。
DESIGN_Q1_VALUES: list[int] = [
    # a  b  c  d  e
    0,
    1,
    0,
    0,
    0,
    # ab ac  ad  ae bc bd be cd ce de
    7,
    4,
    112,
    6,
    3,
    2,
    0,
    1,
    0,
    3,
    # abc abd abe acd ace ade bcd bce bde cde
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    # abcd abce abde acde bcde
    0,
    0,
    0,
    0,
    0,
    # abcde
    0,
    # 空白
    0,
]

DESIGN_Q1_POSITION = 1
DESIGN_Q1_CORRECT = "ad"
DESIGN_Q1_P = 0.8058
DESIGN_Q1_N_CORRECT = 112
DESIGN_Q1_DISC = 0.529
DESIGN_Q1_N = 139

#: 設計書 §10.2 のメタ行。
DESIGN_META = {
    "試験名": "口腔組織学定期試験",
    "試験日": "2025-08-25",
    "受験者数": "139",
    "識別係数定義": "D_25",
}


def counts_from_row(values: list[int]) -> dict[str, int]:
    """31 パターン + 空白 の並び(設計書 §10.2 の列順)を度数辞書にする。"""
    if len(values) != len(PATTERNS) + 1:
        raise ValueError(f"32 個の度数が必要です(現在 {len(values)} 個)")
    counts = dict(zip(PATTERNS, values[:-1]))
    counts[BLANK] = values[-1]
    return {k: v for k, v in counts.items() if v}
