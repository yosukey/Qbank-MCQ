"""口腔組織学 試験問題バンクシステム。

レイヤ構成(実装計画 §5):

- ``itembank.core``  … 純ロジック。``ui`` / ``io`` に依存しない
- ``itembank.io``    … docx / csv / xlsx の入出力
- ``itembank.ui``    … PySide6 の画面(未実装)
"""

__version__ = "0.1.0"
