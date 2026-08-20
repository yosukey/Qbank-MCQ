"""docx の抽出結果をゴールデンファイルとして固定する(実装計画 §4 M3 受入条件)。

    python tools/update_golden.py testdata/exam_2025.docx
    python tools/update_golden.py testdata/*.docx
    python tools/update_golden.py --check testdata/exam_2025.docx   # 差分を見るだけ

``FILE.docx`` の隣に ``FILE.golden.json`` を書く。``testdata/`` 直下に置いた組は
``tests/test_pipeline.py::test_real_docx_matches_golden`` が自動的に回帰対象にする。

**パーサを直したら、まず ``--check`` で差分を目で見てから更新すること。**
ゴールデンを無条件に上書きすると、退行を「正しい結果」として固定してしまう。
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from itembank.io.docx_read import parse_docx  # noqa: E402


def golden_path(docx: Path) -> Path:
    return docx.with_suffix(".golden.json")


def render(docx: Path) -> str:
    """ゴールデンの本文。テストと同じ正規化(sort_keys)で作る。"""
    data = parse_docx(docx).as_dict()
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def summarize(docx: Path) -> str:
    parsed = parse_docx(docx)
    lines = [
        f"  設問数: {len(parsed.questions)}",
        f"  読み飛ばした体裁行: {len(parsed.skipped)}",
        f"  想定外の書式: {len(parsed.unexpected_formats)} 箇所",
    ]
    blocking = [i for i in parsed.issues if i.blocking]
    warnings = [i for i in parsed.issues if not i.blocking]
    lines.append(f"  不整合: {len(blocking)} 件 / 警告: {len(warnings)} 件")
    for issue in blocking[:10]:
        lines.append(f"    [ブロック] {issue.message}")
    for issue in warnings[:10]:
        lines.append(f"    [警告]     {issue.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="docx の抽出結果ゴールデンを更新する")
    parser.add_argument("files", nargs="+", help="対象の .docx")
    parser.add_argument(
        "--check", action="store_true", help="書き換えず、既存ゴールデンとの差分だけ出す"
    )
    args = parser.parse_args(argv)

    changed = 0
    for name in args.files:
        docx = Path(name)
        if not docx.exists():
            print(f"{docx}: ファイルがありません", file=sys.stderr)
            return 2

        new = render(docx)
        target = golden_path(docx)
        old = target.read_text(encoding="utf-8") if target.exists() else ""

        print(f"{docx}")
        print(summarize(docx))

        if old == new:
            print("  ゴールデンに差分はありません")
            continue

        changed += 1
        if args.check:
            print(f"  --- 差分 ({target.name}) ---")
            diff = difflib.unified_diff(
                old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=2
            )
            for line in list(diff)[:120]:
                print(f"  {line}")
            continue

        target.write_text(new, encoding="utf-8")
        print(f"  {'更新' if old else '作成'}: {target}")

    if args.check and changed:
        print(
            f"\n{changed} 件に差分があります。内容を確認してから --check なしで実行してください。"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
