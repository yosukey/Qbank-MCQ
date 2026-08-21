# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec(実装計画 §8 / M7-1)。

    pyinstaller packaging/qbank-mcq.spec --noconfirm

**onedir** で固める(実装計画 §13: onefile は起動が遅く、ウイルス対策ソフトの
誤検知も多い)。出力は ``dist/Qbank-MCQ/`` で、これをそのまま Inno Setup が
``%LOCALAPPDATA%\\Programs\\Qbank-MCQ`` へ入れる。

バージョンは ``src/qbank_mcq/version.py`` から読む。リリース時はワークフローが
``tools/stamp_version.py`` でタグの値に書き換えたあとにビルドするので、exe の
プロパティに出る版番号は必ずタグと一致する。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH は PyInstaller が入れる
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from qbank_mcq.version import VERSION, numeric_version  # noqa: E402

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

APP_NAME = "Qbank-MCQ"

# 使っていない Qt モジュールを外す(実装計画 §2.2「未使用Qtモジュールをexclude」)。
# 画面は QtWidgets しか使わない。QtWebEngine と Qt3D は特に大きい。
EXCLUDES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",  # DB は SQLAlchemy + 標準ライブラリの sqlite3 で読む
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    # 開発・テスト用。配布物には要らない。
    # **matplotlib と numpy は外さない。** 問題詳細と選択肢セットの図(設計書 §14-3)
    # が使う。外すと画面を開いた瞬間に ImportError で落ちる。
    "IPython",
    "PIL.ImageQt",
    "matplotlib.backends.backend_webagg",
    "matplotlib.backends.backend_webagg_core",
    "pandas",  # 設計書 §15: 依存に挙がっているが実装では使っていない
    "pdfplumber",
    "pytest",
    "scipy",  # 設計書 §15:「scipy不要」
    "tkinter",
    "tornado",
]

# 冊子テンプレートやアイコンを置いたら一緒に入れる(実装計画 §5 の resources/)。
datas = []
resources = SRC / "qbank_mcq" / "resources"
if resources.is_dir():
    datas.append((str(resources), "qbank_mcq/resources"))

# GUI から遅延 import する画面・入出力モジュールを取りこぼさないようにする。
# SQLAlchemy の方言のように動的に import されるものも同様。
hiddenimports = collect_submodules("qbank_mcq") + ["sqlalchemy.dialects.sqlite"]

# exe のプロパティに出る版番号。Windows 以外でこの spec を試すときは黙って飛ばす。
version_resource = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    filevers = tuple(int(part) for part in numeric_version(VERSION).split("."))
    version_resource = VSVersionInfo(
        ffi=FixedFileInfo(filevers=filevers, prodvers=filevers),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "041104b0",  # 日本語 / Unicode
                        [
                            StringStruct("CompanyName", APP_NAME),
                            StringStruct("FileDescription", "試験問題バンクシステム"),
                            StringStruct("FileVersion", VERSION),
                            StringStruct("InternalName", APP_NAME),
                            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                            StringStruct("ProductName", APP_NAME),
                            StringStruct("ProductVersion", VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0411, 1200])]),
        ],
    )

icon = ROOT / "packaging" / "qbank-mcq.ico"

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],  # app.py を直接入れると相対 import が壊れる
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# **Python モジュールを excludes に挙げるだけでは、共有ライブラリとプラグインが残る。**
# PySide6 のフックが Qt を丸ごと集めてくるため、実体もここで落とす。名前の断片は
# Windows(Qt6Quick.dll)と Linux(libQt6Quick.so.6)で共通。
DROP_QT_LIBS = (
    "Qt63D",
    "Qt6Bluetooth",
    "Qt6Charts",
    "Qt6DataVisualization",
    "Qt6Designer",
    "Qt6Help",
    "Qt6Multimedia",
    "Qt6Nfc",
    "Qt6Pdf",
    "Qt6Positioning",
    "Qt6Qml",
    "Qt6Quick",
    "Qt6RemoteObjects",
    "Qt6Scxml",
    "Qt6Sensors",
    "Qt6SerialPort",
    "Qt6SpatialAudio",
    "Qt6Sql",
    "Qt6StateMachine",
    "Qt6Test",
    "Qt6TextToSpeech",
    "Qt6WebChannel",
    "Qt6WebEngine",
    "Qt6WebSockets",
)

# Qt のプラグインのうち使わないもの。
# **platforms / styles / imageformats / iconengines は残す。** 落とすと起動しない。
DROP_QT_PLUGIN_DIRS = (
    "PySide6/Qt/plugins/multimedia",
    "PySide6/Qt/plugins/position",
    "PySide6/Qt/plugins/qmltooling",
    "PySide6/Qt/plugins/renderers",
    "PySide6/Qt/plugins/sensors",
    "PySide6/Qt/plugins/sqldrivers",
    "PySide6/Qt/plugins/texttospeech",
    "PySide6/Qt/plugins/webview",
    "PySide6/Qt/qml",
)

# 翻訳ファイルは 30 MB 近くある。日本語の UI を自前で書いているので使わない。
DROP_DATA_DIRS = ("PySide6/translations", *DROP_QT_PLUGIN_DIRS)


def _keep(entry, dropped_names, dropped_dirs):
    dest = entry[0].replace("\\", "/")
    if any(name in dest for name in dropped_names):
        return False
    return not any(dest.startswith(d) or f"/{d}" in dest for d in dropped_dirs)


a.binaries = [b for b in a.binaries if _keep(b, DROP_QT_LIBS, DROP_QT_PLUGIN_DIRS)]
a.datas = [d for d in a.datas if _keep(d, (), DROP_DATA_DIRS)]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,  # UPX は誤検知を増やすので使わない(実装計画 §13)
    console=False,
    icon=str(icon) if icon.is_file() else None,
    version=version_resource,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
