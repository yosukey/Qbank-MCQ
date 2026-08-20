# 口腔組織学 試験問題バンクシステム

[`exam_item_bank_design_v15.md`](exam_item_bank_design_v15.md)(設計書)と
[`exam_item_bank_implementation_plan.md`](exam_item_bank_implementation_plan.md)(実装計画)に
基づく実装。

実装計画 §0 の開発方針どおり、**ロジック層を先に作り、GUI は後**。各機能はまず CLI
サブコマンドとして動く。

## いまできること

`itembank` の CLI で、設計書 §1 の運用サイクルが**一周する**。

```
過去問docx ─┐
            ├─→ 相互検証 → バンクへ一括登録         (局面A / §1.1)
集計CSV ────┘
                     ↓
バンク ──選定──► 出題セット ──finalize──┬─► 問題冊子(.docx)
                                        ├─► 正答キー(.csv) ─► ss-database
                                        ├─► 教員用照合表(.xlsx)
                                        └─► 統計レポート(.xlsx)
                     ▲                                          │
                     └────── 検証チェーン ◄─── 集計CSV ◄─────────┘
                                                    (局面B / §1.2)
```

## セットアップ

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

サンプルデータ(実データの代わり)を作る:

```bash
.venv/bin/python tools/make_sample_data.py
```

## 使い方

```bash
itembank db init                       # DB 作成・スキーマ移行
itembank inspect-docx FILE             # 全 run の書式ダンプ(スパイク①)

# 局面A: 過去問一括取込
itembank import-exam --docx 問題.docx --stats 集計.csv --dry-run --json out.json
itembank import-exam --docx 問題.docx --stats 集計.csv

# 出題支援
itembank bank                          # 問題一覧(正答率はタイプと併記)
itembank select --total 50 --type A=30 --type X2=15 --new-ratio 0.2 \
                --exclude-recent-years 2 --year 2026 --create-exam 定期試験2026
itembank finalize --exam 2 --check-only
itembank finalize --exam 2
itembank export --exam 2 --what all

# 局面B: 統計取込
itembank import-stats --exam 2 --csv 集計.csv --dry-run
itembank import-stats --exam 2 --csv 集計.csv
```

終了コードは `0`=成功 / `1`=検証で止めた / `2`=使い方の誤り。

ユーザーデータは `%APPDATA%\ItemBank\`(Windows 以外は XDG 準拠)に置かれる。
`ITEMBANK_DATA_DIR` で上書きできる。

## モジュール構成

実装計画 §5 のとおり。**`core/` は `ui/` と `io/` に依存しない。**

```
src/itembank/
├─ __main__.py            CLI エントリ
├─ core/
│  ├─ paths.py            %APPDATA% 解決、ログ
│  ├─ db.py               SQLAlchemy モデル(設計書 §8)
│  ├─ migrate.py          スキーマ移行(通し番号方式・自動バックアップ)
│  ├─ text.py             正規化・均等割・タグ処理          ← 純関数
│  ├─ typing_rules.py     タイプ導出・正答個数検証・強調規則  ← 純関数
│  ├─ choiceset.py        署名・類似・リンク・並び順          ← 純関数
│  ├─ stats.py            度数からの導出・フラグ判定          ← 純関数
│  ├─ selection.py        出題候補選定                        ← 純関数
│  ├─ validate.py         検証チェーン                        ← 純関数
│  ├─ bank.py             新規作成・改訂・派生の 3 経路
│  ├─ exam.py             試験の組み立て・finalize・統計付与
│  └─ reporting.py        レポートの行モデルと層別集計
├─ io/
│  ├─ docx_read.py        取込パーサ
│  ├─ docx_write.py       冊子出力
│  ├─ csv_stats.py        集計CSV
│  ├─ csv_key.py          正答キー
│  └─ xlsx_report.py      レポート・照合表
└─ ui/                    (未実装 — M4)
```

計画 §5 の一覧に対し `bank.py` `exam.py` `reporting.py` を足してある。改訂/派生の規則や
finalize の組み立ては GUI から独立して検証できる必要があり、`ui/` に置けないため。

## 設計書のどこが、どこで守られているか

| 設計書 | 実装 | テスト |
|---|---|---|
| §3.1 許可タグ 4 種のホワイトリスト | `core/text.py` `sanitize_html` | `test_text.py` |
| §3.2 検索・パースはタグ除去後 | `core/text.py` `strip_tags` | `test_text.py` |
| §4 強調は否定形のときだけ | `core/typing_rules.py` `check_emphasis_rule` | `test_typing_rules.py` |
| §5.2 `w:eastAsia` を直接読む | `io/docx_read.py` `east_asia_font` | `test_docx_roundtrip.py` |
| §5.3 **往復一致** | `io/docx_write.py` | `test_docx_roundtrip.py`, `test_pipeline.py` |
| §6 選択肢セット(順序を持たない集合) | `core/choiceset.py` | `test_choiceset.py` |
| §7 均等割 | `core/text.py` `normalize_choice` / `render_choice` | `test_text.py` |
| §8 スキーマ | `core/db.py` | `test_migrate.py` |
| §9.2 検証チェーン 9 項目 | `core/validate.py` `validate_stats_import` | `test_validate.py` |
| §10 連携仕様(BOM + CRLF) | `io/csv_key.py`, `io/csv_stats.py` | `test_csv_io.py`, `test_csv_real.py` |
| §11 問題タイプ | `core/typing_rules.py` | `test_typing_rules.py` |
| §12 導出指標と自動フラグ | `core/stats.py` | `test_stats.py` |
| §13.1 選定条件 | `core/selection.py` | `test_selection.py` |
| §13.3 finalize 前チェック | `core/validate.py` `finalize_checks` | `test_validate.py`, `test_exam.py` |
| §2.2 改訂と派生 | `core/bank.py` | `test_bank.py` |

## テスト

```bash
.venv/bin/pytest -q          # 309 件
.venv/bin/ruff check src tests tools
.venv/bin/black --check src tests tools
```

実装計画 §6 のテスト戦略に沿って:

- **ユニット** — `core/` の純関数。正常系より境界と反例を厚く
- **ゴールデンファイル** — `testdata/sample/exam_2025.golden.json` と突き合わせ
- **往復テスト** — 取込 → 冊子出力 → 再取込で HTML が一致
- **異常系** — `testdata/sample/broken_*.csv` が確実にブロックされる
- **統合** — インメモリ SQLite で改訂/派生/finalize/統計取込を通す

### 実データを入れるとき

実装計画 §0 は 2025年度の問題 docx と集計 CSV をゴールデンファイルにするとしている。
それらのファイルはまだ無いので、同じ様式で組んだサンプルを
`tools/make_sample_data.py` が生成している。実ファイルが手に入ったら:

**置き場所は `testdata/` 直下**(`testdata/sample/` はサンプル専用なので混ぜない)。

```
testdata/
├─ exam_2025.docx          ← 問題 docx
├─ item_stats_2025.csv     ← 採点後の集計 CSV(設計書 §10.2)
├─ exam_2025.golden.json   ← 手順 4 で生成する
├─ legacy/                 ← 旧版スキーマの DB ファイル(実装計画 §7)
└─ sample/                 ← 自動生成のサンプル。手を入れない
```

`test_real_docx_matches_golden` は `testdata/*.docx` を直下だけ見るので、
サブディレクトリに置くと回帰対象にならない。

```bash
# 1. 想定外の書式がどれだけあるか目で見る(実装計画 §2.1 が最大のリスクとした点)
itembank inspect-docx testdata/exam_2025.docx

# 2. 登録せずに抽出と相互検証だけ回す。50 問すべてが取れて不整合 0 件になるまで直す
itembank import-exam --docx testdata/exam_2025.docx \
                     --stats testdata/item_stats_2025.csv \
                     --dry-run --json /tmp/dry.json

# 3. 問題なければ本登録
itembank import-exam --docx testdata/exam_2025.docx --stats testdata/item_stats_2025.csv

# 4. 抽出結果をゴールデンとして固定する。以後は pytest が回帰を見張る
python tools/update_golden.py testdata/exam_2025.docx
```

パーサに手を入れたあとは、**まず差分を目で見てから**ゴールデンを更新する。
無条件に上書きすると退行を「正しい結果」として固定してしまう。

```bash
python tools/update_golden.py --check testdata/exam_2025.docx
```

同様に、スキーマ版を上げるたびに旧版の DB ファイルを `testdata/legacy/` に 1 つ残すと、
`test_legacy_databases_migrate` が移行を検証する(実装計画 §7)。

なお `.gitignore` は `*.sqlite` を除外している。`testdata/legacy/` の旧 DB を
コミットするときは `git add -f` が要る。

## 設計書と実物が食い違っている点

設計書 §10.2 は集計 CSV の書式を規定しているが、**採点システムが実際に出す形は
これと違う**(`testdata/item_stats_2026_02.csv` で確認)。アプリ側は両方を読む
`io/csv_stats.py` の別名表で吸収してあるが、設計書自体は未改訂。

| 設計書 §10.2 | 実物 | アプリの扱い |
|---|---|---|
| `#試験名`〜`#受験者数` のメタ行 | 無い | 受験者数を度数合計から導出し、そのことを警告で明示 |
| `正答率` は 0〜1 | `正答率(%)` で 0〜100 | 見出しの `(%)` を見て換算 |
| `正答数` 列 | 無い | 正答パターン列から数える |
| `空白` | `無解答` | 別名として同一視 |
| — | `その他` | 31 パターンでも無回答でもない区分として N に算入 |
| 全問が 5 肢選択 | **30 問中 24 問が記述式** | 選択式でない行は統計の対象外。件数を報告 |
| — | `配点` `措置` `点双列相関` | 読むが保存しない。`措置` が `none` 以外なら警告 |

記述式をバンクに登録する経路は設けていない(選択肢セットと問題タイプが
5 肢選択を前提にしているため)。試験の記録としても残していないので、
**出題番号 7〜30 はアプリ側の試験には現れない。**

## 未着手

| | 内容 | 備考 |
|---|---|---|
| **M4** | PySide6 の画面 10 種(設計書 §14) | ロジックはすべて CLI から呼べる形で揃っている |
| **M6 の一部** | matplotlib による可視化(設計書 §14-3) | 集計値は `core/reporting.py` が算出済み |
| **M7** | PyInstaller spec / Inno Setup / リリース CI | Windows 実機がないと検証できないため未着手 |
| **M1 スパイク②** | PyInstaller の実機確認・署名の要否判断 | 同上 |

`.github/workflows/test.yml` は lint とテストのみを回す。実装計画 §8 のリリース
パイプライン(タグ push → exe + インストーラ)は M7 で足す。
