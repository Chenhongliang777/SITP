# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：在 weibo-collector 目录执行
#   pyinstaller packaging/CSLSentinel.spec --noconfirm

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

PIPELINE_SCRIPTS = [
    "collector_backend.py",
    "preprocess.py",
    "analysis_chain.py",
    "report_html.py",
    "semantic_filter.py",
    "sentiment_model.py",
    "topic_cluster.py",
    "absa_extractor.py",
    "risk_scanner.py",
    "warner_score.py",
]

datas = [
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "packaging" / "USER_GUIDE.txt"), "."),
]
for name in PIPELINE_SCRIPTS:
    datas.append((str(ROOT / name), "."))

binaries = []
hiddenimports = [
    "app",
    "app.gui",
    "app.gui.main_app",
    "app.gui.log_bridge",
    "app.web.server",
    "app.web.routes",
    "app.frozen_worker",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "playwright",
    "playwright.async_api",
    "sklearn.utils._cython_blas",
    "sklearn.neighbors._typedefs",
    "sklearn.neighbors._quad_tree",
    "sklearn.tree._utils",
]

collect_pkgs = [
    "customtkinter",
    "sentence_transformers",
    "transformers",
    "tokenizers",
    "jieba",
    "sklearn",
    "fastapi",
    "starlette",
    "pydantic",
    "uvicorn",
    "playwright",
    "torch",
]

for pkg in collect_pkgs:
    try:
        tmp = collect_all(pkg)
        datas += tmp[0]
        binaries += tmp[1]
        hiddenimports += tmp[2]
    except Exception as e:
        print(f"collect_all({pkg}) warning: {e}")

hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("utils")

a = Analysis(
    [str(ROOT / "sentinel_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "notebook", "IPython", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CSLSentinel",
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
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CSLSentinel",
)
