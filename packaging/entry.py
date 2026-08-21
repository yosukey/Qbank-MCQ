"""PyInstaller に渡す入口(実装計画 §8)。

``src/qbank_mcq/app.py`` を直接 spec の入口にすると、PyInstaller はそれを
``__main__`` として実行するためパッケージの外に置かれてしまい、``app.py`` の相対
import(``from .core import paths``)が失敗する。パッケージとして import させる
ためだけの薄い皮をかぶせる。
"""

from qbank_mcq.app import main

if __name__ == "__main__":
    raise SystemExit(main())
