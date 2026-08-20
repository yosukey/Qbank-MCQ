# PyInstaller の spec(実装計画 M7-1、設計書 §15)。
#
#     PySide6 + PyInstaller(onedirモード) + Inno Setup
#     未使用Qtモジュールをspecでexcludeしexeサイズを抑制
#
# **onefile にしない。** 起動のたびに展開するので遅く、アンチウイルスの誤検知も多い
# (実装計画 §11)。配布はインストーラに任せる。
#
# 使い方(Windows):
#     pip install -r requirements.lock pyinstaller
#     pyinstaller --noconfirm --distpath dist --workpath build packaging/itembank.spec
#
# Linux でも同じ spec が通る。**構成の妥当性(収集漏れ・除外しすぎ)はそこで確かめ、
# Windows 実機では起動とインストールを確かめる**、という分担にしてある。
# ruff: noqa

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT = SPEC_DIR.parent
SRC = PROJECT / "src"

APP_NAME = "ItemBank"
ICON = SPEC_DIR / "itembank.ico"

# 使っていない Qt モジュール。PySide6 をまるごと入れると数百 MB になる。
# ここに挙げるのは「入っていても使わない」もので、削ると起動しなくなるものは挙げない
# (QtCore / QtGui / QtWidgets / QtPrintSupport / QtSvg は必要)。
EXCLUDED_QT = [
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
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

# 実行時に要らないもの。設計書 §15 は scipy も pdfplumber も不要としている。
EXCLUDED_OTHER = [
    "IPython",
    "PIL.ImageQt",
    "matplotlib.backends.backend_webagg",
    "matplotlib.backends.backend_webagg_core",
    "pandas",
    "pdfplumber",
    "pytest",
    "scipy",
    "tkinter",
    "tornado",
]

# SQLAlchemy は方言を遅延輸入する。SQLite だけは名指しで抱える。
HIDDEN = collect_submodules("sqlalchemy.dialects.sqlite")

datas = []
resources = SRC / "itembank" / "resources"
if resources.is_dir() and any(resources.iterdir()):
    # 冊子テンプレートやアイコン(設計書 §13.2)。無ければ同梱しない。
    datas.append((str(resources), "itembank/resources"))

a = Analysis(
    # entry.py 経由で入る。app.py を直接渡すと __main__ として実行され、
    # パッケージの文脈が無いまま相対 import に入って落ちる。
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_QT + EXCLUDED_OTHER,
    noarchive=False,
    optimize=0,
)

# Python モジュールを excludes に挙げても、**共有ライブラリとプラグインは残る**。
# PySide6 のフックが Qt のライブラリ一式を丸ごと集めてくるため、実体をここで落とす。
# 名前は Windows(Qt6Quick.dll)と Linux(libQt6Quick.so.6)で共通の断片を使う。
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

# Qt のプラグインディレクトリのうち、使わないもの。
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

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX はアンチウイルスの誤検知を増やす。サイズより起動の確実さを取る。
    console=False,  # GUI アプリ。コンソールを出さない(ログはファイルに出る)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
