"""口腔組織学 試験問題バンクシステム。

レイヤ構成(実装計画 §5):

- ``itembank.core``  … 純ロジック。``ui`` / ``io`` に依存しない
- ``itembank.io``    … docx / csv / xlsx の入出力
- ``itembank.ui``    … PySide6 の画面(M4 で本格実装。いまは起動確認用の窓のみ)
"""

from .version import VERSION

#: リリースタグから決まる(``version.py`` の説明を参照)。
__version__ = VERSION
