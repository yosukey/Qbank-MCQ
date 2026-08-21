"""アプリのバージョン(実装計画 §8「ビルドとリリース」)。

**正はリリースタグ**。``git tag v0.3.0 && git push --tags`` すると
``.github/workflows/release.yml`` が ``tools/stamp_version.py`` を呼び、下の
``VERSION`` をタグの値に書き換えてから exe とインストーラを作る。これで

- アプリウィンドウのタイトルと「情報」欄(``ui/about.py``)
- インストーラのファイル名(``Qbank-MCQ-0.3.0-setup.exe``)
- exe / インストーラの Windows ファイルプロパティ
- ``qbank --version``

がすべて同じ値になる。リポジトリに置いてある値は開発中の暫定値なので、タグを打つ
たびにコミットし直す必要はない。

このモジュールはビルド前(依存を入れる前)にも読めるよう、標準ライブラリと
``core.paths`` の定数以外に依存しない。
"""

from __future__ import annotations

import re

from .core.paths import APP_NAME

#: 開発中の暫定値。リリース時はタグの値で上書きされる(モジュール冒頭の説明を参照)。
VERSION = "0.1.0"

#: リリースタグの書式。``v`` + セマンティックバージョン。実装計画 §8 のとおり
#: ``0.x`` の間は互換性を保証しないため、``v0.3.0-rc1`` のような事前公開版も受ける。
TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)$")


class InvalidTagError(ValueError):
    """リリースタグの書式が想定と違う。"""


def version_from_tag(tag: str) -> str:
    """``refs/tags/v0.3.0`` または ``v0.3.0`` から ``0.3.0`` を取り出す。

    ワークフローは ``github.ref`` をそのまま渡してくるので、``refs/tags/`` が
    付いたままでも受け付ける。
    """
    name = tag.strip()
    if name.startswith("refs/tags/"):
        name = name[len("refs/tags/") :]
    matched = TAG_RE.match(name)
    if matched is None:
        raise InvalidTagError(
            f"リリースタグは v0.3.0 の形式で打ってください(受け取った値: {tag!r})"
        )
    return matched.group("version")


def numeric_version(version: str) -> str:
    """``0.3.0`` → ``0.3.0.0``。Windows のファイルバージョン欄用。

    exe のプロパティと Inno Setup の ``VersionInfoVersion`` は数値 4 つしか受け
    付けないため、``-rc1`` のような事前公開版の識別子は落とす。
    """
    core = version.split("-", 1)[0].split("+", 1)[0]
    parts = [p for p in core.split(".") if p != ""]
    if not parts or not all(p.isdigit() for p in parts):
        raise InvalidTagError(f"数値のバージョンとして読めません: {version!r}")
    parts = (parts + ["0", "0", "0"])[:4]
    return ".".join(parts)


def installer_filename(version: str | None = None) -> str:
    """インストーラのファイル名(実装計画 §8 の ``Qbank-MCQ-0.3.0-setup.exe``)。"""
    return f"{APP_NAME}-{version or VERSION}-setup.exe"
