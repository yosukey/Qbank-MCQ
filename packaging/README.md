# 配布物のビルド(実装計画 M7)

```
packaging/
├─ entry.py        凍結ビルドの入口(パッケージとして import するだけ)
├─ itembank.spec   PyInstaller(onedir)
├─ installer.iss   Inno Setup(ユーザー単位インストール)
└─ itembank.ico    アイコン(任意。無ければアイコンなしで通る)
```

実装計画 §1 の構成図は `build/` としているが、そこは PyInstaller の作業ディレクトリで
`.gitignore` の対象でもあるため、**定義ファイルは `packaging/` に置く**。

## Windows で作る

```powershell
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.lock pyinstaller
.venv\Scripts\pytest -q

.venv\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\itembank.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.2.0 packaging\installer.iss
```

`dist\ItemBank-0.2.0-setup.exe` ができる。タグを push すれば GitHub Actions が同じ手順を
回す(`.github/workflows/release.yml`)。

## どこまで確かめたか

| | 状況 |
|---|---|
| spec が通り、onedir が生成される | **確認済み**(Linux) |
| 凍結したアプリが起動し、DB を作って移行する | **確認済み**(Linux, offscreen) |
| 未使用 Qt を落としても起動する | **確認済み**(Linux。PySide6 が 119 MB → 99 MB) |
| Windows での起動・インストール・上書き・移行 | **未確認**(実機が要る) |
| SmartScreen の警告の出かた・署名の要否 | **未確認**(設計書 §15 が早期に決めろとしている項目) |

CI の `凍結した exe が起動するか` は windows-latest で起動だけ確かめる。
**別 PC(開発環境なし)での確認は自動化できない**ので、実装計画 M7-4 の実機確認は
手で行う必要がある。チェックリスト:

1. 開発環境のない PC にインストーラを実行 → 管理者権限を求められないこと
2. 起動 → `%APPDATA%\ItemBank` に DB・ログができること
3. docx 取込・統計取込・冊子出力が通ること
4. 旧版がある PC に上書きインストール → データが残り、スキーマ移行が走ること
5. アンインストール → `%APPDATA%\ItemBank` が残ること

## サイズ

onedir で 250 MB 前後(Linux 実測)。内訳の大半は Qt(ICU を含む)・numpy・matplotlib で、
設計書 §15 のとおり未使用 Qt モジュールとその共有ライブラリ・プラグイン・翻訳を
spec で落としてある。**UPX は使わない。** 数十 MB のために誤検知の危険を増やす取引は
割に合わない(実装計画 §11)。

インストーラは lzma2/max で圧縮するので、配布物はこれよりかなり小さくなる。

## アイコン

`packaging/itembank.ico` を置くと exe とインストーラに使われる。無い状態でもビルドは
通る(spec が存在を見てから渡している)。
