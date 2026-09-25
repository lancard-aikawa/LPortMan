# -*- mode: python ; coding: utf-8 -*-
"""onedir ビルド定義。``uv run pyinstaller packaging/lportman.spec`` で使う。

出力は ``dist/LPortMan/`` (exe 2 本 + ``_internal/``)。台帳などのデータは
実行時に exe の隣の ``data/`` に作られる。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH は PyInstaller が注入する

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    # cli は gui を遅延 import する。取りこぼしを避けて全部入れる
    hiddenimports=collect_submodules("lportman"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "IPython"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821


# **実行ファイルは 2 本。** Windows の実行ファイルは console / GUI のどちらかでしか
# 作れない (python.exe / pythonw.exe と同じ形)。
#   lportman.exe   … console=True。CLI 用 (Claude が check / suggest の結果を読む)
#   lportmanw.exe  … console=False。ダブルクリックで画面を開く用
# 大文字小文字だけで分けないこと (Windows では同じファイル名になる)。
def _exe(name: str, console: bool):
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


coll = COLLECT(  # noqa: F821
    _exe("lportman", True),
    _exe("lportmanw", False),
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LPortMan",
)
