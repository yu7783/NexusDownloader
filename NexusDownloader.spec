# -*- mode: python ; coding: utf-8 -*-
"""Nexus Downloader の PyInstaller ビルド定義。

- **onedir** で出力する（onefile は起動のたびに一時フォルダへ展開するため遅い）。
- `config.json` / `addons/` / `plugins/` は **exe の隣** に置く外部ファイルとして
  扱う（`build_exe.ps1` がコピーする）。これによりアドオンの追加・更新が可能。
- アドオンは動的 import のため PyInstaller の静的解析に引っかからない。
  そのため、アドオンが使う依存（yt-dlp / requests / bs4 / selenium）を
  明示的に同梱する。
"""

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# アドオンが外部 .py から import するパッケージ（静的解析では検出されない）
for package in ("qfluentwidgets", "requests", "yt_dlp", "bs4", "selenium"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        # インストールされていないオプション依存はスキップ
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
    "PyQt6.QtNetwork",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # アドオンは yt-dlp / requests / bs4 / selenium しか使わない。
    # 環境にインストールされている機械学習系パッケージを誤って取り込むと
    # exe が 1GB を超えて起動も遅くなるため、明示的に除外する。
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "pydoc",
        "test",
        "torch",
        "torchvision",
        "torchaudio",
        "functorch",
        "gradio",
        "gradio_client",
        "diffusers",
        "transformers",
        "accelerate",
        "peft",
        "bitsandbytes",
        "timm",
        "safetensors",
        "huggingface_hub",
        "tokenizers",
        "librosa",
        "numba",
        "llvmlite",
        "scipy",
        "sklearn",
        "pandas",
        "pyarrow",
        "cv2",
        "matplotlib",
        "altair",
        "narwhals",
        "networkx",
        "IPython",
        "jupyter",
        "notebook",
        "jedi",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NexusDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NexusDownloader",
)
