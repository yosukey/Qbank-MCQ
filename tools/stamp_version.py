"""リリースタグのバージョンを ``src/itembank/version.py`` に焼き込む(実装計画 §8)。

``.github/workflows/release.yml`` が、exe をビルドする前にこれを一度だけ走らせる。

    python tools/stamp_version.py --ref refs/tags/v0.3.0   # タグ push から
    python tools/stamp_version.py --version 0.3.0          # 手動実行から
    python tools/stamp_version.py --ref refs/tags/v0.3.0 --check   # 書かずに確かめる

``VERSION = "…"`` の行だけを置き換える。こうしておくと、タグを打つたびに
バージョンをコミットして回る必要がなく、**タグ・窓の表示・インストーラのファイル名が
ずれない**。標準出力には決まった書式で

    version=0.3.0
    numeric_version=0.3.0.0
    installer=ItemBank-0.3.0-setup.exe

を出し、``GITHUB_OUTPUT`` があればそこにも同じものを書く(後続ステップが使う)。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from itembank.version import (  # noqa: E402
    InvalidTagError,
    installer_filename,
    numeric_version,
    version_from_tag,
)

VERSION_FILE = ROOT / "src" / "itembank" / "version.py"

#: 置換対象。行頭の ``VERSION = "…"`` ちょうど 1 行だけを狙う。
ASSIGNMENT_RE = re.compile(r'^VERSION = "[^"]*"$', re.MULTILINE)


def stamp(version: str, *, path: Path = VERSION_FILE, write: bool = True) -> str:
    """``version.py`` の ``VERSION`` を書き換え、書き換え後の全文を返す。"""
    source = path.read_text(encoding="utf-8")
    replaced, count = ASSIGNMENT_RE.subn(f'VERSION = "{version}"', source, count=1)
    if count != 1:
        raise SystemExit(f"{path} に 'VERSION = \"…\"' の行が見つかりません")
    if write:
        path.write_text(replaced, encoding="utf-8")
    return replaced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ref", help="refs/tags/v0.3.0 または v0.3.0")
    source.add_argument("--version", help="0.3.0(タグを打たない手動ビルド用)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="ファイルを書き換えず、バージョンの取り出しだけを確かめる",
    )
    args = parser.parse_args(argv)

    try:
        version = version_from_tag(args.ref) if args.ref else version_from_tag(f"v{args.version}")
        numeric = numeric_version(version)
    except InvalidTagError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    stamp(version, write=not args.check)

    outputs = {
        "version": version,
        "numeric_version": numeric,
        "installer": installer_filename(version),
        "tag": f"v{version}",
    }
    for key, value in outputs.items():
        print(f"{key}={value}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            for key, value in outputs.items():
                fh.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
