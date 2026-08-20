"""更新の確認(実装計画 M7-5)。

    バージョン通知(Releases の version.json を見るだけ)

自動更新はしない。**やることは「新しい版が出ているか」を伝えるところまで。**
入れ替えはインストーラに任せる。単独開発で自動更新まで抱えると、壊れたときに
戻す手立てまで作ることになる。

リリースノートに**スキーマ変更の有無**を必ず明記する(実装計画 §8)ので、
``version.json`` にもその欄を持たせ、画面から見えるようにする。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: Releases に添付した ``version.json``。タグを打つたび CI が上書きする。
LATEST_URL = "https://github.com/yosukey/Qbank-MCQ/releases/latest/download/version.json"

#: 手で押したときだけ見に行くので、待たせない。**待つくらいなら諦める。**
DEFAULT_TIMEOUT = 3.0

_VERSION_PART = re.compile(r"(\d+)")


class UpdateCheckError(RuntimeError):
    """確認できなかった(繋がらない・壊れている)。**更新が無いことではない。**"""


@dataclass(frozen=True)
class VersionInfo:
    """``version.json`` の中身。"""

    version: str
    url: str | None = None
    notes: str | None = None
    #: このバージョンが要求するスキーマ版。上がっていれば DB が移行される。
    schema_version: int | None = None

    @classmethod
    def from_dict(cls, data: Any) -> VersionInfo:
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise UpdateCheckError("version.json の形式が違います")
        schema = data.get("schema_version")
        return cls(
            version=data["version"].strip(),
            url=data.get("url") if isinstance(data.get("url"), str) else None,
            notes=data.get("notes") if isinstance(data.get("notes"), str) else None,
            schema_version=schema if isinstance(schema, int) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "url": self.url,
            "notes": self.notes,
            "schema_version": self.schema_version,
        }


def version_key(text: str) -> tuple[int, ...]:
    """比較用の数値の並び。``'v0.10.0'`` → ``(0, 10, 0)``。

    数字だけを拾う。``0.2.0-rc1`` は ``(0, 2, 0, 1)`` になり、``0.2.0`` より新しいと
    判定される。**リリース候補を配る運用はしない**ので、この単純さで足りる。
    """
    return tuple(int(part) for part in _VERSION_PART.findall(text or ""))


def is_newer(latest: str, current: str) -> bool:
    """``latest`` が ``current`` より新しいか。読めない値は「新しくない」とする。"""
    left, right = version_key(latest), version_key(current)
    if not left or not right:
        return False
    return left > right


def fetch_version_info(
    url: str = LATEST_URL, *, timeout: float = DEFAULT_TIMEOUT, opener=urllib.request.urlopen
) -> VersionInfo:
    """``version.json`` を読む。``opener`` を差し替えればテストから叩ける。"""
    try:
        with opener(url, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise UpdateCheckError(f"更新を確認できませんでした: {exc}") from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(f"version.json を読めませんでした: {exc}") from exc
    return VersionInfo.from_dict(data)


def check_for_update(
    current: str, *, url: str = LATEST_URL, timeout: float = DEFAULT_TIMEOUT, opener=None
) -> tuple[VersionInfo | None, str]:
    """``(新しい版, 画面に出す文言)``。新しい版が無ければ第 1 要素は ``None``。

    例外は投げない。更新の確認に失敗しただけでアプリを止める理由はない。
    """
    try:
        latest = fetch_version_info(url, timeout=timeout, opener=opener or urllib.request.urlopen)
    except UpdateCheckError as exc:
        log.info("更新確認: %s", exc)
        return None, str(exc)

    if not is_newer(latest.version, current):
        return None, f"最新版を使っています(v{current})"

    message = f"新しい版があります: v{latest.version}(いま v{current})"
    if latest.schema_version is not None:
        message += f" / スキーマ版 {latest.schema_version}"
    if latest.notes:
        message += f" / {latest.notes}"
    if latest.url:
        message += f" / {latest.url}"
    return latest, message
