# testdata

実装計画 §6 が `testdata/` に置くものとして挙げているのは、2025年度問題 docx・集計 CSV・
抽出結果ゴールデン JSON・壊した CSV 数種。

```
testdata/
├─ exam_2025.docx          問題 docx(実データ)
├─ item_stats_2025.csv     採点後の集計 CSV(実データ・設計書 §10.2 の書式)
├─ exam_2025.golden.json   抽出結果の固定。tools/update_golden.py が作る
├─ legacy/                 旧版スキーマの DB ファイル。移行テストが総当たりする
└─ sample/                 実データの代わりの自動生成物。直接編集しない
```

## 実データの置き方

**`testdata/` 直下に置く。** 回帰テスト `test_real_docx_matches_golden` は
`testdata/*.docx` を直下だけ見るので、サブディレクトリでは拾われない。

ゴールデンの作成:

```bash
python tools/update_golden.py testdata/exam_2025.docx
```

## 集計 CSV の形

設計書 §10.2 の書式であること。**設問ごとの度数**であり、受験者ごとの回答一覧ではない。

```csv
#試験名,口腔組織学定期試験
#試験日,2025-08-25
#受験者数,139
#識別係数定義,D_25
問題,正答肢,正答率,正答数,識別係数,a,b,c,d,e,ab,...,abcde,空白
1,ad,0.8058,112,0.529,0,1,0,0,0,7,...,0,0
```

- UTF-8 BOM 付き・CRLF
- パターン列は 31 列 + `空白` の計 32 列
- 値はすべて人数(整数)。割合で渡すと検証チェーンがブロックする

形が違う場合は `itembank import-stats --dry-run` が何が足りないかを名指しで報告する。

## sample/

`tools/make_sample_data.py` が乱数種を固定して生成したもの。実データが無くても
CLI とパイプライン全体を通せるようにするためのもので、内容は実在の設問ではない。

```bash
python tools/make_sample_data.py
```

`broken_*.csv` は検証チェーン(設計書 §9.2)を確実に踏ませるためのわざと壊した CSV。

## 注意

問題 docx と集計 CSV は試験の実物である。リポジトリは private のままにすること
(実装計画 §1)。集計 CSV は設問ごとの度数であり個人が特定できる情報は含まないが、
**受験者ごとの回答・得点が並んだファイルはこのシステムの入力ではなく、
リポジトリに置く必要もない。**
