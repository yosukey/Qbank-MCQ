"""Releases に添える ``version.json`` を作る(実装計画 M7-5)。

    python tools/make_version_json.py dist/version.json --version 0.2.0 \
        --notes "統計レポートの層別を追加。スキーマ変更なし"

``--version`` を省くと ``qbank_mcq.__version__`` を使う。スキーマ版は
``core.migrate.TARGET_VERSION`` から自動で入る。**リリースノートにスキーマ変更の
有無を必ず明記する**(実装計画 §8)ための欄であり、手で書き換えるものではない。

CI から呼ぶ前提だが、手で作っても同じものができる。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qbank_mcq import __version__  # noqa: E402
from qbank_mcq.core.migrate import TARGET_VERSION  # noqa: E402
from qbank_mcq.core.updates import VersionInfo  # noqa: E402

RELEASE_URL = "https://github.com/yosukey/Qbank-MCQ/releases/latest"


def build(version: str, notes: str | None, url: str) -> VersionInfo:
    return VersionInfo(
        version=version.lstrip("v"),
        url=url,
        notes=notes,
        schema_version=TARGET_VERSION,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="version.json を作る")
    parser.add_argument("out", nargs="?", default="dist/version.json")
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--notes")
    parser.add_argument("--url", default=RELEASE_URL)
    args = parser.parse_args(argv)

    info = build(args.version, args.notes, args.url)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(info.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"書き出しました: {out}(v{info.version}, スキーマ版 {info.schema_version})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
