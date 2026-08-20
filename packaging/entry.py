"""凍結ビルドの入口(実装計画 M7-1)。

``src/itembank/app.py`` を直接 PyInstaller に渡すと、``__main__`` として実行されて
パッケージの文脈が失われ、``from .core import paths`` が
``attempted relative import with no known parent package`` で落ちる。

そこで**パッケージとして import するだけの薄い入口**を置く。ここに処理を書かない。
"""

from __future__ import annotations

import sys

from itembank.app import main

if __name__ == "__main__":
    sys.exit(main())
