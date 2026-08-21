"""PyInstaller に渡す入口(実装計画 §8 / M7-1)。

``src/qbank_mcq/app.py`` を直接 spec の入口にすると、PyInstaller はそれを
``__main__`` として実行するためパッケージの外に置かれてしまい、``app.py`` の相対
import(``from .core import paths``)が
``attempted relative import with no known parent package`` で落ちる。

そこで**パッケージとして import するだけの薄い皮**を置く。ここに処理を書かない。
"""

from qbank_mcq.app import main

if __name__ == "__main__":
    raise SystemExit(main())
