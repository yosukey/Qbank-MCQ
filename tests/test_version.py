"""バージョンの取り回しのテスト(実装計画 §8)。

守りたいのは一点だけ:**リリースタグ `v0.3.0` を打ったら、窓の表示もインストーラの
ファイル名も `0.3.0` になる**。タグ → `version.py` → 窓 / インストーラ名の各段を、
Windows も PyInstaller も無い環境で確かめる。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from itembank import __version__
from itembank.ui.about import about_rows, version_label, window_title
from itembank.version import (
    VERSION,
    InvalidTagError,
    installer_filename,
    numeric_version,
    version_from_tag,
)

ROOT = Path(__file__).resolve().parent.parent


def load_stamp_module():
    """``tools/stamp_version.py`` を読み込む(``tools/`` はパッケージではない)。"""
    path = ROOT / "tools" / "stamp_version.py"
    spec = importlib.util.spec_from_file_location("stamp_version", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- タグの読み取り ----------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("refs/tags/v0.3.0", "0.3.0"),  # ワークフローは github.ref をそのまま渡す
        ("v0.3.0", "0.3.0"),
        ("v1.0.0", "1.0.0"),
        ("v10.20.30", "10.20.30"),
        ("v0.3.0-rc1", "0.3.0-rc1"),  # 事前公開版
        (" v0.3.0 ", "0.3.0"),
    ],
)
def test_version_comes_from_the_tag(tag: str, expected: str) -> None:
    assert version_from_tag(tag) == expected


@pytest.mark.parametrize(
    "tag",
    [
        "0.3.0",  # v が無い
        "v0.3",  # パッチ番号が無い
        "v0.3.0.1",  # 4 つ目がある
        "release-0.3.0",
        "refs/heads/main",  # ブランチ push で誤って走らせた場合
        "v",
        "",
    ],
)
def test_a_tag_of_the_wrong_shape_is_refused(tag: str) -> None:
    """書式違反はビルド前に止める。exe を作ってから気づくと作り直しになる。"""
    with pytest.raises(InvalidTagError):
        version_from_tag(tag)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.3.0", "0.3.0.0"),
        ("1.2.3", "1.2.3.0"),
        ("0.3.0-rc1", "0.3.0.0"),  # Windows の版番号は数値 4 つだけ
    ],
)
def test_numeric_version_for_windows(version: str, expected: str) -> None:
    assert numeric_version(version) == expected
    assert len(numeric_version(version).split(".")) == 4


# -- インストーラのファイル名 ------------------------------------------------


def test_installer_filename_embeds_the_version() -> None:
    assert installer_filename("0.3.0") == "ItemBank-0.3.0-setup.exe"
    assert installer_filename() == f"ItemBank-{VERSION}-setup.exe"


def test_the_iss_builds_the_same_filename() -> None:
    """Inno Setup 側の組み立てが ``installer_filename`` とずれていないこと。"""
    iss = (ROOT / "packaging" / "itembank.iss").read_text(encoding="utf-8")
    matched = re.search(r"^OutputBaseFilename=(.+)$", iss, re.MULTILINE)
    assert matched is not None, "OutputBaseFilename が見つかりません"
    rendered = (
        matched.group(1).strip().replace("{#AppName}", "ItemBank").replace("{#AppVersion}", "0.3.0")
    )
    assert f"{rendered}.exe" == installer_filename("0.3.0")


# -- 窓の表示 ----------------------------------------------------------------


def test_the_window_shows_the_version() -> None:
    assert window_title("0.3.0") == "ItemBank 0.3.0"
    assert "0.3.0" in version_label("0.3.0")
    rows = dict(about_rows(version="0.3.0"))
    assert rows["バージョン"] == "0.3.0"


def test_the_window_defaults_to_the_stamped_version() -> None:
    assert window_title().endswith(VERSION)
    assert __version__ == VERSION


def test_about_rows_survive_a_database_that_was_never_opened() -> None:
    """DB を開く前でも窓は出す(移行に失敗したときの手がかりになるため)。"""
    rows = dict(about_rows())
    assert rows["スキーマ版"] == "—"
    assert rows["DB"] == "—"


def test_about_rows_show_the_bank_at_a_glance() -> None:
    rows = dict(
        about_rows(
            schema_version=3,
            data_dir=Path("/tmp/data"),
            db_path=Path("/tmp/data/itembank.sqlite"),
            counts={"問題": 120},
            frozen=True,
        )
    )
    assert rows["スキーマ版"] == "3"
    assert rows["実行形態"] == "配布 exe"
    assert rows["問題"] == "120"


# -- 焼き込み ----------------------------------------------------------------


def test_stamping_rewrites_only_the_version_line(tmp_path: Path) -> None:
    stamp_version = load_stamp_module()
    source = ROOT / "src" / "itembank" / "version.py"
    copy = tmp_path / "version.py"
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    stamped = stamp_version.stamp("0.3.0", path=copy)

    assert 'VERSION = "0.3.0"' in stamped
    assert copy.read_text(encoding="utf-8") == stamped
    before = source.read_text(encoding="utf-8").splitlines()
    after = stamped.splitlines()
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1, "VERSION の行以外が書き換わっています"

    assert re.search(r'^VERSION = "0\.3\.0"$', stamped, re.MULTILINE)


def test_stamping_is_idempotent(tmp_path: Path) -> None:
    stamp_version = load_stamp_module()
    copy = tmp_path / "version.py"
    copy.write_text((ROOT / "src" / "itembank" / "version.py").read_text("utf-8"), "utf-8")
    once = stamp_version.stamp("0.3.0", path=copy)
    assert stamp_version.stamp("0.3.0", path=copy) == once


def test_stamping_a_file_without_the_assignment_is_an_error(tmp_path: Path) -> None:
    stamp_version = load_stamp_module()
    broken = tmp_path / "version.py"
    broken.write_text("VERSION = '0.1.0'\n", encoding="utf-8")  # 引用符が違う
    with pytest.raises(SystemExit):
        stamp_version.stamp("0.3.0", path=broken)


def test_the_cli_reports_what_the_workflow_needs(capsys: pytest.CaptureFixture[str]) -> None:
    """``--check`` は書き換えずに、後続ステップが読む値だけを出す。"""
    stamp_version = load_stamp_module()
    source = ROOT / "src" / "itembank" / "version.py"
    before = source.read_text(encoding="utf-8")

    assert stamp_version.main(["--ref", "refs/tags/v0.3.0", "--check"]) == 0

    printed = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert printed == {
        "version": "0.3.0",
        "numeric_version": "0.3.0.0",
        "installer": "ItemBank-0.3.0-setup.exe",
        "tag": "v0.3.0",
    }
    assert source.read_text(encoding="utf-8") == before


def test_the_cli_refuses_a_bad_tag() -> None:
    """ブランチ名を渡すなど、リリース以外で走らせたときに黙って 0.0.0 を作らない。"""
    stamp_version = load_stamp_module()
    assert stamp_version.main(["--ref", "refs/heads/main", "--check"]) == 2
