# Qbank-MCQ — 口腔組織学 試験問題バンクシステム

[`exam_item_bank_design_v16.md`](exam_item_bank_design_v16.md)(設計書)と
[`exam_item_bank_implementation_plan.md`](exam_item_bank_implementation_plan.md)(実装計画)に
基づく実装。

実装計画 §0 の開発方針どおり、**ロジック層を先に作り、GUI は後**。各機能はまず CLI
サブコマンドとして動く。

## いまできること

`qbank` の CLI で、設計書 §1 の運用サイクルが**一周する**。

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
.venv/bin/pip install -e ".[dev]"       # GUI も動かすなら ".[dev,gui]"
.venv/bin/pytest -q
```

サンプルデータ(実データの代わり)を作る:

```bash
.venv/bin/python tools/make_sample_data.py
```

## 使い方

```bash
qbank db init                       # DB 作成・スキーマ移行
qbank inspect-docx FILE             # 全 run の書式ダンプ(スパイク①)

# 局面A: 過去問一括取込
qbank import-exam --docx 問題.docx --stats 集計.csv --dry-run --json out.json
qbank import-exam --docx 問題.docx --stats 集計.csv

# 出題支援
qbank bank                          # 問題一覧(正答率はタイプと併記)
qbank select --total 50 --type A=30 --type X2=15 --new-ratio 0.2 \
             --exclude-recent-years 2 --year 2026 --create-exam 定期試験2026
qbank finalize --exam 2 --check-only
qbank finalize --exam 2
qbank export --exam 2 --what all

# 局面B: 統計取込
qbank import-stats --exam 2 --csv 集計.csv --dry-run
qbank import-stats --exam 2 --csv 集計.csv
```

終了コードは `0`=成功 / `1`=検証で止めた / `2`=使い方の誤り。

ユーザーデータは `%APPDATA%\Qbank-MCQ\`(Windows 以外は XDG 準拠)に置かれる。
`QBANK_MCQ_DATA_DIR` で上書きできる。

GUI はまだ起動確認用の窓だけ(画面 10 種は M4)。バージョン・DB の所在・件数が出る。

```bash
qbank-gui            # = python -m qbank_mcq.app
```

## モジュール構成

実装計画 §5 のとおり。**`core/` は `ui/` と `io/` に依存しない。**

```
src/qbank_mcq/
├─ __main__.py            CLI エントリ
├─ app.py                 GUI エントリ(ログ初期化 → DB 移行 → 窓)
├─ version.py             バージョン(正はリリースタグ — 下記「リリース」)
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
└─ ui/
   ├─ about.py            窓に出す文言(Qt に依存しない ← テストから直接叩ける)
   └─ main_window.py      起動確認用の窓(画面 10 種は M4)
```

配布まわりは `packaging/` にある。

```
packaging/
├─ entry.py               PyInstaller の入口(app.py を package として import する皮)
├─ qbank-mcq.spec         PyInstaller spec(onedir・未使用 Qt モジュールを exclude)
└─ qbank-mcq.iss          Inno Setup(ユーザー単位インストール・データ保持)
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
| §9.2 検証チェーン | `core/validate.py` `validate_stats_import` | `test_validate.py` |
| §10 連携仕様(BOM + CRLF) | `io/csv_key.py`, `io/csv_stats.py` | `test_csv_io.py`, `test_csv_real.py` |
| §11 問題タイプ | `core/typing_rules.py` | `test_typing_rules.py` |
| §12 導出指標と自動フラグ | `core/stats.py` | `test_stats.py` |
| §13.1 選定条件 | `core/selection.py` | `test_selection.py` |
| §13.3 finalize 前チェック | `core/validate.py` `finalize_checks` | `test_validate.py`, `test_exam.py` |
| §2.2 改訂と派生 | `core/bank.py` | `test_bank.py` |

## テスト

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests tools packaging
.venv/bin/black --check src tests tools packaging
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
qbank inspect-docx testdata/exam_2025.docx

# 2. 登録せずに抽出と相互検証だけ回す。50 問すべてが取れて不整合 0 件になるまで直す
qbank import-exam --docx testdata/exam_2025.docx \
                     --stats testdata/item_stats_2025.csv \
                     --dry-run --json /tmp/dry.json

# 3. 問題なければ本登録
qbank import-exam --docx testdata/exam_2025.docx --stats testdata/item_stats_2025.csv

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
  Qbank-MCQ 側の試験に現れる出題番号は連続しない

v15 の書式(メタ行つき・`正答率` が 0〜1・`空白` 列)も引き続き読める。旧データの
取り込みに備えて残してあり、`io/csv_stats.py` が方言を自動判定する。

## リリース(Windows インストーラ)

**バージョン番号の正はリリースタグ。** タグを打って push すると
`.github/workflows/release.yml`(windows-latest)が動く。

```bash
git tag v0.3.0
git push origin v0.3.0
```

```
タグ v0.3.0
  └─ tools/stamp_version.py が src/qbank_mcq/version.py の VERSION を 0.3.0 に書き換える
       ├─ pytest(Windows 実機で)
       ├─ PyInstaller (onedir)  → dist/Qbank-MCQ/Qbank-MCQ.exe  … プロパティも 0.3.0
       ├─ Inno Setup            → dist/installer/Qbank-MCQ-0.3.0-setup.exe
       └─ Release に添付
```

同じ 0.3.0 が次の 3 か所に出る。ワークフローはビルドの途中でこれを突き合わせ、
食い違ったら失敗する。

| どこ | 見え方 |
|---|---|
| アプリウィンドウ | タイトル `Qbank-MCQ 0.3.0` と本文の「バージョン 0.3.0」 |
| インストーラ | ファイル名 `Qbank-MCQ-0.3.0-setup.exe` |
| exe / インストーラのプロパティ | 製品バージョン `0.3.0`(ファイルバージョンは `0.3.0.0`) |

そのため**バージョンを上げるためのコミットは要らない**。リポジトリの
`version.py` にある値は開発中の暫定値で、`v0.3.0` 以外の書式のタグ(`0.3.0` や
`release-0.3.0`)はビルド前に弾かれる。

インストーラの性質(実装計画 §8 / 設計書 §15):

- 入る場所は `%LOCALAPPDATA%\Programs\Qbank-MCQ`。**管理者権限を求めない**
- **`%APPDATA%\Qbank-MCQ` には触らない**。上書きインストールでもアンインストールでも
  DB・バックアップ・取込原本は残り、新しい版が起動時にスキーマ移行する
- リリースノートに対応スキーマ版を必ず書く(ワークフローが自動で入れる)

タグを打たずに試すときは Actions から `release` を手動実行する(バージョンを入力する。
Release は作らず、成果物は artifact に残る)。

## 未着手

| | 内容 | 備考 |
|---|---|---|
| **M4** | PySide6 の画面 10 種(設計書 §14) | いまの窓は起動確認用(実装計画 §2.2 スパイク②)。ロジックはすべて CLI から呼べる形で揃っている |
| **M6 の一部** | matplotlib による可視化(設計書 §14-3) | 集計値は `core/reporting.py` が算出済み |
| **M7 の実機確認** | 別 PC でのインストール・起動・DB 移行、署名の要否判断 | ビルドは CI で通るが、Windows 実機での確認はまだ |

`.github/workflows/test.yml` は lint とテストのみを回す(ブランチへの push と PR)。
リリースパイプラインは `release.yml` で、タグ `v*` の push でだけ動く。
