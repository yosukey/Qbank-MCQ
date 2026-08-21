# 配布物のビルド(実装計画 M7)

```
packaging/
├─ entry.py          凍結ビルドの入口(パッケージとして import するだけ)
├─ qbank-mcq.spec    PyInstaller(onedir)
├─ qbank-mcq.iss     Inno Setup(ユーザー単位インストール)
└─ qbank-mcq.ico     アイコン(任意。無ければアイコンなしで通る)
```

実装計画 §1 の構成図は `build/` としているが、そこは PyInstaller の作業ディレクトリで
`.gitignore` の対象でもあるため、**定義ファイルは `packaging/` に置く**。

## Windows で作る

```powershell
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-build.lock pytest
.venv\Scripts\pytest -q

.venv\Scripts\python tools\stamp_version.py --version 0.3.0
.venv\Scripts\pyinstaller packaging\qbank-mcq.spec --noconfirm --clean
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.3.0 /DNumericVersion=0.3.0.0 packaging\qbank-mcq.iss
```

`dist\installer\Qbank-MCQ-0.3.0-setup.exe` ができる。タグを push すれば GitHub Actions が
同じ手順を回す(`.github/workflows/release.yml`)。**版番号の正はタグ**なので、手で
`version.py` を書き換えず `tools/stamp_version.py` を通す。

## 落としているもの、落としていないもの

Python モジュールを `excludes` に挙げるだけでは**共有ライブラリとプラグインは残る**。
PySide6 のフックが Qt を丸ごと集めてくるため、spec は実体(`Qt6Quick` など)・
プラグインディレクトリ・翻訳も名指しで落としている。

**matplotlib と numpy は落とさない。** 問題詳細と選択肢セットの図(設計書 §14-3)が
使っており、外すと画面を開いた瞬間に ImportError になる。exe を作るまで出ない不具合
なので、`tests/test_updates.py::test_spec_keeps_matplotlib_for_the_charts` で見張って
いる。同様に `platforms` / `styles` / `imageformats` / `iconengines` の各プラグインは
落とすと起動しない。

## どこまで確かめたか

| | 状況 |
|---|---|
| spec が通り、onedir が生成される | **確認済み**(Linux) |
| 凍結したアプリが起動し、DB を作って移行する | **確認済み**(Linux, offscreen) |
| 凍結したアプリの中で matplotlib が読める | **確認済み**(Linux。font_manager の初期化までログに出る) |
| 未使用 Qt と翻訳を落としても起動する | **確認済み**(Linux) |
| Windows での起動・インストール・上書き・移行 | **未確認**(実機が要る) |
| SmartScreen の警告の出かた・署名の要否 | **未確認**(設計書 §15 が早期に決めろとしている項目) |

CI の `凍結した exe が起動するか` は windows-latest で起動だけ確かめる。
**別 PC(開発環境なし)での確認は自動化できない**ので、実装計画 M7-4 の実機確認は
手で行う必要がある。チェックリスト:

1. 開発環境のない PC にインストーラを実行 → 管理者権限を求められないこと
2. 起動 → `%APPDATA%\Qbank-MCQ` に DB・ログができること
3. docx 取込・統計取込・冊子出力が通ること
4. 旧版がある PC に上書きインストール → データが残り、スキーマ移行が走ること
5. アンインストール → `%APPDATA%\Qbank-MCQ` が残ること

## サイズ

onedir で 260 MB 前後(Linux 実測)。内訳の大半は Qt(ICU を含む)・numpy・matplotlib。
**UPX は使わない。** 数十 MB のために誤検知の危険を増やす取引は割に合わない
(実装計画 §11)。インストーラは lzma2/max で圧縮するので、配布物はこれより小さくなる。

## アイコン

`packaging/qbank-mcq.ico` を置くと exe とインストーラに使われる。無い状態でもビルドは
通る(spec が存在を見てから渡している)。
