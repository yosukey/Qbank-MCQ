# 口腔組織学 試験問題バンクシステム

[`exam_item_bank_design_v16.md`](exam_item_bank_design_v16.md)(設計書)と
[`exam_item_bank_implementation_plan.md`](exam_item_bank_implementation_plan.md)(実装計画)に
基づく実装。

実装計画 §0 の開発方針どおり、**ロジック層を先に作り、GUI は後**。各機能はまず CLI
サブコマンドとして動き、画面はそれと同じ関数を呼ぶ。

## いまできること

CLI でも画面でも、設計書 §1 の運用サイクルが**一周する**。

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
.venv/bin/pip install -e ".[dev,gui]"   # GUI が要らなければ ".[dev]" だけでよい
.venv/bin/pytest -q
```

GUI は任意依存である。PySide6 が無い環境では GUI のテストが skip され、CLI だけで
運用サイクルは一周する(実装計画 §0)。Linux で動かすときは Qt の共有ライブラリ
(`libegl1` `libgl1` `libxkbcommon0` など)が要る。

サンプルデータ(実データの代わり)を作る:

```bash
.venv/bin/python tools/make_sample_data.py
```

## 使い方

### 画面(設計書 §14)

```bash
itembank gui            # または python -m itembank.app
```

タブは設計書 §14 の番号順。問題編集(§14-2)と問題詳細(§14-3)は一覧から開く
ダイアログ。

| タブ | 設計書 | 中身 |
|---|---|---|
| 問題バンク | §14-1 | フィルタ付き一覧。新規作成・複製作成(派生)・編集・詳細 |
| 選択肢セット | §14-4 | セット一覧・近似リンク・設問×項目のマーク率・統合・監査 |
| 選択肢アイテム | §14-5 | 用語単位の実績(§6.5) |
| 過去問一括取込 | §14-6 | 局面A。書式付きプレビュー → 目視確認 → 一括登録 |
| 試験セット | §14-7 | 選定 → 差し替え → finalize 前チェック → 確定 |
| 出力 | §14-8 | 冊子 docx / 正答キー / 照合表 / 統計レポート |
| 統計取込 | §14-9 | 局面B。試験を選ぶ → 検証チェーン → 確定 → フラグ一覧 |
| 設定 | §14-10 | タグ・フラグ閾値・近似リンク閾値・基準フォント・否定語・バックアップ |

**局面A(過去問一括取込)と局面B(統計取込)はタブが分かれている。** 取り違えると
同じ問題が二重登録される(設計書 §1.4)。統計取込のタブには問題を作る導線が無い。

### CLI

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
├─ app.py                 GUI エントリ
├─ core/
│  ├─ paths.py            %APPDATA% 解決、ログ
│  ├─ config.py           設定の永続化(設計書 §14-10)
│  ├─ db.py               SQLAlchemy モデル(設計書 §8)
│  ├─ migrate.py          スキーマ移行(通し番号方式・自動バックアップ)
│  ├─ text.py             正規化・均等割・タグ処理          ← 純関数
│  ├─ typing_rules.py     タイプ導出・正答個数検証・強調規則  ← 純関数
│  ├─ choiceset.py        署名・類似・リンク・並び順          ← 純関数
│  ├─ stats.py            度数からの導出・フラグ判定          ← 純関数
│  ├─ selection.py        出題候補選定                        ← 純関数
│  ├─ validate.py         検証チェーン                        ← 純関数
│  ├─ bank.py             新規作成・改訂・派生の 3 経路、セットの統合
│  ├─ exam.py             試験の組み立て・finalize・統計付与
│  ├─ importer.py         一括取込の手順(CLI と画面で共用)
│  └─ reporting.py        レポート・問題履歴・セットの読みモデル
├─ io/
│  ├─ docx_read.py        取込パーサ
│  ├─ docx_write.py       冊子出力
│  ├─ csv_stats.py        集計CSV
│  ├─ csv_key.py          正答キー
│  └─ xlsx_report.py      レポート・照合表
└─ ui/
   ├─ workspace.py        DB・セッション・設定を画面に配る
   ├─ richtext.py         HTML 断片 ⇔ QTextDocument、書式ボタン
   ├─ charts.py           matplotlib の 4 つの図(設計書 §14-3)
   ├─ common.py           表示の決まりごと(タグ除去・正答率の併記)
   ├─ main_window.py      タブ
   ├─ bank_view.py / question_editor.py / question_detail.py
   ├─ choiceset_view.py / item_view.py
   └─ import_view.py / exam_builder.py / export_view.py /
      stats_import.py / settings_view.py
```

計画 §5 の一覧に対し `bank.py` `exam.py` `importer.py` `reporting.py` を足してある。
改訂/派生の規則、finalize の組み立て、一括取込の手順は GUI から独立して検証できる
必要があり、`ui/` に置けないため。**画面と CLI は同じ関数を呼ぶ。** 手順を両側に
写すと、片方だけ直したときに登録内容が食い違う。

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
| §9.2 検証チェーン | `core/validate.py` `validate_stats_import` | `test_validate.py` |
| §10 連携仕様(BOM + CRLF) | `io/csv_key.py`, `io/csv_stats.py` | `test_csv_io.py`, `test_csv_real.py` |
| §11 問題タイプ | `core/typing_rules.py` | `test_typing_rules.py` |
| §12 導出指標と自動フラグ | `core/stats.py` | `test_stats.py` |
| §13.1 選定条件 | `core/selection.py` | `test_selection.py` |
| §13.3 finalize 前チェック | `core/validate.py` `finalize_checks` | `test_validate.py`, `test_exam.py` |
| §2.2 改訂と派生 | `core/bank.py` | `test_bank.py` |
| §14 画面構成(10 種) | `ui/` | `test_ui_*.py` |
| §14-2 保存形式のまま表示・編集 | `ui/richtext.py` | `test_ui_richtext.py` |
| §14-3 パターンの可視化 | `ui/charts.py` | `test_ui_bank.py` |
| §1.4 局面の取り違え防止 | `ui/main_window.py`(タブ分離) | `test_ui_workflow.py` |

## テスト

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests tools
.venv/bin/black --check src tests tools
```

実装計画 §6 のテスト戦略に沿って:

- **ユニット** — `core/` の純関数。正常系より境界と反例を厚く
- **ゴールデンファイル** — `testdata/sample/exam_2025.golden.json` と突き合わせ
- **往復テスト** — 取込 → 冊子出力 → 再取込で HTML が一致
- **異常系** — `testdata/sample/broken_*.csv` が確実にブロックされる
- **統合** — インメモリ SQLite で改訂/派生/finalize/統計取込を通す
- **画面** — 画面のない環境(`QT_QPA_PLATFORM=offscreen`)でウィジェットを組み立てて
  操作する。判断の要る部分(絞り込み・保存経路・検証)は純関数か core に寄せてあり、
  画面のテストは「その関数を正しく呼んでいるか」だけを見る

実装計画 §6 は GUI を手動チェックリストとしているが、offscreen で走るぶんは自動化した。
費用対効果が合わないのは見た目の確認だけである。

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

## 集計 CSV の形式

設計書 §10.2 は v16 で**採点システムの実物に合わせて改訂済み**
(`testdata/item_stats_2026_02.csv` が基準)。`io/csv_stats.py` はこの形式を正として読む。

```csv
問,配点,措置,正答,正答率(%),識別係数,点双列相関,a,b,…,abcde,無解答,その他
1,5,none,b,50.0,0.294,0.265,13,69,…,0,0,0
7,3,none,記述式,38.4,0.706,0.478,-,-,…,-,-,-
```

押さえておくべき点:

- **メタ行が無い** → 受験者数は度数合計から導出し、導出したことを警告で明示する
- **`正答率(%)` は 0〜100**、`正答数` 列は無い(正答パターン列から数える)
- **`その他` 列は N に算入する**。外すと受験者数と正答率がずれる。ただし
  マーク率・指示個数違反率には算入しない
- **記述式は統計の対象外**。バンクは 5 肢選択問題のみを扱う(設計書 §11.1)ため、
  ItemBank 側の試験に現れる出題番号は連続しない

v15 の書式(メタ行つき・`正答率` が 0〜1・`空白` 列)も引き続き読める。旧データの
取り込みに備えて残してあり、`io/csv_stats.py` が方言を自動判定する。

## 未着手

| | 内容 | 備考 |
|---|---|---|
| **M7** | PyInstaller spec / Inno Setup / リリース CI | Windows 実機がないと検証できないため未着手 |
| **M1 スパイク②** | PyInstaller の実機確認・署名の要否判断 | 同上 |
| M4 の残り | 画像の差し替え、印刷プレビュー、テンプレート差し替えの画面 | 冊子出力自体は動く |

`.github/workflows/test.yml` は lint とテストのみを回す。実装計画 §8 のリリース
パイプライン(タグ push → exe + インストーラ)は M7 で足す。
