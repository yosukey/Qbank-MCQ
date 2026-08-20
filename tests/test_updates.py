"""更新の確認と配布物の定義(実装計画 M7)。

自動更新はしない。**「新しい版が出ている」と伝えるところまで**(M7-5)。
確認に失敗してもアプリを止めないことを反例側で押さえる。
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from itembank import __version__
from itembank.core.migrate import TARGET_VERSION
from itembank.core.updates import (
    UpdateCheckError,
    VersionInfo,
    check_for_update,
    fetch_version_info,
    is_newer,
    version_key,
)

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT / "packaging"


def _opener(payload: bytes | Exception):
    """``urlopen`` の代わり。``BytesIO`` はそのまま ``with`` で使える。"""

    def open_it(url, timeout=None):  # noqa: ARG001 - 署名だけ合わせる
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(payload)

    return open_it


def _json(**kwargs) -> bytes:
    return json.dumps(kwargs, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# 版番号の比較
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.1.0", (0, 1, 0)), ("v0.10.0", (0, 10, 0)), ("1.0", (1, 0)), ("", ())],
)
def test_version_key(text: str, expected: tuple[int, ...]) -> None:
    assert version_key(text) == expected


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.2.0", "0.1.0", True),
        ("0.10.0", "0.9.0", True),  # 文字列比較なら取り違える組
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        ("なにこれ", "0.1.0", False),  # 読めない値で「新しい」と言わない
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert is_newer(latest, current) is expected


# ---------------------------------------------------------------------------
# version.json
# ---------------------------------------------------------------------------


def test_fetch_reads_the_fields() -> None:
    info = fetch_version_info(
        "http://example/version.json",
        opener=_opener(_json(version="0.3.0", url="https://x", notes="めも", schema_version=2)),
    )
    assert info == VersionInfo("0.3.0", "https://x", "めも", 2)


def test_fetch_rejects_a_broken_payload() -> None:
    with pytest.raises(UpdateCheckError):
        fetch_version_info("http://example", opener=_opener("{ こわれている".encode()))


def test_fetch_rejects_a_missing_version() -> None:
    with pytest.raises(UpdateCheckError):
        fetch_version_info("http://example", opener=_opener(_json(notes="版が無い")))


def test_network_failure_is_not_an_update(caplog) -> None:
    """繋がらないことは「更新が無い」ことではない。**が、止める理由でもない。**"""
    latest, message = check_for_update(
        "0.1.0", opener=_opener(OSError("名前解決に失敗")), url="http://example"
    )
    assert latest is None
    assert "確認できませんでした" in message


def test_check_reports_a_newer_version() -> None:
    latest, message = check_for_update(
        "0.1.0",
        url="http://example",
        opener=_opener(_json(version="0.2.0", schema_version=3, notes="スキーマ変更あり")),
    )
    assert latest is not None and latest.version == "0.2.0"
    assert "新しい版があります" in message
    # 実装計画 §8: スキーマ変更の有無を必ず伝える。
    assert "スキーマ版 3" in message
    assert "スキーマ変更あり" in message


def test_check_says_up_to_date() -> None:
    latest, message = check_for_update(
        "0.2.0", url="http://example", opener=_opener(_json(version="0.2.0"))
    )
    assert latest is None
    assert "最新版" in message


def test_generator_writes_the_current_schema_version(tmp_path: Path) -> None:
    """``version.json`` のスキーマ版は手で書かない(``TARGET_VERSION`` から入る)。"""
    import sys

    sys.path.insert(0, str(PROJECT / "tools"))
    from make_version_json import main as generate

    out = tmp_path / "version.json"
    assert generate([str(out), "--version", "v9.9.9", "--notes", "めも"]) == 0

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == "9.9.9", "先頭の v は落とす"
    assert data["schema_version"] == TARGET_VERSION
    assert data["notes"] == "めも"
    assert VersionInfo.from_dict(data).version == "9.9.9"


def test_generator_defaults_to_the_package_version(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(PROJECT / "tools"))
    from make_version_json import main as generate

    out = tmp_path / "version.json"
    generate([str(out)])
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == __version__


# ---------------------------------------------------------------------------
# 配布物の定義(実装計画 M7-1, M7-2)
# ---------------------------------------------------------------------------


def test_spec_excludes_unused_qt_modules() -> None:
    """未使用 Qt モジュールを exclude する(設計書 §15)。"""
    spec = (PACKAGING / "itembank.spec").read_text(encoding="utf-8")
    for module in ("QtWebEngineCore", "QtQuick", "Qt3DRender", "QtMultimedia"):
        assert module in spec

    # 落とすと起動しないものを除外していない。
    for needed in ("PySide6.QtWidgets", "PySide6.QtGui", "PySide6.QtCore"):
        assert f'"{needed}",' not in spec


def test_spec_builds_onedir_without_upx() -> None:
    """onefile は起動が遅く誤検知も多い(実装計画 §11)。UPX も使わない。"""
    spec = (PACKAGING / "itembank.spec").read_text(encoding="utf-8")
    assert "COLLECT(" in spec
    assert "exclude_binaries=True" in spec
    assert "upx=False" in spec
    assert "console=False" in spec


def test_spec_entry_point_keeps_the_package_context() -> None:
    """``app.py`` を直接渡すと相対 import が解けない。薄い入口を経由する。"""
    spec = (PACKAGING / "itembank.spec").read_text(encoding="utf-8")
    assert 'SPEC_DIR / "entry.py"' in spec

    entry = (PACKAGING / "entry.py").read_text(encoding="utf-8")
    assert "from itembank.app import main" in entry


def test_installer_is_per_user_and_keeps_the_data() -> None:
    """ユーザー単位・管理者権限なし。アンインストールでデータを消さない(設計書 §15)。"""
    iss = (PACKAGING / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss
    assert r"DefaultDirName={localappdata}\Programs\{#AppName}" in iss
    assert (
        "{userappdata}\\ItemBank"
        not in re.sub(r"(?m)^\s*;.*$", "", iss).split("[UninstallDelete]")[1].split("[Code]")[0]
    )


def test_release_workflow_runs_tests_before_building() -> None:
    """テストが落ちたら配布物を作らない(実装計画 §8)。"""
    workflow = (PROJECT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert workflow.index("pytest -q") < workflow.index("pyinstaller --noconfirm")
    assert "requirements.lock" in workflow
    assert "ISCC.exe" in workflow
    assert "version.json" in workflow
