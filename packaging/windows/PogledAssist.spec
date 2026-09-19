from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


repo_root = Path(SPEC).resolve().parents[2]
tobii_datas, tobii_binaries, tobii_hidden_imports = collect_all("tobiiresearch")

datas = [
    (str(repo_root / "assets" / "icon.png"), "assets"),
    (str(repo_root / "gaze_mouse" / "assets" / "checkbox_x.svg"), "gaze_mouse/assets"),
    (
        str(repo_root / "gaze_mouse" / "assets" / "bosnian-model.json.gz"),
        "gaze_mouse/assets",
    ),
    (
        str(repo_root / "gaze_mouse" / "assets" / "bosnian-model.meta.json"),
        "gaze_mouse/assets",
    ),
    (str(repo_root / "gaze_mouse" / "__init__.py"), "gaze_mouse"),
    (str(repo_root / "gaze_mouse" / "tobii_stream_engine.py"), "gaze_mouse"),
    (str(repo_root / "gaze_mouse" / "tobii_stream_engine_bridge.py"), "gaze_mouse"),
]
datas += collect_data_files("qtawesome")
datas += tobii_datas

analysis = Analysis(
    [str(repo_root / "run_gaze_mouse.py")],
    pathex=[str(repo_root)],
    binaries=tobii_binaries,
    datas=datas,
    hiddenimports=["tobii_research", *tobii_hidden_imports],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PogledAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(repo_root / "assets" / "icon.png"),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PogledAssist",
)
